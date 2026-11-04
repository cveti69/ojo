"""The day's spending on Meta and ElevenLabs: past the limit no new call starts, past the stop the calls under way end."""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import pathlib
import stat
import threading
import time

import pytest
from starlette.testclient import TestClient

from app import config, ears, meta, spend, voice
from app.server import create_app
from conftest import AUTH, TOKEN, FakeEars, idle, new_session, receive_type, wav, words
from test_ears import FakeSocket, fake_connection, run
from test_ears import until as heard
from test_meta import FakeConnection, ok, stream, use_connections

WORDS = "Нажми на синюю кнопку"
WONDER, SPARK = "muse-wonder-1.0", "muse-spark-1.3"
USAGE = {"prompt_tokens": 10_000, "prompt_tokens_details": {"cached_tokens": 4_000}, "completion_tokens": 500,
         "completion_tokens_details": {"reasoning_tokens": 300}, "total_tokens": 10_500}
AT_WONDER = (6_000 * 4.00 + 4_000 * 0.20 + 500 * 20.00) / 1e6
AT_SPARK = (6_000 * 1.25 + 4_000 * 0.15 + 500 * 4.25) / 1e6
AN_HOUR_OF_SOUND = 0.18


def at(moment):
    """A clock that stands at one moment, given in UTC."""
    return lambda: datetime.datetime.fromisoformat(moment).replace(tzinfo=datetime.timezone.utc).timestamp()


def until(check, seconds=3):
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "It did not happen in time"
        time.sleep(0.005)


def kept(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source)


@pytest.mark.parametrize("model,dollars", [(WONDER, AT_WONDER), (SPARK, AT_SPARK), ("muse-new-one", AT_WONDER)])
def test_an_answer_costs_its_tokens_at_its_model_s_price(monkeypatch, model, dollars):
    use_connections(monkeypatch, FakeConnection([stream("{", "}", usage=USAGE)]))
    meta.chat_json(model, [], "low", 16)
    assert spend.ledger.dollars == pytest.approx(dollars)


def test_a_translation_is_counted_too(monkeypatch):
    use_connections(monkeypatch, FakeConnection([ok({"choices": [{"message": {"content": "Halo"}}], "usage": USAGE})]))
    meta.chat(SPARK, [], "minimal")
    assert spend.ledger.dollars == pytest.approx(AT_SPARK)


def test_an_answer_cut_off_is_counted_as_a_long_turn_and_a_refusal_as_nothing(monkeypatch):
    use_connections(monkeypatch, FakeConnection([(200, b"data: {not json\n\n")]), FakeConnection([(429, b"slow down")]))
    with pytest.raises(meta.MetaError):
        meta.chat_json(WONDER, [], "low", 16)
    assert spend.ledger.dollars == pytest.approx((20_000 * 4.00 + 2_000 * 20.00) / 1e6)
    with pytest.raises(meta.MetaError):
        meta.chat_json(WONDER, [], "low", 16)
    assert spend.ledger.dollars == pytest.approx(0.12)


def test_sound_by_file_is_counted_by_its_length(monkeypatch):
    use_connections(monkeypatch, FakeConnection([ok({"transcript": "Да", "audioDurationMs": 100})]))
    audio = wav()
    meta.transcribe(audio)
    assert spend.ledger.dollars == pytest.approx(len(audio) / 32000 * AN_HOUR_OF_SOUND / 3600)


def test_the_stream_of_sound_is_counted_with_the_silence_that_keeps_it_open(monkeypatch):
    socket = FakeSocket()
    fake_connection(monkeypatch, socket)
    monkeypatch.setattr(ears, "PHONE_GAP", 0.01)
    monkeypatch.setattr(ears, "FRAME_SECONDS", 0.001)

    async def scenario():
        hearing = ears.Ears(lambda said: None)
        hearing.hear(bytes(6400))
        socket.say({"sessionId": "live"})
        await heard(lambda: socket.sent.count(ears.SILENCE) >= 3)
        hearing.close()
    run(scenario)
    sent = sum(len(frame) for frame in socket.sent if isinstance(frame, bytes))
    assert sent >= 6400 + 3 * len(ears.SILENCE)
    assert spend.ledger.dollars == pytest.approx(sent / 32000 * AN_HOUR_OF_SOUND / 3600)


def test_the_stream_of_sound_stops_at_the_stop_and_opens_no_session_past_it(monkeypatch):
    socket, unused = FakeSocket(), FakeSocket()
    fake_connection(monkeypatch, socket, unused)
    monkeypatch.setattr(ears, "PHONE_GAP", 0.01)
    monkeypatch.setattr(ears, "FRAME_SECONDS", 0.001)
    monkeypatch.setattr(ears, "LONGEST_WAIT", 0.01)

    async def scenario():
        hearing = ears.Ears(lambda said: None)
        socket.say({"sessionId": "live"})
        await heard(lambda: socket.sent.count(ears.SILENCE) >= 3)
        spend.ledger.add(52.0)
        sent = len(socket.sent)
        await asyncio.sleep(0.05)  # Fifty frames' time with the session still open.
        assert len(socket.sent) <= sent + 1
        socket.say(None)  # Meta closes the idle session; past the stop no new one opens.
        await asyncio.sleep(0.1)
        hearing.close()
        assert unused.sent == []
    run(scenario)


def test_an_image_for_sam_is_counted(monkeypatch):
    use_connections(monkeypatch, FakeConnection([ok({"output": [{"content": [{"type": "output_text", "text": ""}]}]})]))
    meta.sam(b"jpeg", "button")
    assert spend.ledger.dollars == pytest.approx(2.50 / 1000)


def test_sound_and_images_are_counted_before_they_go_whatever_comes_back(monkeypatch):
    use_connections(monkeypatch, FakeConnection([(500, b"{}"), (500, b"{}")]))
    audio = wav()
    with pytest.raises(meta.MetaError):
        meta.transcribe(audio)
    with pytest.raises(meta.MetaError):
        meta.sam(b"jpeg", "button")
    assert spend.ledger.dollars == pytest.approx(len(audio) / 32000 * AN_HOUR_OF_SOUND / 3600 + 2.50 / 1000)


@pytest.mark.parametrize("ask", ["stream", "chat"])
def test_a_wait_that_ran_out_is_counted_as_a_long_turn_and_a_lost_connection_as_nothing(monkeypatch, ask):
    use_connections(monkeypatch, FakeConnection([TimeoutError()]), FakeConnection([ConnectionResetError()]))
    call = (lambda: meta.chat_json(WONDER, [], "low", 16)) if ask == "stream" else (lambda: meta.chat(WONDER, [], "minimal"))
    with pytest.raises(meta.MetaError):
        call()
    assert spend.ledger.dollars == pytest.approx((20_000 * 4.00 + 2_000 * 20.00) / 1e6)
    with pytest.raises(meta.MetaError):
        call()
    assert spend.ledger.dollars == pytest.approx(0.12)


def test_nothing_that_costs_goes_out_past_the_stop_even_when_asked_before_it(monkeypatch):
    connection = FakeConnection([ok({})] * 5)
    use_connections(monkeypatch, connection)
    spend.ledger.add(52.0)
    for ask in (lambda: meta.chat_json(WONDER, [], "low", 16), lambda: meta.chat(SPARK, [], "minimal"),
                lambda: meta.transcribe(wav()), lambda: meta.sam(b"jpeg", "button")):
        with pytest.raises(meta.MetaError) as refused:
            ask()
        assert refused.value.status == 503 and refused.value.reason == "spent"
    with pytest.raises(OSError):
        voice.ElevenLabsSynthesizer().stream(WORDS)
    assert connection.requests == [] and spend.ledger.dollars == 52.0


@pytest.mark.parametrize("model,their_count,characters,per_thousand", [
    ("eleven_v4_turbo", None, len(WORDS), 0.04), ("eleven_v4_turbo", "40", 40, 0.04), ("eleven_v4_turbo", "2", len(WORDS), 0.04),
    ("eleven_v4_turbo", "inf", len(WORDS), 0.04), ("eleven_v4", None, len(WORDS), 0.08), ("eleven_new_one", None, len(WORDS), 0.08)])
def test_a_voiced_line_costs_its_characters(monkeypatch, model, their_count, characters, per_thousand):
    monkeypatch.setattr(config, "ELEVENLABS_MODEL", model)
    use_connections(monkeypatch, FakeConnection([(200, b"sound", {"character-cost": their_count} if their_count else {})]))
    assert b"".join(voice.ElevenLabsSynthesizer().stream(WORDS)) == b"sound"
    assert spend.ledger.dollars == pytest.approx(characters * per_thousand / 1000)


def test_a_line_elevenlabs_refused_is_counted_all_the_same(monkeypatch):
    use_connections(monkeypatch, FakeConnection([(500, b"{}")]))
    with pytest.raises(OSError):
        voice.ElevenLabsSynthesizer().stream(WORDS)
    assert spend.ledger.dollars == pytest.approx(len(WORDS) * 0.04 / 1000)


def test_the_day_starts_at_midnight_in_bali(monkeypatch, tmp_path):
    path = str(tmp_path / "spend.json")
    monkeypatch.setattr(spend, "clock", at("2026-11-04T15:59:59"))  # A second to midnight in Bali.
    ledger = spend.Ledger(path)
    ledger.add(50.0)
    assert ledger.refuses() and ledger.day == "2026-11-04"
    monkeypatch.setattr(spend, "clock", at("2026-11-04T16:00:00"))
    assert not ledger.refuses() and ledger.dollars == 0 and ledger.day == "2026-11-05"
    assert kept(path) == {"day": "2026-11-05", "usd": 0}
    ledger.add(1.0)
    monkeypatch.setattr(spend, "clock", at("2026-11-05T00:30:00"))  # Midnight in London ends no day.
    assert not ledger.refuses() and ledger.dollars == 1.0 and ledger.day == "2026-11-05"


def test_a_clock_set_back_over_midnight_starts_no_day_again(monkeypatch):
    monkeypatch.setattr(spend, "clock", at("2026-11-04T16:00:05"))
    ledger = spend.Ledger("")
    ledger.add(53.0)
    monkeypatch.setattr(spend, "clock", at("2026-11-04T15:59:58"))
    assert ledger.stops() and ledger.dollars == 53.0 and ledger.day == "2026-11-05"


@pytest.mark.parametrize("limit", [None, 10.0])
def test_new_calls_are_refused_from_the_limit_and_those_under_way_go_on(backend, monkeypatch, limit):
    if limit:
        monkeypatch.setattr(config, "SPEND_LIMIT", limit)
    mark = limit or 50.0
    spend.ledger.add(mark - 0.01)
    session_id = new_session(backend.client)
    spend.ledger.add(0.01)
    assert backend.client.post("/sessions", headers=AUTH, json={}).status_code == 503
    assert backend.client.post("/sessions/" + session_id + "/words", headers=AUTH, json={"text": "Что это?"}).status_code == 200
    assert not backend.store.sessions[session_id].ended


def test_the_voice_goes_on_between_the_limit_and_the_stop(backend, monkeypatch):
    use_connections(monkeypatch, FakeConnection([(200, b"sound")]))
    spend.ledger.add(51.0)
    assert backend.client.post("/speech", headers=AUTH, json={"text": WORDS}).content == b"sound"


def test_the_stop_ends_every_call_under_way_and_all_that_costs(backend, caplog):
    first, second = new_session(backend.client), new_session(backend.client)
    for name in (first, second):
        words(backend.client, name)
        idle(backend, name)
        assert backend.store.sessions[name].chat  # Something said, for the end to erase.
    with backend.client.websocket_connect("/sessions/" + first + "/events", headers=AUTH) as events:
        events.receive_json()
        counted = threading.Thread(target=spend.ledger.add, args=(52.0,))  # As a model's answer is counted, in its own thread.
        counted.start()
        counted.join()
        until(lambda: all(backend.store.sessions[name].ended for name in (first, second)))
        receive_type(events, "done")
    assert all(backend.store.sessions[name].chat == [] for name in (first, second))
    assert backend.client.post("/sessions", headers=AUTH, json={}).status_code == 503
    assert backend.client.post("/speech", headers=AUTH, json={"text": WORDS}).status_code == 503
    assert backend.client.post("/translate", headers=AUTH, json={"text": WORDS, "from": "ru", "to": "id"}).status_code == 503
    assert backend.client.post("/transcribe", content=wav(), headers={**AUTH, "Content-Type": "audio/wav"}).status_code == 503
    assert "Spend: the day passed $52.00, the calls under way end." in caplog.text


def test_a_restart_keeps_the_day_and_its_sum(tmp_path):
    path = str(tmp_path / "spend.json")
    first = spend.Ledger(path)
    first.add(12.5)
    first.add(37.6)  # Past the limit: written at once.
    again = spend.Ledger(path)
    assert again.dollars == pytest.approx(50.1) and again.refuses()
    assert kept(path) == {"day": spend.day(), "usd": 50.1} and stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_the_server_writes_the_day_down_as_it_stops(monkeypatch, tmp_path):
    path = str(tmp_path / "spend.json")
    monkeypatch.setattr(config, "SHARED_TOKEN", TOKEN)
    monkeypatch.setattr(spend, "ledger", spend.Ledger(path))
    with TestClient(create_app(ears=FakeEars)):
        spend.ledger.add(1.0)
        spend.ledger.add(2.0)  # Within seconds of the last write: it waits.
        assert kept(path)["usd"] == 1.0
    assert kept(path)["usd"] == 3.0


def test_the_folder_of_the_file_is_made_for_it(tmp_path):
    path = tmp_path / "state" / "spend.json"
    spend.Ledger(str(path)).add(1.0)
    assert kept(path)["usd"] == 1.0 and stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_the_file_by_default_is_outside_the_repository():
    repository = pathlib.Path(__file__).resolve().parents[2]
    assert os.path.isabs(config.SPEND_FILE) and repository not in pathlib.Path(config.SPEND_FILE).resolve().parents


def test_a_file_that_cannot_be_written_is_said_once_and_the_count_goes_on(tmp_path, caplog, monkeypatch):
    (tmp_path / "taken").write_text("a file where the folder would be", encoding="utf-8")
    ledger = spend.Ledger(str(tmp_path / "taken" / "spend.json"))
    for _ in range(3):
        ledger.add(20.0)
        monkeypatch.setattr(ledger, "saved_at", -1e9)
    assert ledger.dollars == 60.0 and ledger.refuses() and ledger.stops()
    assert caplog.text.count("could not be written") == 1


@pytest.mark.parametrize("left", ['{"day": "2026-11-03", "usd": 49}', "not json", '{"day": "2026-11-04", "usd": NaN}',
                                  '{"day": "2026-11-04", "usd": -5}', '{"day": "2026-11-04"}', '{"day": "2026-11-04", "usd": 1' + "0" * 400 + "}",
                                  "[" * 100_000, '{"day": ["2026-11-04"], "usd": 49}'])
def test_a_file_of_another_day_or_a_broken_one_starts_the_day_at_zero(monkeypatch, tmp_path, left):
    monkeypatch.setattr(spend, "clock", at("2026-11-04T05:00:00"))
    path = tmp_path / "spend.json"
    path.write_text(left, encoding="utf-8")
    assert spend.Ledger(str(path)).dollars == 0


def test_neither_the_file_nor_the_log_holds_a_word(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    path = tmp_path / "spend.json"
    monkeypatch.setattr(spend, "ledger", spend.Ledger(str(path)))
    use_connections(monkeypatch, FakeConnection([(200, WORDS.encode(), {"character-cost": "21"})]))
    b"".join(voice.ElevenLabsSynthesizer().stream(WORDS))
    spend.ledger.add(30.0)
    spend.ledger.add(22.5)
    assert set(kept(path)) == {"day", "usd"} and WORDS not in path.read_text(encoding="utf-8")
    assert [record.getMessage() for record in caplog.records if record.name == "app.spend"] == [
        "Spend: the day passed $30.",
        "Spend: the day passed $50.00, new calls are refused.",
        "Spend: the day passed $52.00, the calls under way end.",
    ]
    assert all(WORDS not in record.getMessage() for record in caplog.records)
