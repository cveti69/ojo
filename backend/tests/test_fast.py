"""A call the person chose to be fast: other models and the phone's voice. Its screen comes as in any call."""
from __future__ import annotations

import pytest

from app import config, friend, language, meta
from conftest import AUTH, idle, line, look, new_session, post_frame, sharing, words
from test_friend import SCREENS, SHOW, blocked, events_of, kinds, pictures, spoken, wait_until
from test_not_looking import asked_with, back, last_words, lines_of, pictures_in, played, turned

RU = language.words("ru")


def changed(client, session_id, at, moved=False):
    assert client.post("/sessions/" + session_id + "/changed", headers=AUTH, json={"at": at, "moved": moved}).status_code == 200


def on_screen(client, session_id, timestamp, text):
    assert client.post("/sessions/" + session_id + "/text", headers=AUTH,
                       json={"frame_ts": timestamp, "lines": [{"text": text, "words": []}]}).status_code == 200


def seeing(backend, **changes):
    """A call whose agent has seen one screen."""
    session_id = sharing(backend.client, signals=True, **changes)
    post_frame(backend.client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    return session_id


def test_the_words_for_the_models_are_the_ones_agreed():
    assert (config.META_FAST_MODEL, config.META_FAST_REASONING) == ("muse-spark-1.3", "minimal")
    assert (config.META_FAST_SPARE_MODEL, config.META_FAST_SPARE_REASONING) == ("muse-wonder-1.0", "low")


def test_a_call_without_the_word_is_the_usual_one(backend):
    response = backend.client.post("/sessions", headers=AUTH, json={})
    session_id = response.json()["session_id"]
    assert response.json()["model"] == "test-talk" and not backend.store.sessions[session_id].fast
    words(backend.client, session_id)
    idle(backend, session_id)
    assert [(call.model, call.effort) for call in backend.meta.calls] == [("test-talk", "low")]


def test_the_usual_spare_model_keeps_the_level_of_the_call(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    entered, release = blocked(backend, only=1)
    session_id = new_session(backend.client, effort="medium")
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Поздно."), line("Запасной.")]
    try:
        words(backend.client, session_id)
        assert wait_until(lambda: spoken(seen))
        idle(backend, session_id)
    finally:
        release.set()
    assert [(call.model, call.effort) for call in backend.meta.calls] == [("test-talk", "medium"), ("test-spare", "medium")]


def test_a_usual_call_still_waits_for_the_screen_as_it_is_now(backend):
    client = backend.client
    session_id = seeing(backend)
    changed(client, session_id, 200)
    seen = events_of(backend, session_id)
    calls = len(backend.meta.calls)
    words(client, session_id)
    assert "look" in kinds(seen) and len(backend.meta.calls) == calls
    assert look(client, session_id, [SCREENS[1]], first_taken=300).status_code == 200
    idle(backend, session_id)
    assert len(backend.meta.calls) == calls + 1


def test_a_fast_call_takes_the_quick_model_with_the_least_thought(backend):
    response = backend.client.post("/sessions", headers=AUTH, json={"fast": True})
    session_id = response.json()["session_id"]
    assert response.json()["model"] == config.META_FAST_MODEL
    words(backend.client, session_id)
    idle(backend, session_id)
    assert [(call.model, call.effort) for call in backend.meta.calls] == [("muse-spark-1.3", "minimal")]


def test_a_chosen_level_does_not_slow_a_fast_call(backend):
    session_id = new_session(backend.client, fast=True, effort="high")
    words(backend.client, session_id)
    idle(backend, session_id)
    assert [call.effort for call in backend.meta.calls] == ["minimal"]


def test_the_spare_model_of_a_fast_call_comes_after_the_same_wait(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    entered, release = blocked(backend, only=1)
    session_id = new_session(backend.client, fast=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Поздно."), line("Запасной.")]
    try:
        words(backend.client, session_id)
        assert wait_until(lambda: spoken(seen))
        idle(backend, session_id)
    finally:
        release.set()
    assert [(call.model, call.effort) for call in backend.meta.calls] == [("muse-spark-1.3", "minimal"), ("muse-wonder-1.0", "low")]
    assert spoken(seen) == ["Запасной."]


def test_the_seconds_of_the_spare_model_did_not_change():
    assert friend.SPARE_SECONDS == 8


def test_the_message_that_has_just_come_is_what_the_model_reads(backend):
    """The whole point of the fast call: the frame from after their words, and not the last one that came before them."""
    client = backend.client
    session_id = seeing(backend, fast=True)
    changed(client, session_id, 200)
    seen = events_of(backend, session_id)
    calls = len(backend.meta.calls)
    words(client, session_id, "Что он написал?")
    assert "look" in kinds(seen) and len(backend.meta.calls) == calls  # No model until the fresh frame is here.
    assert look(client, session_id, [SCREENS[1]], first_taken=300).status_code == 200
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    assert session.last_frame.timestamp_ms == 300 and len(backend.meta.calls) == calls + 1
    assert pictures(asked_with(backend, -1)[-1]) == [meta.data_url(session.last_frame.jpeg)]


def test_a_fresh_look_at_nearly_the_same_screen_goes_too(backend):
    """A new line in a chat is a small change, and the model must still get the frame that has it."""
    client = backend.client
    session_id = seeing(backend, fast=True)
    first = backend.store.sessions[session_id].last_frame
    changed(client, session_id, 200)
    words(client, session_id, "Что он написал?")
    assert look(client, session_id, [SCREENS[0][:-1] + b"\xd9"], first_taken=300).status_code == 200
    idle(backend, session_id)
    assert backend.store.sessions[session_id].chat[-2]["frame"].timestamp_ms == 300 and first.timestamp_ms == 100


def test_a_new_small_frame_is_the_screen_as_it_is_now(backend):
    client = backend.client
    session_id = seeing(backend, fast=True)
    changed(client, session_id, 200)
    assert post_frame(client, session_id, SCREENS[1], 300, small=True).status_code == 202
    seen = events_of(backend, session_id)
    words(client, session_id, "Что он написал?")
    idle(backend, session_id)
    assert "look" not in kinds(seen) and backend.store.sessions[session_id].chat[-2]["frame"].timestamp_ms == 300


@pytest.mark.parametrize("fast", [False, True])
def test_the_wait_for_the_screen_is_the_same_in_both_calls(backend, monkeypatch, fast):
    monkeypatch.setattr(friend, "LOOK_SECONDS", 0.05)
    client = backend.client
    session_id = seeing(backend, fast=fast)
    changed(client, session_id, 200)
    seen = events_of(backend, session_id)
    words(client, session_id)
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    assert kinds(seen)[:2] == ["heard", "look"]


@pytest.mark.parametrize("fast", [False, True])
def test_with_no_frame_yet_there_is_only_the_look(backend, fast):
    client = backend.client
    session_id = sharing(client, fast=fast, signals=True)
    seen = events_of(backend, session_id)
    words(client, session_id)
    assert "look" in kinds(seen) and not backend.meta.calls


@pytest.mark.parametrize("fast", [False, True])
def test_a_still_payment_screen_goes_as_in_any_call(backend, fast):
    """A chat with the word "bayar" in it has to stay readable, so a fast call has no rule of its own here."""
    client = backend.client
    session_id = sharing(client, signals=True, fast=fast)
    backend.meta.replies = [line("Проверь сумму.", private=True), line("Хорошо.")]
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    words(client, session_id)
    idle(backend, session_id)
    assert pictures_in(asked_with(backend, -1)) == 1


@pytest.mark.parametrize("fast", [False, True])
def test_the_words_of_a_payment_on_a_screen_take_no_picture_from_the_model(backend, fast):
    client = backend.client
    session_id = sharing(client, signals=True, fast=fast)
    post_frame(client, session_id, SCREENS[0], 100)
    on_screen(client, session_id, 100, "Bayar sekarang")
    idle(backend, session_id)
    words(client, session_id, "Что он написал?")
    idle(backend, session_id)
    assert pictures_in(asked_with(backend, -1)) == 1


def test_the_answer_works_as_it_does_in_a_usual_call(backend):
    client = backend.client
    session_id = seeing(backend, fast=True)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Нажми сюда.", paste="готовый текст", show=SHOW)]
    changed(client, session_id, 200)
    words(client, session_id)
    assert look(client, session_id, [SCREENS[0]], first_taken=300).status_code == 200
    idle(backend, session_id)
    assert spoken(seen) == ["Нажми сюда.", ""]
    assert next(event for event in seen if event["type"] == "say" and event["paste_text"])["paste_text"] == "готовый текст"
    assert session.texts == ["готовый текст"] and "hint" in kinds(seen)


def test_the_log_names_the_mode_and_the_models(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    new_session(backend.client, fast=True)
    new_session(backend.client)
    rows = [row for row in lines_of(tmp_path / "calls") if row["kind"] == "call"]
    assert sorted((row["fast"], row["model"], row["effort"], row["spare"], row["spare_effort"]) for row in rows) == [
        (False, "test-talk", "low", "test-spare", "low"), (True, "muse-spark-1.3", "minimal", "muse-wonder-1.0", "low")]
