from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.request
from email import policy
from email.parser import BytesParser

import pytest

from app import config, meta
from app.server import _TokenFilter
from conftest import wav


def test_asr_multipart_and_authorization(monkeypatch):
    monkeypatch.setattr(config, "META_API_KEY", "dummy-api-key")
    monkeypatch.setattr(config, "META_API_BASE", "https://meta.invalid/v1")
    monkeypatch.setattr(config, "META_TRANSCRIBE_MODEL", "test-voice-model")
    audio = wav()
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        return io.BytesIO(json.dumps({
            "transcript": "  Напиши водителю  ", "audioDurationMs": 1234,
            "turns": [{"transcript": "Напиши водителю", "startMs": 0, "endMs": 1234}]
        }, ensure_ascii=False).encode())
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    result = meta.transcribe(audio, language_bias="ru", keywords=["водитель"])
    assert result["text"] == "Напиши водителю" and result["audio_duration_ms"] == 1234
    assert len(result["turns"]) == 1
    request = requests[0]
    assert request.full_url == "https://meta.invalid/v1/asr/transcribe"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer dummy-api-key"
    content_type = request.get_header("Content-type")
    message = BytesParser(policy=policy.default).parsebytes(
        ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + request.data
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


def test_meta_errors_do_not_include_upstream_body(monkeypatch):
    monkeypatch.setattr(config, "META_API_KEY", "dummy")

    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "failure", {}, io.BytesIO(b"secret-upstream-body"))
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
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
