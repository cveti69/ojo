from __future__ import annotations

import json
import logging
import ssl
from email import policy
from email.parser import BytesParser

import pytest

from app import config, meta, pool, voice
from app.server import _AddressFilter, _TokenFilter
from conftest import wav


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def read(self):
        return self.body

    def read1(self, size):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def readline(self):
        line, mark, self.body = self.body.partition(b"\n")
        return line + mark


class FakeConnection:
    """Each reply is a status with a body, or an error to raise."""

    def __init__(self, replies, close_after=False):
        self.replies = replies
        self.close_after = close_after
        self.sock = self
        self.requests = []

    def settimeout(self, timeout):
        self.timeout = timeout

    def request(self, method, path, data, headers):
        self.requests.append({"method": method, "path": path, "data": data, "headers": headers})

    def getresponse(self):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if self.close_after:
            self.close()
        return FakeResponse(*reply)

    def close(self):
        self.sock = None


def use_connections(monkeypatch, *connections):
    left = list(connections)
    monkeypatch.setattr(config, "META_API_KEY", "dummy-api-key")
    monkeypatch.setattr(config, "META_API_BASE", "https://meta.invalid/v1")
    monkeypatch.setattr(pool, "connect", lambda host, timeout: left.pop(0))
    return left


def ok(value):
    return 200, json.dumps(value, ensure_ascii=False).encode()


def test_asr_multipart_and_authorization(monkeypatch):
    monkeypatch.setattr(config, "META_TRANSCRIBE_MODEL", "test-voice-model")
    audio = wav()
    connection = FakeConnection([ok({
        "transcript": "  Напиши водителю  ", "audioDurationMs": 1234,
        "turns": [{"transcript": "Напиши водителю", "startMs": 0, "endMs": 1234}]
    })])
    use_connections(monkeypatch, connection)
    result = meta.transcribe(audio, language_bias="ru", keywords=["водитель"])
    assert result["text"] == "Напиши водителю" and result["audio_duration_ms"] == 1234
    assert len(result["turns"]) == 1
    request = connection.requests[0]
    assert request["path"] == "/v1/asr/transcribe"
    assert request["method"] == "POST"
    assert request["headers"]["Authorization"] == "Bearer dummy-api-key"
    content_type = request["headers"]["Content-Type"]
    message = BytesParser(policy=policy.default).parsebytes(
        ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + request["data"]
    )
    parts = list(message.iter_parts())
    assert len(parts) == 2
    assert [part.get_param("name", header="content-disposition") for part in parts] == ["request", "audio"]
    assert parts[0].get_content_type() == "application/json"
    assert json.loads(parts[0].get_payload(decode=True)) == {
        "model": "test-voice-model", "audioEncoding": "WAV", "mode": "PUSH_TO_TALK",
        "languageBias": "ru", "keywords": ["водитель"]
    }
    assert parts[1].get_content_type() == "audio/wav"
    assert parts[1].get_payload(decode=True) == audio


@pytest.mark.parametrize("reply,text", [
    ({"transcript": "", "turns": [{"text": "Не использовать"}], "audioDurationMs": 10}, ""),
    ({"transcript": None}, ""),
    ({"data": {"transcript": "  Текст  ", "audioDurationMs": 10}}, "Текст"),
    ({"result": {"transcript": "Ещё текст", "turns": []}}, "Ещё текст"),
    ({"turns": [{"transcript": "Привет"}, {"text": "водитель"}]}, "Привет водитель"),
    ({"text": "Старый формат"}, "Старый формат"),
])
def test_transcription_response_shapes(reply, text):
    assert meta.parse_transcription(reply)["text"] == text


@pytest.mark.parametrize("reply", [{}, {"transcript": ["bad"]}, {"error": "failure"}])
def test_invalid_asr_response(reply):
    with pytest.raises(meta.MetaError):
        meta.parse_transcription(reply)


def stream(*pieces, usage=None):
    """An answer as Meta sends it, a piece at a time."""
    events = [{"choices": [{"delta": {"content": piece}}]} for piece in pieces]
    events.append({"choices": [], "usage": usage or {}})
    return 200, b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events) + b"data: [DONE]\n\n"


def test_a_turn_of_the_call_asks_for_json_without_a_schema(monkeypatch):
    connection = FakeConnection([stream("{", "}", usage={"completion_tokens": 7})])
    use_connections(monkeypatch, connection)
    reply = meta.chat_json("test-model", [{"role": "user", "content": "Привет"}], "medium", 16)
    assert reply["text"] == "{}" and reply["usage"] == {"completion_tokens": 7}
    sent = json.loads(connection.requests[0]["data"])
    assert sent["response_format"] == {"type": "json_object"} and sent["reasoning_effort"] == "medium"
    assert sent["max_completion_tokens"] == meta.ANSWER_TOKENS and sent["stream"] is True


def test_the_answer_is_shown_as_it_comes_and_the_connection_stays(monkeypatch):
    connection = FakeConnection([stream('{"say"', ': "Да"', "}")])
    use_connections(monkeypatch, connection)
    seen = []
    assert meta.chat_json("test-model", [], "medium", 16, seen.append)["text"] == '{"say": "Да"}'
    assert seen == ['{"say"', '{"say": "Да"', '{"say": "Да"}'] and meta.connections.kept == [connection]


def test_a_broken_stream_is_an_error_and_keeps_no_connection(monkeypatch):
    use_connections(monkeypatch, FakeConnection([(200, b"data: {not json\n\n")]))
    with pytest.raises(meta.MetaError) as error:
        meta.chat_json("test-model", [], "medium", 16)
    assert error.value.status == 502 and error.value.reason == "broken stream" and meta.connections.kept == []


def test_a_refused_answer_gives_its_status(monkeypatch):
    use_connections(monkeypatch, FakeConnection([(429, b"slow down")]))
    with pytest.raises(meta.MetaError) as error:
        meta.chat_json("test-model", [], "medium", 16)
    assert error.value.status == 429


def test_a_refusal_gives_its_type_and_code_and_not_its_message(monkeypatch):
    body = {"error": {"message": "слова из запроса", "type": "rate_limit_error", "code": "too_many_requests"}}
    use_connections(monkeypatch, FakeConnection([(429, json.dumps(body).encode())]))
    with pytest.raises(meta.MetaError) as error:
        meta.chat_json("test-model", [], "medium", 16)
    assert error.value.status == 429 and error.value.reason == "rate_limit_error too_many_requests"
    assert "слова" not in str(error.value)


@pytest.mark.parametrize("body", [b"slow down", b"\xff", b"[]", b"{}", b'{"error": "too many"}', b'{"error": {"message": "x"}}'])
def test_a_refusal_that_does_not_name_itself_has_no_reason(monkeypatch, body):
    use_connections(monkeypatch, FakeConnection([(500, body)]))
    with pytest.raises(meta.MetaError) as error:
        meta.chat_json("test-model", [], "medium", 16)
    assert error.value.status == 500 and error.value.reason is None


def test_a_reason_stays_a_short_name_whatever_meta_sends(monkeypatch):
    body = {"error": {"type": "слова из запроса", "code": "a b:c" + "d" * 99}}
    use_connections(monkeypatch, FakeConnection([(400, json.dumps(body).encode())]))
    with pytest.raises(meta.MetaError) as error:
        meta.chat_json("test-model", [], "medium", 16)
    assert error.value.reason == "abc" + "d" * 37


def test_a_lost_connection_is_named(monkeypatch):
    use_connections(monkeypatch, FakeConnection([ConnectionResetError()]))
    with pytest.raises(meta.MetaError) as error:
        meta.chat_json("test-model", [], "medium", 16)
    assert error.value.status == 502 and error.value.reason == "ConnectionResetError"


def test_a_connection_is_used_again(monkeypatch):
    connection = FakeConnection([ok({"first": 1}), ok({"second": 2})])
    use_connections(monkeypatch, connection)
    assert meta._request("GET", "/models") == {"first": 1}
    assert meta._request("GET", "/models") == {"second": 2}
    assert len(connection.requests) == 2 and connection.timeout == 60


@pytest.mark.parametrize("closed", [ConnectionResetError(), ssl.SSLEOFError()])
def test_a_connection_closed_by_meta_gives_way_to_a_new_one(monkeypatch, closed):
    stale = FakeConnection([ok({}), closed])
    fresh = FakeConnection([ok({"answer": 1})])
    use_connections(monkeypatch, stale, fresh)
    meta._request("POST", "/chat/completions", {})
    assert meta._request("POST", "/chat/completions", {}) == {"answer": 1}
    assert stale.sock is None and len(fresh.requests) == 1


def test_a_new_connection_that_fails_reports_it(monkeypatch):
    use_connections(monkeypatch, FakeConnection([ConnectionResetError()]))
    with pytest.raises(meta.MetaError) as error:
        meta._request("GET", "/models")
    assert error.value.status == 502


@pytest.mark.parametrize("asked,used", [("chosen", "chosen"), ("unknown", "standard"), (None, "standard")])
def test_only_a_listed_voice_is_used(monkeypatch, asked, used):
    connection = FakeConnection([(200, b"sound")])
    monkeypatch.setattr(pool, "connect", lambda host, timeout: connection)
    monkeypatch.setattr(config, "ELEVENLABS_VOICE", "standard")
    monkeypatch.setattr(config, "VOICES", {"standard": "George", "chosen": "Done"})
    assert b"".join(voice.ElevenLabsSynthesizer().stream("Да", asked)) == b"sound"
    assert "/text-to-speech/" + used + "/stream" in connection.requests[0]["path"]


def test_a_timeout_is_not_tried_again(monkeypatch):
    slow = FakeConnection([ok({}), TimeoutError()])
    left = use_connections(monkeypatch, slow, FakeConnection([ok({"late": 1})]))
    meta._request("GET", "/models")
    with pytest.raises(meta.MetaError):
        meta._request("GET", "/models")
    assert len(left) == 1 and slow.sock is None


def test_a_closing_answer_keeps_no_connection(monkeypatch):
    use_connections(monkeypatch, FakeConnection([ok({})], close_after=True))
    meta._request("GET", "/models")
    assert meta.connections.kept == []


def test_a_spoken_line_read_to_its_end_keeps_the_connection(monkeypatch):
    connection = FakeConnection([(200, b"sound"), (200, b"more")])
    use_connections(monkeypatch, connection)
    synthesizer = voice.ElevenLabsSynthesizer()
    assert b"".join(synthesizer.stream("Да")) == b"sound"
    assert b"".join(synthesizer.stream("Нет")) == b"more"
    assert "output_format=pcm_24000" in connection.requests[0]["path"] and len(connection.requests) == 2


def test_a_line_the_phone_left_halfway_closes_the_connection(monkeypatch):
    connection = FakeConnection([(200, bytes(voice.SPEECH_CHUNK * 2))])
    use_connections(monkeypatch, connection)
    chunks = voice.ElevenLabsSynthesizer().stream("Да")
    next(chunks)
    chunks.close()
    assert connection.sock is None and voice.connections.kept == []


def test_a_refused_line_is_an_error_before_any_sound(monkeypatch):
    use_connections(monkeypatch, FakeConnection([(401, b"secret")]))
    with pytest.raises(OSError):
        voice.ElevenLabsSynthesizer().stream("Да")


def test_meta_errors_do_not_include_upstream_body(monkeypatch):
    use_connections(monkeypatch, FakeConnection([(403, b"secret-upstream-body")]))
    with pytest.raises(meta.MetaError) as error:
        meta.transcribe(wav())
    assert error.value.status == 403
    assert "secret-upstream-body" not in str(error.value)


@pytest.mark.parametrize("parameter", ["token", "%74oken"])
def test_debug_token_is_redacted_in_access_log(parameter):
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %s', (
        "127.0.0.1", "GET", "/debug/s_test?" + parameter + "=secret-value&other=1", "1.1", 200
    ), None)
    assert _TokenFilter().filter(record)
    assert "secret-value" not in record.getMessage()
    assert "other=1" in record.getMessage()


@pytest.mark.parametrize("logger, message, args", [
    ("uvicorn.access", '%s - "%s %s HTTP/%s" %d', ("203.0.113.9:51234", "POST", "/sessions/s_test/frames", "1.1", 202)),
    ("uvicorn.error", '%s - "WebSocket %s" [accepted]', ("203.0.113.9:51234", "/sessions/s_test/events")),
    ("uvicorn.error", '%s - "WebSocket %s" 403', ("203.0.113.9:51234", "/sessions/s_test/listen")),
])
def test_no_line_keeps_the_phone_address(logger, message, args):
    """The lines a call leaves say what was asked for, never who asked: the address is gone before the line is written."""
    record = logging.LogRecord(logger, logging.INFO, "", 0, message, args, None)
    for item in logging.getLogger(logger).filters:
        item.filter(record)
    line = record.getMessage()
    assert "203.0.113.9" not in line and line.startswith('- - "') and "/sessions/s_test/" in line
    assert any(isinstance(item, _AddressFilter) for item in logging.getLogger(logger).filters)


def test_other_lines_keep_their_words():
    record = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, "Started server process [%d]", (4321,), None)
    assert _AddressFilter().filter(record) and record.getMessage() == "Started server process [4321]"
