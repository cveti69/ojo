from __future__ import annotations

import base64
import io
import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import config, friend, grounding, hint, language, meta, server, sessions
from conftest import AUTH, idle, jpeg, line, look, new_session, post_frame, real_locate, say, sharing, words

RU = language.words("ru")
SHOW = {"label": "Beli Langsung", "object": "green button", "box": [600, 900, 950, 960]}
SCREENS = [jpeg((value, value, value)) for value in (20, 90, 160, 230)]


def covered(box, color=(20, 40, 60)):
    image = Image.open(io.BytesIO(jpeg())).convert("RGB")
    ImageDraw.Draw(image).rectangle(box, fill=color)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def with_keyboard():
    return covered((0, 520, 390, 844))


def with_video():
    return covered((0, 350, 390, 500))


def wait_until(condition, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def events_of(backend, session_id):
    seen = []
    session = backend.store.sessions[session_id]
    publish = session.publish
    session.publish = lambda event: (seen.append(event), publish(event))[1]
    return seen


def kinds(seen):
    return [event["type"] for event in seen]


def spoken(seen):
    return [event["text_ru"] for event in seen if event["type"] == "say"]


def texts(message):
    return [part["text"] for part in message["content"] if part["type"] == "text"]


def pictures(message):
    return [part["image_url"]["url"] for part in message["content"] if part["type"] == "image_url"]


def roles(call):
    return [message["role"] for message in call.messages]


def blocked(backend, only=None):
    """The model does not answer until released, on every call or on one."""
    entered, release = threading.Event(), threading.Event()

    def block(index):
        if only is None or index == only:
            entered.set()
            assert release.wait(5)
    backend.meta.hook = block
    return entered, release


def played(backend, session_id, turn):
    assert backend.client.post("/sessions/" + session_id + "/played", headers=AUTH, json={"turn": turn}).status_code == 200


def test_the_rules_stay_general():
    assert not set(("Tokopedia", "Gojek", "Grab", "шампунь", "такси", "Например", "например")) & set(
        (friend.ROLE + friend.RULES).replace(",", " ").replace(".", " ").split())


def test_words_with_no_screen_go_straight_to_the_model(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    words(backend.client, session_id, "Какие взять?")
    idle(backend, session_id)
    message = backend.meta.calls[0].messages[-1]
    assert texts(message) == ["Какие взять? " + RU["unseen"]] and not pictures(message)
    assert kinds(seen) == ["heard", "say"] and seen[1]["turn"] == 1 and seen[1]["text_ru"] == "Это кнопка поиска."


def test_words_on_a_shared_screen_wait_for_the_screen_as_it_is_now(backend):
    client = backend.client
    session_id = sharing(client)
    post_frame(client, session_id)
    idle(backend, session_id)
    seen = events_of(backend, session_id)
    words(client, session_id, "Это что?")
    assert kinds(seen) == ["heard", "look"] and len(backend.meta.calls) == 1
    assert post_frame(client, session_id, SCREENS[0], timestamp=124).json() == {"accepted": False, "reason": "looking"}
    assert look(client, session_id, [SCREENS[1], SCREENS[2]]).json() == {"ok": True}
    idle(backend, session_id)
    message = backend.meta.calls[1].messages[-1]
    assert texts(message) == ["Это что?"] and pictures(message) == [meta.data_url(SCREENS[2])]


def test_a_phone_that_sends_no_screen_does_not_hold_the_answer(backend, monkeypatch):
    monkeypatch.setattr(friend, "LOOK_SECONDS", 0.01)
    client = backend.client
    session_id = sharing(client)
    post_frame(client, session_id)
    idle(backend, session_id)
    words(client, session_id, "Это что?")
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    assert texts(backend.meta.calls[1].messages[-1]) == ["Это что?"] and pictures(backend.meta.calls[1].messages[-1])
    backend.meta.calls.clear()
    other = sharing(client)
    words(client, other, "Это что?")
    assert wait_until(lambda: len(backend.meta.calls) == 1)
    assert texts(backend.meta.calls[0].messages[-1]) == ["Это что? " + RU["unseen"]]


def test_a_new_line_cancels_the_wait_for_an_old_screen(backend, monkeypatch):
    monkeypatch.setattr(friend, "LOOK_SECONDS", 0.05)
    client = backend.client
    session_id = sharing(client)
    words(client, session_id, "Посмотри")
    words(client, session_id, "цену")
    assert look(client, session_id, [jpeg()]).status_code == 200
    idle(backend, session_id)
    time.sleep(0.1)
    assert len(backend.meta.calls) == 1 and texts(backend.meta.calls[0].messages[-1]) == ["Посмотри цену"]


def test_the_voice_comes_first_and_the_window_after_it(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Жми зелёную кнопку.", show=SHOW)]
    assert post_frame(client, session_id).json() == {"accepted": True}
    idle(backend, session_id)
    assert kinds(seen) == ["say", "hint"]
    assert seen[1]["instruction_ru"] == "Жми зелёную кнопку." and seen[1]["step_id"] == "s1"
    assert seen[1]["timings"]["box_source"] == "test" and seen[1]["image_jpeg_base64"]


def test_the_picture_that_goes_out_has_the_outline_on_it(backend):
    session_id = sharing(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Жми.", show=SHOW)]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    image = Image.open(io.BytesIO(base64.b64decode(seen[1]["image_jpeg_base64"]))).convert("RGB")
    assert sum(1 for red, green, blue in image.getdata() if red > 190 and green > 160 and blue < 80) > 150


def test_silence_with_an_outline_uses_the_label(backend):
    session_id = sharing(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("", show=SHOW)]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert kinds(seen) == ["quiet", "hint"] and seen[1]["instruction_ru"] == SHOW["label"]


def test_no_outline_without_a_screen(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Вот тут.", show=SHOW)]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert kinds(seen) == ["heard", "say"]


def test_a_ready_text_with_no_voice_closes_the_line(backend):
    session_id = new_session(backend.client)
    backend.meta.replies = [line("", paste="sampo"), line()]
    words(backend.client, session_id, "Один")
    idle(backend, session_id)
    words(backend.client, session_id, "Два")
    idle(backend, session_id)
    assert texts(backend.meta.calls[1].messages[-1]) == ["Два " + RU["unseen"]]


def test_silence_closes_the_line(backend):
    client = backend.client
    session_id = new_session(client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line(""), line("Да.")]
    words(client, session_id, "Угу")
    idle(backend, session_id)
    assert kinds(seen) == ["heard", "quiet"]
    words(client, session_id, "Слышишь?")
    idle(backend, session_id)
    assert roles(backend.meta.calls[1]) == ["system", "user", "assistant", "user"]


@pytest.mark.parametrize("entry,pasted", [("clipboard", "sampo"), ("keyboard", None)])
def test_a_ready_text_goes_to_the_clipboard_or_the_keyboard(backend, entry, pasted):
    client = backend.client
    session_id = new_session(client, text_entry=entry)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Вставь в поиск.", paste="sampo")]
    words(client, session_id)
    idle(backend, session_id)
    assert spoken(seen)[0] == "Вставь в поиск." and [event["paste_text"] for event in seen if event["type"] == "say"][-1] == pasted
    assert client.get("/sessions/" + session_id + "/suggestions", headers=AUTH).json() == {
        "step_id": "s1", "texts": ["sampo"], "hint_ru": ""
    }


@pytest.mark.parametrize("entry,kind", [("clipboard", "say"), ("keyboard", "quiet")])
def test_a_ready_text_with_no_words(backend, entry, kind):
    session_id = new_session(backend.client, text_entry=entry)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("", paste="sampo")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert seen[-1]["type"] == kind


def test_a_new_screen_is_a_turn_and_a_video_on_the_page_is_not(backend):
    client = backend.client
    session_id = sharing(client)
    assert post_frame(client, session_id, timestamp=1).json() == {"accepted": True}
    idle(backend, session_id)
    message = backend.meta.calls[0].messages[-1]
    assert texts(message) == [RU["silent"]] and len(pictures(message)) == 1
    assert post_frame(client, session_id, timestamp=2).json() == {"accepted": False, "reason": "same"}
    assert post_frame(client, session_id, with_video(), timestamp=3).json() == {"accepted": False, "reason": "same"}
    assert post_frame(client, session_id, with_keyboard(), timestamp=4).json() == {"accepted": True}
    idle(backend, session_id)
    assert len(backend.meta.calls) == 2


def test_a_small_change_waits_for_the_next_turn(backend):
    client = backend.client
    session_id = sharing(client)
    post_frame(client, session_id, timestamp=1)
    idle(backend, session_id)
    assert post_frame(client, session_id, SCREENS[0], timestamp=2, small=True).json() == {"accepted": False, "reason": "small"}
    assert len(backend.meta.calls) == 1
    assert backend.store.sessions[session_id].last_frame.timestamp_ms == 2


def test_a_broadcast_started_again_shows_the_screen_anew(backend):
    client = backend.client
    session_id = sharing(client)
    prefix = "/sessions/" + session_id + "/broadcast"
    post_frame(client, session_id)
    idle(backend, session_id)
    client.post(prefix, headers=AUTH, json={"state": "finished"})
    client.post(prefix, headers=AUTH, json={"state": "started"})
    assert post_frame(client, session_id).json() == {"accepted": True}
    idle(backend, session_id)


def test_after_the_broadcast_the_model_is_told_there_is_no_screen(backend):
    client = backend.client
    session_id = sharing(client)
    client.post("/sessions/" + session_id + "/broadcast", headers=AUTH, json={"state": "finished"})
    words(client, session_id, "Видишь?")
    idle(backend, session_id)
    assert texts(backend.meta.calls[0].messages[-1]) == ["Видишь? " + RU["unseen"]]


def test_a_new_screen_replaces_the_answer_for_the_old_one(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Старое."), line("Новое.")]
    try:
        post_frame(client, session_id, SCREENS[0])
        assert entered.wait(2)
        assert post_frame(client, session_id, SCREENS[1], timestamp=124).json() == {"accepted": True}
        assert wait_until(lambda: len(backend.meta.calls) == 2)
        idle(backend, session_id)
    finally:
        release.set()
    assert spoken(seen) == ["Новое."]
    message = backend.meta.calls[1].messages[-1]
    assert texts(message) == [RU["silent"], RU["silent"]] and len(pictures(message)) == 2


def test_a_screen_that_never_settles_still_gets_an_answer(backend):
    entered, release = blocked(backend)
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    try:
        for index, screen in enumerate(SCREENS[:3]):
            assert post_frame(client, session_id, screen, timestamp=index).json() == {"accepted": True}
        assert post_frame(client, session_id, SCREENS[3], timestamp=3).json() == {"accepted": False, "reason": "busy"}
    finally:
        release.set()
    idle(backend, session_id)
    assert wait_until(lambda: len(spoken(seen)) == 2) and backend.store.sessions[session_id].restarts == 0


def test_the_screen_that_had_to_wait_gets_its_turn_after_the_answer(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client)
    session = backend.store.sessions[session_id]
    session.restarts = friend.MAX_RESTARTS
    post_frame(client, session_id, SCREENS[0], timestamp=1)
    assert entered.wait(2)
    assert post_frame(client, session_id, SCREENS[1], timestamp=2).json() == {"accepted": False, "reason": "busy"}
    release.set()
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    assert session.chat[-2]["text"] == RU["silent"] and session.chat[-2]["frame"].timestamp_ms == 2 and not session.held


def test_a_small_change_does_not_take_the_turn_of_the_screen_that_waited(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client)
    session = backend.store.sessions[session_id]
    session.restarts = friend.MAX_RESTARTS
    post_frame(client, session_id, SCREENS[0], timestamp=1)
    assert entered.wait(2)
    assert post_frame(client, session_id, SCREENS[1], timestamp=2).json() == {"accepted": False, "reason": "busy"}
    assert post_frame(client, session_id, SCREENS[1], timestamp=3, small=True).json() == {"accepted": False, "reason": "small"}
    release.set()
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    assert session.chat[-2]["text"] == RU["silent"] and session.chat[-2]["frame"].timestamp_ms == 3


def test_words_under_way_take_the_place_of_the_screen_that_waited(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client)
    session = backend.store.sessions[session_id]
    session.restarts = friend.MAX_RESTARTS
    post_frame(client, session_id, SCREENS[0], timestamp=1)
    assert entered.wait(2)
    assert post_frame(client, session_id, SCREENS[1], timestamp=2).json()["reason"] == "busy"
    words(client, session_id, "Что это?")
    release.set()
    assert look(client, session_id, [SCREENS[1]]).status_code == 200
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    assert len(backend.meta.calls) == 2


def test_the_same_phrase_by_its_second_road_is_one_turn(backend):
    session_id = new_session(backend.client)
    entered, release = blocked(backend, only=1)
    words(backend.client, session_id, "Куда нажать?")
    assert entered.wait(2)
    words(backend.client, session_id, "куда нажать")
    release.set()
    idle(backend, session_id)
    assert len(backend.meta.calls) == 1 and backend.store.sessions[session_id].turn == 1


def test_an_outline_on_a_fresh_look_at_another_screen_goes_out(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    post_frame(client, session_id, SCREENS[0], timestamp=100)
    idle(backend, session_id)
    backend.meta.replies = [line("Жми.", show=SHOW)]
    words(client, session_id)
    look(client, session_id, [SCREENS[1]], first_taken=200)
    idle(backend, session_id)
    assert kinds(seen)[-1] == "hint" and "left" not in kinds(seen)
    assert backend.store.sessions[session_id].last_frame.timestamp_ms == 200


def test_a_screen_after_the_look_still_takes_the_outline_away(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    session = backend.store.sessions[session_id]
    post_frame(client, session_id, SCREENS[0], timestamp=100)
    idle(backend, session_id)
    session.restarts = friend.MAX_RESTARTS
    backend.meta.replies = [line("Жми.", show=SHOW)]
    backend.meta.hook = lambda index: post_frame(client, session_id, SCREENS[2], timestamp=300)
    words(client, session_id)
    look(client, session_id, [SCREENS[1]], first_taken=200)
    idle(backend, session_id)
    assert "hint" not in kinds(seen) and session.last_frame.timestamp_ms == 300


def test_a_look_older_than_the_last_frame_does_not_replace_it(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    session = backend.store.sessions[session_id]
    post_frame(client, session_id, SCREENS[0], timestamp=100)
    idle(backend, session_id)
    words(client, session_id)
    assert post_frame(client, session_id, SCREENS[2], timestamp=300).json()["reason"] == "looking"
    backend.meta.replies = [line("Жми.", show=SHOW)]
    look(client, session_id, [SCREENS[1]], first_taken=200)
    idle(backend, session_id)
    assert "hint" not in kinds(seen) and session.last_frame.timestamp_ms == 300


def test_a_frame_a_look_has_overtaken_changes_nothing(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    session = backend.store.sessions[session_id]
    post_frame(client, session_id, SCREENS[0], timestamp=100)
    idle(backend, session_id)
    backend.meta.replies = [line("Жми.", show=SHOW)]
    words(client, session_id)
    look(client, session_id, [SCREENS[1]], first_taken=300)
    idle(backend, session_id)
    calls = len(backend.meta.calls)
    assert post_frame(client, session_id, SCREENS[0], timestamp=200).json() == {"accepted": False, "reason": "old"}
    idle(backend, session_id)
    assert session.last_frame.timestamp_ms == 300 and len(backend.meta.calls) == calls
    assert session.shown and "left" not in kinds(seen)


def test_a_clock_set_back_on_the_phone_does_not_stop_the_frames(backend):
    client = backend.client
    session_id = sharing(client)
    post_frame(client, session_id, SCREENS[0], timestamp=100_000)
    idle(backend, session_id)
    assert post_frame(client, session_id, SCREENS[1], timestamp=50_000).json() == {"accepted": True}
    idle(backend, session_id)
    assert backend.store.sessions[session_id].last_frame.timestamp_ms == 50_000


def test_a_look_that_comes_too_late_is_told_so(backend, monkeypatch):
    client = backend.client
    session_id = sharing(client)
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    words(client, session_id)
    session = backend.store.sessions[session_id]
    read_pictures = server.read_pictures

    async def late(request):
        session.heard = ""
        return await read_pictures(request)
    monkeypatch.setattr(server, "read_pictures", late)
    assert look(client, session_id, [SCREENS[1]]).status_code == 409


def test_a_payment_screen_read_late_leaves_the_log_and_the_chat(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client)
    backend.meta.replies = [line("Проверь сумму.")]
    post_frame(client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert list((folder / "frames").iterdir())
    assert client.post("/sessions/" + session_id + "/text", headers=AUTH, json={
        "frame_ts": 123, "lines": [{"text": "Bayar Sekarang", "words": []}]}).status_code == 200
    assert not list((folder / "frames").iterdir()) and "Проверь сумму" not in (folder / "call.log").read_text(encoding="utf-8")
    assert not any(turn.get("frame") for turn in backend.store.sessions[session_id].chat)


def test_no_outline_over_a_screen_that_came_while_it_was_drawn(backend, monkeypatch):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    session = backend.store.sessions[session_id]
    backend.meta.replies = [line("Жми.", show=SHOW)]
    real = hint.render

    def render(*args):
        session.last_frame = friend.prepare_frame(SCREENS[3], 720, 1560, 2, time.perf_counter())
        return real(*args)
    monkeypatch.setattr(hint, "render", render)
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    assert "hint" not in kinds(seen) and session.last_hint is None


def test_words_that_arrive_after_the_call_ended_start_nothing(backend, monkeypatch):
    client = backend.client
    session_id = new_session(client)
    session = backend.store.sessions[session_id]

    def transcribe(audio, language_bias=None):
        session.ended = True
        return "Куда нажать?"
    monkeypatch.setattr(client.app.state.speech, "transcribe", transcribe)
    assert say(client, session_id).status_code == 200
    assert backend.meta.calls == [] and session.turn == 0


def test_an_outline_of_a_screen_they_left_does_not_come_back(backend):
    client = backend.client
    session_id = sharing(client)
    backend.meta.replies = [line("Жми.", show=SHOW)]
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    assert client.get("/sessions/" + session_id + "/hint", headers=AUTH).status_code == 200
    post_frame(client, session_id, SCREENS[1])
    idle(backend, session_id)
    assert client.get("/sessions/" + session_id + "/hint", headers=AUTH).status_code == 204


def test_new_words_replace_the_answer_under_way_and_its_turn_stays(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Старое."), line("Новое.")]
    try:
        post_frame(client, session_id)
        assert entered.wait(2)
        words(client, session_id, "Подожди")
        assert look(client, session_id, [SCREENS[0]], first_taken=200).status_code == 200
        assert wait_until(lambda: len(backend.meta.calls) == 2)
        idle(backend, session_id)
    finally:
        release.set()
    assert spoken(seen) == ["Новое."]
    assert texts(backend.meta.calls[1].messages[-1]) == [RU["silent"], "Подожди"]


def test_words_before_the_first_sound_are_one_line(backend):
    client = backend.client
    session_id = new_session(client)
    words(client, session_id, "Посмотри цену")
    idle(backend, session_id)
    words(client, session_id, "и доставку")
    idle(backend, session_id)
    call = backend.meta.calls[1]
    assert roles(call) == ["system", "user"] and texts(call.messages[-1]) == ["Посмотри цену и доставку " + RU["unseen"]]


def test_words_after_the_sound_stand_alone(backend):
    client = backend.client
    session_id = new_session(client)
    words(client, session_id, "Посмотри цену")
    idle(backend, session_id)
    played(backend, session_id, 1)
    words(client, session_id, "и доставку")
    idle(backend, session_id)
    assert roles(backend.meta.calls[1]) == ["system", "user", "assistant", "user"]


def test_an_old_sound_leaves_a_newer_line_open(backend):
    client = backend.client
    session_id = new_session(client)
    words(client, session_id, "Раз")
    idle(backend, session_id)
    played(backend, session_id, 1)
    words(client, session_id, "Два")
    idle(backend, session_id)
    played(backend, session_id, 1)
    assert backend.store.sessions[session_id].open_words == "Два"


def test_the_notes_come_back_on_the_next_turn(backend):
    client = backend.client
    session_id = new_session(client)
    backend.meta.replies = [line("Да.", notes="ищем подарок")]
    words(client, session_id, "Раз")
    idle(backend, session_id)
    played(backend, session_id, 1)
    words(client, session_id, "Два")
    idle(backend, session_id)
    assert "ищем подарок" in backend.meta.calls[1].messages[2]["content"]


def test_the_model_sees_the_last_four_different_screens_and_all_the_words(backend):
    client = backend.client
    session_id = sharing(client)
    screens = [jpeg((value, 0, 0)) for value in (0, 50, 100, 150, 200, 250)]
    for index, screen in enumerate(screens):
        assert post_frame(client, session_id, screen, timestamp=index).json() == {"accepted": True}
        idle(backend, session_id)
    messages = backend.meta.calls[-1].messages
    seen = [url for message in messages if message["role"] == "user" for url in pictures(message)]
    assert seen == [meta.data_url(screen) for screen in screens[2:]]
    assert sum(len(texts(message)) for message in messages if message["role"] == "user") == 6


def test_a_new_look_at_a_screen_replaces_the_old_one_and_the_page_before_stays(backend):
    client = backend.client
    session_id = sharing(client)
    post_frame(client, session_id, SCREENS[0], timestamp=1)
    idle(backend, session_id)
    post_frame(client, session_id, SCREENS[1], timestamp=2)
    idle(backend, session_id)
    again = covered((0, 0, 40, 40), (90, 90, 90))
    for index, screen in enumerate((again, again)):
        words(client, session_id)
        assert look(client, session_id, [screen], first_taken=10 + index).status_code == 200
        idle(backend, session_id)
        played(backend, session_id, backend.store.sessions[session_id].turn)
    messages = backend.meta.calls[-1].messages
    assert sum(len(pictures(message)) for message in messages if message["role"] == "user") == 3


def test_the_first_answer_failing_brings_the_spare_model(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [OSError("down"), line("Запасной.")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert [call.model for call in backend.meta.calls] == ["test-talk", "test-spare"] and spoken(seen) == ["Запасной."]


@pytest.mark.parametrize("broken", ["", "нет", "[1, 2]", '{"say": ', "{1: 2}"])
def test_a_broken_answer_brings_the_spare_model(backend, broken):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [broken, line("Запасной.")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert spoken(seen) == ["Запасной."]


def test_a_late_answer_brings_the_spare_model_and_changes_nothing_after_it(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    entered, release = blocked(backend, only=1)
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    session = backend.store.sessions[session_id]
    backend.meta.replies = [line("Поздно.", paste="главная", notes="главная"), line("Запасной.", paste="запасная", notes="запасная")]
    try:
        words(backend.client, session_id)
        assert wait_until(lambda: spoken(seen))
        idle(backend, session_id)
    finally:
        release.set()
    time.sleep(0.05)
    answers = [json.loads(turn["text"]) for turn in session.chat if turn["role"] == "assistant"]
    assert list(filter(None, spoken(seen))) == ["Запасной."] and session.texts == ["запасная"]
    assert [answer["notes"] for answer in answers] == ["запасная"] and session.last_answer["notes"] == "запасная"


def test_a_line_from_the_main_model_can_still_win_beside_the_spare_one(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    spare_began, main_said, spare_ended = threading.Event(), threading.Event(), threading.Event()

    def hook(index):
        if index == 1:
            assert spare_began.wait(2)
        else:
            spare_began.set()
            assert main_said.wait(2)

    def after(index):
        if index == 1:
            main_said.set()
            assert spare_ended.wait(2)
            time.sleep(0.05)
        else:
            spare_ended.set()
    backend.meta.hook, backend.meta.after_text = hook, after
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    session = backend.store.sessions[session_id]
    backend.meta.replies = [line("Главный.", paste="главная", notes="главная"), line("Запасной.", paste="запасная", notes="запасная")]
    words(backend.client, session_id)
    assert wait_until(lambda: session.last_answer)
    idle(backend, session_id)
    assert list(filter(None, spoken(seen))) == ["Главный."] and session.texts == ["главная"] and session.last_answer["notes"] == "главная"


def test_a_line_in_time_keeps_the_spare_model_out_however_long_the_rest_takes(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    backend.meta.after_text = lambda index: time.sleep(0.15)
    session_id = new_session(backend.client)
    words(backend.client, session_id)
    idle(backend, session_id)
    assert [call.model for call in backend.meta.calls] == ["test-talk"]


def test_a_whole_answer_shuts_the_spare_model_up(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    spare_began, answered = threading.Event(), threading.Event()

    def hook(index):
        if index == 1:
            assert spare_began.wait(2)
        else:
            spare_began.set()
            assert answered.wait(2)
    backend.meta.hook = hook
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [{"notes": "заметка", "say": "Главный."}, line("Запасной.")]
    words(backend.client, session_id)
    assert wait_until(lambda: spoken(seen))
    idle(backend, session_id)
    answered.set()
    time.sleep(0.05)
    assert spoken(seen) == ["Главный."] and len(backend.store.sessions[session_id].chat) == 2


def test_silence_from_the_spare_model_waits_for_the_main_one(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    spare_ended = threading.Event()

    def hook(index):
        if index == 1:
            assert spare_ended.wait(2)
            time.sleep(0.05)
    backend.meta.hook = hook
    backend.meta.after_text = lambda index: spare_ended.set() if index == 2 else None
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Главный."), line("")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert spoken(seen) == ["Главный."]


def test_silence_from_the_spare_model_stands_when_the_main_one_broke(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [OSError("down"), line("")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert spoken(seen) == [] and "quiet" in kinds(seen)
    assert backend.store.sessions[session_id].last_answer["say"] == ""


def test_a_spare_line_stands_when_the_rest_of_its_answer_breaks(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    entered, release = blocked(backend, only=1)
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    session = backend.store.sessions[session_id]
    backend.meta.replies = [line("Главный.", paste="главная"), '{"say": "Запасной.", "notes": "обрыв']
    try:
        words(backend.client, session_id)
        assert wait_until(lambda: spoken(seen))
        idle(backend, session_id)
    finally:
        release.set()
    time.sleep(0.05)
    assert spoken(seen) == ["Запасной."] and session.texts == [] and json.loads(session.chat[-1]["text"])["say"] == "Запасной."


def test_a_spare_line_keeps_its_own_answer_when_the_main_one_ends_first(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    spare_said, main_ended = threading.Event(), threading.Event()

    def after(index):
        if index == 1:
            main_ended.set()
        else:
            spare_said.set()
            assert main_ended.wait(2)
            time.sleep(0.05)
    backend.meta.hook = lambda index: spare_said.wait(2) if index == 1 else None
    backend.meta.after_text = after
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    session = backend.store.sessions[session_id]
    backend.meta.replies = [line("Главный.", notes="главная"), line("Запасной.", notes="запасная")]
    words(backend.client, session_id)
    assert wait_until(lambda: session.last_answer)
    idle(backend, session_id)
    assert spoken(seen) == ["Запасной."] and session.last_answer["notes"] == "запасная"


def test_both_models_share_one_limit(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    entered, release = blocked(backend, only=1)
    session_id = new_session(backend.client)
    try:
        words(backend.client, session_id)
        assert wait_until(lambda: len(backend.meta.calls) == 2)
        idle(backend, session_id)
    finally:
        release.set()
    assert backend.meta.calls[1].timeout < backend.meta.calls[0].timeout


def test_the_log_says_when_the_spare_model_began_and_not_how_the_loser_ended(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    entered, release = blocked(backend, only=1)
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [OSError("down"), line("Запасной.")]
    try:
        words(backend.client, session_id)
        assert wait_until(lambda: spoken(seen))
        idle(backend, session_id)
    finally:
        release.set()
    time.sleep(0.05)
    rows = lines_of(next((tmp_path / "calls").iterdir()))
    assert [row["model"] for row in rows if row["kind"] == "spare"] == ["test-spare"] and "no answer" not in [row["kind"] for row in rows]


def test_a_new_turn_stops_the_race(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    release = threading.Event()
    backend.meta.hook = lambda index: release.wait(5) if index < 3 else None
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Старый."), line("Старый запасной."), line("Новый.")]
    words(backend.client, session_id, "Первое")
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    words(backend.client, session_id, "Второе")
    assert wait_until(lambda: spoken(seen) == ["Новый."])
    release.set()
    time.sleep(0.05)
    assert spoken(seen) == ["Новый."]


def test_the_first_object_of_a_reply_counts(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = ['Вот: {"say": "Первый."} {"say": "Второй."}']
    words(backend.client, session_id)
    idle(backend, session_id)
    assert spoken(seen) == ["Первый."] and len(backend.meta.calls) == 1


def test_no_answer_at_all_says_so_on_words(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [OSError("down"), OSError("down")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert spoken(seen) == [RU["failed"]]


def test_no_answer_on_a_screen_stays_silent(backend):
    session_id = sharing(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [OSError("down"), OSError("down")]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert kinds(seen) == ["quiet"]


@pytest.mark.parametrize("words_in,cut", [
    ("Первая фраза. " * 60, ("Первая фраза. " * 35).strip()),
    ("а" * 600, "а" * 500),
    ("  Да.  ", "Да."),
    (None, ""),
])
def test_a_reply_over_the_limit_ends_with_a_whole_sentence(words_in, cut):
    assert friend._line(words_in) == cut


@pytest.mark.parametrize("target_lang", ["auto", "id"])
def test_the_screen_gives_the_app_language(backend, target_lang):
    client = backend.client
    session_id = sharing(client, target_lang=target_lang)
    backend.meta.replies = [line(lang="TH"), line(lang="не язык")]
    payload = {"text": "Подождите здесь", "from": "ru", "to": "auto", "session_id": session_id}
    assert client.post("/translate", headers=AUTH, json=payload).status_code == 409
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    post_frame(client, session_id, SCREENS[1])
    idle(backend, session_id)
    assert client.post("/translate", headers=AUTH, json=payload).status_code == 200
    assert json.loads(backend.meta.text_calls[-1][-1]["content"])["to"] == "th"


def test_a_translation_goes_into_the_language_of_the_screen_whatever_the_settings_say(backend):
    client = backend.client
    session_id = sharing(client)
    backend.meta.replies = [line(lang="zh")]
    payload = {"text": "Подождите здесь", "from": "ru", "to": "id", "session_id": session_id}
    assert client.post("/translate", headers=AUTH, json=payload).status_code == 200
    assert json.loads(backend.meta.text_calls[-1][-1]["content"])["to"] == "id"
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    assert client.post("/translate", headers=AUTH, json=payload).status_code == 200
    assert json.loads(backend.meta.text_calls[-1][-1]["content"])["to"] == "zh"


def test_the_language_of_the_screen_goes_back_with_the_frames(backend):
    client = backend.client
    session_id = sharing(client, target_lang="id")
    backend.meta.replies = [line(lang="zh")]
    assert post_frame(client, session_id).json() == {"accepted": True}
    idle(backend, session_id)
    assert post_frame(client, session_id, timestamp=124).json() == {"accepted": False, "reason": "same", "lang": "zh"}


def test_what_is_outlined_goes_when_they_leave_the_screen(backend):
    client = backend.client
    session_id = sharing(client)
    backend.meta.replies = [line("Жми.", show=SHOW)]
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    seen = events_of(backend, session_id)
    post_frame(client, session_id, SCREENS[1])
    idle(backend, session_id)
    assert kinds(seen) == ["left", "say"]


def test_a_video_on_the_page_leaves_the_outline_alone(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Жми вот сюда.", show=SHOW)]
    backend.meta.hook = lambda index: post_frame(client, session_id, with_video())
    post_frame(client, session_id)
    idle(backend, session_id)
    post_frame(client, session_id, with_video())
    assert kinds(seen) == ["say", "hint"]


def test_no_outline_over_a_screen_they_left_while_the_model_thought(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.store.sessions[session_id].restarts = friend.MAX_RESTARTS
    backend.meta.replies = [line("Жми вот сюда.", show=SHOW)]
    backend.meta.hook = lambda index: post_frame(client, session_id, SCREENS[1])
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    assert "hint" not in kinds(seen) and backend.store.sessions[session_id].last_hint is None


def test_no_outline_if_they_left_while_the_button_was_found(backend, monkeypatch):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Жми вот сюда.", show=SHOW)]
    found = grounding.locate

    def then_they_left(*args, **kwargs):
        post_frame(client, session_id, SCREENS[1])
        return found(*args, **kwargs)
    monkeypatch.setattr(grounding, "locate", then_they_left)
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    assert "hint" not in kinds(seen)


def test_a_bad_box_goes_and_the_label_still_finds_the_button(backend, monkeypatch):
    monkeypatch.setattr(grounding, "locate", real_locate)
    session_id = sharing(backend.client)
    session = backend.store.sessions[session_id]
    session.remember_text(123, [{"text": "Beli Langsung", "box": [0.1, 0.5, 0.4, 0.55], "words": [
        {"text": "Beli", "box": [0.1, 0.5, 0.2, 0.55]}, {"text": "Langsung", "box": [0.21, 0.5, 0.4, 0.55]}]}])
    backend.meta.replies = [line("Жми её.", show=dict(SHOW, box=[900, 900, 100, 960]))]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert session.grounding["box"] == [0.1, 0.5, 0.4, 0.55] and session.last_hint["timings"]["box_source"] == "text"


def test_words_read_on_the_same_screen_help_a_kept_frame(backend, monkeypatch):
    monkeypatch.setattr(grounding, "locate", real_locate)
    client = backend.client
    session_id = sharing(client)
    post_frame(client, session_id, timestamp=500)
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    session.remember_text(500, [{"text": "Bayar", "box": [0.3, 0.3, 0.5, 0.35], "words": [{"text": "Bayar", "box": [0.3, 0.3, 0.5, 0.35]}]}])
    backend.meta.replies = [line("Вот она.", show={"label": "Bayar", "object": "button", "box": []})]
    words(client, session_id)
    assert look(client, session_id, [jpeg()], first_taken=900).status_code == 200
    idle(backend, session_id)
    assert session.grounding["box"] == [0.3, 0.3, 0.5, 0.35]


def test_a_button_nobody_finds_stays_only_in_words(backend):
    session_id = sharing(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Она вверху.", show={"label": "Bayar", "object": "", "box": []})]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert kinds(seen) == ["say"]


def test_the_model_hears_on_its_next_turn_that_no_outline_came(backend):
    client = backend.client
    session_id = sharing(client)
    backend.meta.replies = [line("Она вверху.", show={"label": "Lanjut", "object": "", "box": []}), line("Вот тут."), line("Да.")]
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    words(client, session_id, "Где?")
    look(client, session_id, [SCREENS[0]], first_taken=200)
    idle(backend, session_id)
    assert texts(backend.meta.calls[1].messages[-1]) == [RU["unshown"], "Где?"]
    played(backend, session_id, 1)
    words(client, session_id, "Вижу")
    look(client, session_id, [SCREENS[0]], first_taken=300)
    idle(backend, session_id)
    assert texts(backend.meta.calls[2].messages[-1]) == ["Вижу"]


def test_the_model_hears_that_they_left_before_the_outline_came(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client)
    backend.store.sessions[session_id].restarts = friend.MAX_RESTARTS
    backend.meta.replies = [line("Жми вот сюда.", show=SHOW), line("Вижу.")]
    post_frame(client, session_id, SCREENS[0], timestamp=1)
    assert entered.wait(2)
    post_frame(client, session_id, SCREENS[1], timestamp=2)
    release.set()
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    assert texts(backend.meta.calls[1].messages[-1]) == [RU["unshown"], RU["silent"]]


def test_the_model_hears_that_a_new_turn_stopped_the_outline(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def locate(*args):
        entered.set()
        assert release.wait(5)
        return {"box": [0.1, 0.1, 0.9, 0.2], "source": "test-sam", "ms": 7}
    monkeypatch.setattr(grounding, "locate", locate)
    client = backend.client
    session_id = sharing(client)
    backend.meta.replies = [line("Жми вот сюда.", show=SHOW), line("Вижу.")]
    post_frame(client, session_id, SCREENS[0], timestamp=1)
    assert entered.wait(2)
    post_frame(client, session_id, SCREENS[1], timestamp=2)
    release.set()
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    assert texts(backend.meta.calls[1].messages[-1]) == [RU["unshown"], RU["silent"]]


def test_an_outline_that_came_leaves_nothing_to_hear(backend):
    client = backend.client
    session_id = sharing(client)
    backend.meta.replies = [line("Жми.", show=SHOW), line("Да.")]
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    words(client, session_id, "Где?")
    look(client, session_id, [SCREENS[0]], first_taken=200)
    idle(backend, session_id)
    assert texts(backend.meta.calls[1].messages[-1]) == ["Где?"]


def test_words_before_the_first_sound_take_the_fact_away_with_the_answer(backend):
    client = backend.client
    session_id = sharing(client)
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    backend.meta.replies = [line("Она вверху.", show={"label": "Lanjut", "object": "", "box": []}), line("Да.")]
    words(client, session_id, "Где")
    look(client, session_id, [SCREENS[0]], first_taken=200)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].chat[-1]["text"] == RU["unshown"]
    words(client, session_id, "кнопка")
    look(client, session_id, [SCREENS[0]], first_taken=300)
    idle(backend, session_id)
    assert texts(backend.meta.calls[-1].messages[-1]) == ["Где кнопка"]


def test_the_outline_does_not_hold_up_the_server(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def locate(*args):
        entered.set()
        assert release.wait(5)
        return {"box": [0.1, 0.1, 0.9, 0.2], "source": "test-sam", "ms": 7}
    monkeypatch.setattr(grounding, "locate", locate)
    session_id = sharing(backend.client)
    backend.meta.replies = [line(show=SHOW)]
    try:
        post_frame(backend.client, session_id)
        assert entered.wait(2)
        assert backend.client.get("/health").status_code == 200
    finally:
        release.set()
    idle(backend, session_id)
    assert backend.store.sessions[session_id].grounding["source"] == "test-sam"


def test_the_end_of_the_call_drops_the_answer_under_way(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    try:
        post_frame(client, session_id)
        assert entered.wait(2)
        client.post("/sessions/" + session_id + "/end", headers=AUTH)
    finally:
        release.set()
    idle(backend, session_id)
    assert kinds(seen) == ["done"]


def test_other_call_runs_while_the_model_is_blocked(backend):
    entered, release = blocked(backend, only=1)
    first, second = sharing(backend.client), sharing(backend.client)
    try:
        post_frame(backend.client, first)
        assert entered.wait(2)
        post_frame(backend.client, second)
        idle(backend, second)
        assert backend.store.sessions[second].last_answer is not None
        assert backend.store.sessions[first].last_answer is None
    finally:
        release.set()
    idle(backend, first)


def lines_of(folder):
    return [json.loads(line) for line in (folder / "call.log").read_text(encoding="utf-8").splitlines()]


def test_a_call_is_kept_only_when_asked_for_and_outside_the_repository(backend, monkeypatch, tmp_path):
    session_id = sharing(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert not list(tmp_path.glob("calls/*"))
    monkeypatch.setattr(config, "CALLS_DIR", str(Path(friend.__file__).parents[2] / "calls"))
    new_session(backend.client)
    assert not (Path(friend.__file__).parents[2] / "calls").exists()


def test_the_log_of_a_call_says_what_was_heard_seen_and_said(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client)
    picture = jpeg()
    backend.meta.replies = [line("Жми её.", show=SHOW)]
    words(client, session_id, "Что нажать?")
    assert look(client, session_id, [picture], first_taken=777).status_code == 200
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert (tmp_path / "calls").stat().st_mode & 0o777 == 0o700 and folder.stat().st_mode & 0o777 == 0o700
    assert (folder / "frames").stat().st_mode & 0o777 == 0o700 and (folder / "call.log").stat().st_mode & 0o777 == 0o600
    lines = lines_of(folder)
    assert [line["kind"] for line in lines] == ["call", "broadcast", "heard", "answer", "show"]
    assert lines[2]["said"] == "Что нажать?"
    answer = lines[3]
    assert answer["answer"]["say"] == "Жми её." and answer["model"] == "test-talk" and answer["tokens"] == 9
    assert (folder / "frames" / answer["frame"]).read_bytes() == picture
    assert (folder / "frames" / answer["frame"]).stat().st_mode & 0o777 == 0o600
    assert lines[4]["outcome"] == "shown" and lines[4]["target"]["label"] == SHOW["label"]
    assert config.SHARED_TOKEN not in (folder / "call.log").read_text(encoding="utf-8")


def test_the_log_says_when_no_answer_came(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = new_session(backend.client)
    backend.meta.replies = [OSError("down"), OSError("down")]
    words(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert [line["model"] for line in lines_of(folder) if line["kind"] == "no answer"] == ["test-talk", "test-spare"]


@pytest.mark.parametrize("reply", [line("Сумма 34 900, проверь сам.", private=True, show=SHOW),
                                   line("Введи пароль сам.", show=SHOW)])
def test_a_private_screen_is_not_written_down_and_not_sent_again(backend, monkeypatch, tmp_path, reply):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [reply, line("Хорошо.")]
    post_frame(client, session_id)
    idle(backend, session_id)
    assert spoken(seen) == [reply["say"]] and kinds(seen)[-1] == "hint"
    folder = next((tmp_path / "calls").iterdir())
    assert not list((folder / "frames").iterdir())
    written = (folder / "call.log").read_text(encoding="utf-8")
    assert "34 900" not in written and "пароль" not in written and SHOW["label"] not in written
    played(backend, session_id, 0)
    words(client, session_id)
    assert look(client, session_id, [SCREENS[0]]).status_code == 200
    idle(backend, session_id)
    assert sum(len(pictures(message)) for message in backend.meta.calls[1].messages if message["role"] == "user") == 1


@pytest.mark.parametrize("text,expected", [
    ('{"say": "Да.", "no', "Да."),
    ('  {\n "say" : "Да.", "notes": "x"}', "Да."),
    ('{"say": "Сказал \\"да\\" и\\nвсё", "', 'Сказал "да" и\nвсё'),
    ('{"say": "Ещё пиш', ""),
    ('{"say": "Кончается косой \\', ""),
    ('{"say": "", "show"', ""),
    ('{"notes": "x", "say": "Да."}', ""),
])
def test_the_line_of_an_answer_still_being_written(text, expected):
    assert friend.first_line(text) == expected


def test_the_voice_does_not_wait_for_the_rest_of_the_answer(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    rest = threading.Event()
    backend.meta.after_text = lambda index: rest.wait(5)
    words(backend.client, session_id)
    assert wait_until(lambda: "say" in kinds(seen))
    assert backend.store.sessions[session_id].last_answer is None
    rest.set()
    idle(backend, session_id)
    assert spoken(seen) == ["Это кнопка поиска."]
    answer = json.loads(backend.store.sessions[session_id].chat[-1]["text"])
    assert answer["notes"] == "заметка" and backend.store.sessions[session_id].last_answer == answer


def test_a_ready_text_follows_the_line_that_has_gone_out(backend):
    session_id = new_session(backend.client, text_entry="clipboard")
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Вставь это.", paste="Halo")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert [(event["text_ru"], event["paste_text"]) for event in seen if event["type"] == "say"] == [("Вставь это.", None), ("", "Halo")]


def test_a_line_that_has_gone_out_stands_when_the_answer_breaks(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = ['{"say": "Уже сказал.", "notes": "обрыв']
    words(backend.client, session_id)
    idle(backend, session_id)
    assert spoken(seen) == ["Уже сказал."] and len(backend.meta.calls) == 1
    assert json.loads(backend.store.sessions[session_id].chat[-1]["text"])["say"] == "Уже сказал."


def test_a_screen_whose_answer_broke_leaves_nothing_in_the_log(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies = ['{"say": "Проверь сумму.", "private": tr']
    post_frame(client, session_id)
    idle(backend, session_id)
    assert spoken(seen) == ["Проверь сумму."]
    folder = next((tmp_path / "calls").iterdir())
    assert not list((folder / "frames").iterdir())
    written = [json.loads(row) for row in (folder / "call.log").read_text(encoding="utf-8").splitlines()]
    assert [row["private"] for row in written if row["kind"] == "answer"] == [True] and "Проверь сумму" not in str(written)


def test_a_model_given_up_on_stays_silent(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.02)
    monkeypatch.setattr(friend, "ANSWER_SECONDS", 0.1)
    entered, release = blocked(backend)
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Поздний ответ."), line("Поздний запасной.")]
    words(backend.client, session_id)
    assert wait_until(lambda: spoken(seen) == [RU["failed"]])
    release.set()
    time.sleep(0.05)
    assert spoken(seen) == [RU["failed"]]


def test_a_line_from_an_answer_nobody_waits_for_stays_silent(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Старый ответ."), line("Новый ответ.")]
    entered, release = blocked(backend, 1)
    words(backend.client, session_id, "Первое")
    assert entered.wait(2)
    words(backend.client, session_id, "Второе")
    assert wait_until(lambda: spoken(seen) == ["Новый ответ."])
    release.set()
    time.sleep(0.05)
    assert spoken(seen) == ["Новый ответ."]


PAYMENT_SCREEN = [{"text": text, "box": [0.1, 0.1, 0.3, 0.2], "words": []} for text in (
    "Pembayaran", "Total tagihan", "Rp33.900", "Metode pembayaran", "Transfer Bank", "Kartu Kredit •••• 4242", "Bayar Sekarang")]
PAY_BOX = [100, 100, 300, 200]


@pytest.mark.parametrize("screen,reply", [
    (PAYMENT_SCREEN, line("Проверь сумму и нажми зелёную кнопку внизу.")),
    ([], line("Сумма к оплате верная?")),
    ([], line("Проверь сумму.", show={"label": "Bayar Sekarang", "object": "green button", "box": PAY_BOX})),
    ([], line("Проверь сумму.", show={"label": "Lanjut", "object": "payment button", "box": PAY_BOX})),
])
def test_a_payment_screen_stays_out_of_the_log_when_the_model_forgets_to_say_so(backend, monkeypatch, tmp_path, screen, reply):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = sharing(backend.client)
    backend.store.sessions[session_id].remember_text(123, screen)
    backend.meta.replies = [reply]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert not list((folder / "frames").iterdir())
    answer = next(line for line in lines_of(folder) if line["kind"] == "answer")
    assert answer["private"] is True and "answer" not in answer
    assert "Проверь сумму" not in (folder / "call.log").read_text(encoding="utf-8")


def test_payment_words_that_come_during_the_drawing_reach_the_hint_and_the_log(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client, eyes=True)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    render = hint.render

    def draw(*args):
        assert client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 7, "lines": [{"text": "Bayar Sekarang", "words": []}]}).status_code == 200
        return render(*args)
    monkeypatch.setattr(hint, "render", draw)
    backend.meta.replies = [line("Жми сюда.", show=SHOW)]
    post_frame(client, session_id, timestamp=7)
    idle(backend, session_id)
    assert wait_until(lambda: "hint" in kinds(seen))
    assert next(event for event in seen if event["type"] == "hint")["calm"] is True
    folder = next((tmp_path / "calls").iterdir())
    assert wait_until(lambda: any(row["kind"] == "show" for row in lines_of(folder)))
    assert SHOW["label"] not in (folder / "call.log").read_text(encoding="utf-8")


def test_payment_words_ahead_of_their_frame_wait_for_its_answer(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client, eyes=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Проверь сумму.")]
    assert client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 7, "lines": [{"text": "Bayar Sekarang", "words": []}]}).status_code == 200
    assert "face" not in kinds(seen)
    post_frame(client, session_id, timestamp=7)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert [row["private"] for row in lines_of(folder) if row["kind"] == "answer"] == [True]
    assert not list((folder / "frames").iterdir())
    assert backend.store.sessions[session_id].chat[0]["frame"] is None


def test_payment_words_of_a_frame_gone_with_its_turn_take_it_out_of_the_log(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client, eyes=True)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Проверь сумму."), line("Хорошо.")]
    words(client, session_id)
    assert look(client, session_id, [SCREENS[0]], first_taken=7).status_code == 200
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert [picture.name for picture in (folder / "frames").iterdir()] == ["7.jpg"]
    words(client, session_id, "и ещё")
    assert look(client, session_id, [SCREENS[3]], first_taken=9).status_code == 200
    idle(backend, session_id)
    assert all(turn["frame"].timestamp_ms == 9 for turn in session.chat if turn.get("frame"))
    assert session.last_frame.timestamp_ms == 9
    calm = len([event for event in seen if event.get("calm")])
    assert client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 7, "lines": [{"text": "Bayar Sekarang", "words": []}]}).status_code == 200
    assert [picture.name for picture in (folder / "frames").iterdir()] == ["9.jpg"]
    assert "Проверь сумму" not in (folder / "call.log").read_text(encoding="utf-8")
    assert len([event for event in seen if event.get("calm")]) == calm  # They are on another screen by now.


def test_payment_words_after_the_outline_take_it_out_of_the_log(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client, eyes=True)
    session = backend.store.sessions[session_id]
    backend.meta.replies = [line("Жми сюда.", show=SHOW)]
    post_frame(client, session_id, timestamp=7)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert wait_until(lambda: any(row["kind"] == "show" for row in lines_of(folder)))
    assert session.last_hint["calm"] is False
    assert client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 7, "lines": [{"text": "Bayar Sekarang", "words": []}]}).status_code == 200
    assert not [row for row in lines_of(folder) if row["kind"] in ("show", "answer")]
    assert SHOW["label"] not in (folder / "call.log").read_text(encoding="utf-8")
    assert session.last_hint["calm"] is True


def test_a_product_card_is_not_a_bank_card(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = sharing(backend.client)
    backend.meta.replies = [line("Открой карточку товара, посмотрим объём.")]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert list((folder / "frames").iterdir())
    assert next(line for line in lines_of(folder) if line["kind"] == "answer")["private"] is False


@pytest.mark.parametrize("reply,private", [
    (line("Это Baidu Карты."), False),
    (line("Открой Яндекс Карты.", show={"label": "Карты", "object": "map app icon", "box": [100, 100, 200, 200]}), False),
    (line("Введите номер карты."), True),
    (line("Введите номер вашей карты."), True),
    (line("Проверьте данные карты."), True),
    (line("Подойдёт банковская карта."), True),
    (line("Подойдёт банковскую карту."), True),
    (line("Это можно картой."), True),
    (line("Подойдёт кредитная карта."), True),
    (line("Подойдёт дебетовая карта."), True),
    (line("Укажите срок действия карты."), True),
    (line("Проверь сумму.", show={"label": "Номер карты", "object": "field", "box": [100, 100, 200, 200]}), True),
    (line("Это Baidu Карты.", private=True), True),
])
def test_the_words_of_the_model_tell_a_map_app_from_a_bank_card(backend, monkeypatch, tmp_path, reply, private):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = sharing(backend.client)
    backend.meta.replies = [reply]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert next(row for row in lines_of(folder) if row["kind"] == "answer")["private"] is private
    assert bool(list((folder / "frames").iterdir())) is not private


PAYMENT_WORDS = [
    "支付", "付款方式", "密码", "银行卡", "验证码", "パスワード", "クレジットカード", "お支払い方法", "認証コード",
    "비밀번호", "결제하기", "카드 번호", "카드번호", "인증번호", "รหัสผ่าน", "ชำระเงิน", "บัตรเครดิต", "รหัสยืนยัน",
    "Mật khẩu", "Thanh toán", "Số thẻ", "Mã xác nhận", "كلمة المرور", "الدفع", "رقم البطاقة", "رمز التحقق",
]


@pytest.mark.parametrize("seen_there", ["Карта", "Карты", "Оплата картой", "Bayar"] + PAYMENT_WORDS)
def test_on_the_screen_a_card_stays_private_whatever_the_model_says(backend, monkeypatch, tmp_path, seen_there):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = sharing(backend.client)
    backend.store.sessions[session_id].remember_text(123, [{"text": seen_there, "box": [0.1, 0.1, 0.3, 0.2], "words": []}])
    backend.meta.replies = [line("Это Baidu Карты.")]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert next(row for row in lines_of(folder) if row["kind"] == "answer")["private"] is True
    assert not list((folder / "frames").iterdir())


@pytest.mark.parametrize("said", PAYMENT_WORDS)
def test_the_model_naming_a_payment_in_any_language_is_enough(backend, monkeypatch, tmp_path, said):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = sharing(backend.client)
    backend.meta.replies = [line("Проверь сумму.", show={"label": said, "object": "field", "box": [100, 100, 200, 200]})]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert next(row for row in lines_of(folder) if row["kind"] == "answer")["private"] is True


@pytest.mark.parametrize("seen_there", ["路线 输入终点 搜索 骑行", "検索 経路 目的地", "검색 경로 목적지", "ค้นหา เส้นทาง", "Tìm kiếm Đường đi", "بحث الطريق"])
def test_a_map_in_those_languages_is_not_a_payment(backend, monkeypatch, tmp_path, seen_there):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = sharing(backend.client)
    backend.store.sessions[session_id].remember_text(123, [{"text": seen_there, "box": [0.1, 0.1, 0.3, 0.2], "words": []}])
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert next(row for row in lines_of(folder) if row["kind"] == "answer")["private"] is False
    assert list((folder / "frames").iterdir())


def test_the_words_on_the_screen_make_it_private_without_the_model(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = sharing(backend.client)
    backend.store.sessions[session_id].remember_text(123, [{"text": "Nomor kartu", "box": [0.1, 0.1, 0.3, 0.2], "words": []}])
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert not list((folder / "frames").iterdir())
    answer = next(line for line in lines_of(folder) if line["kind"] == "answer")
    assert answer["private"] is True and "answer" not in answer


def test_the_eyes_ask_nothing_of_the_model(backend):
    plain = new_session(backend.client)
    eyes = new_session(backend.client, eyes=True)
    prompt = lambda session_id: friend.messages_of(backend.store.sessions[session_id])[0]["content"]
    assert "face" not in prompt(plain) and prompt(eyes) == prompt(plain)


def test_the_voice_does_not_wait_for_the_face(backend):
    session_id = new_session(backend.client, eyes=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Вот оно.", face="рад")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert kinds(seen).index("say") < kinds(seen).index("face")


def test_a_face_from_the_model_goes_nowhere(backend):
    session_id = new_session(backend.client, eyes=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Хм.", face="рад")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert [event for event in seen if event["type"] == "face"] == [{"type": "face", "turn": 1, "face": None, "calm": False}]


def test_a_payment_screen_has_no_face_and_keeps_the_eyes_calm(backend):
    session_id = new_session(backend.client, eyes=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Проверь сумму.", face="рад", private=True)]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert [(event["face"], event["calm"]) for event in seen if event["type"] == "face"] == [(None, True)]


def test_words_of_a_payment_calm_the_eyes_before_the_answer(backend):
    client = backend.client
    entered, release = blocked(backend, only=1)
    session_id = sharing(client, eyes=True)
    seen = events_of(backend, session_id)
    try:
        post_frame(client, session_id, timestamp=7)
        assert entered.wait(2)
        assert client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 7, "lines": [{"text": "Bayar Sekarang", "words": []}]}).status_code == 200
        assert [event["calm"] for event in seen if event["type"] == "face"] == [True]
    finally:
        release.set()
    idle(backend, session_id)


def test_the_eyes_read_a_new_screen(backend):
    client = backend.client
    session_id = sharing(client, eyes=True)
    seen = events_of(backend, session_id)
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    assert "reading" in kinds(seen)


def test_nothing_changes_for_a_call_without_eyes(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Вот оно.", face="рад")]
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    assert kinds(seen) == ["say"]


def test_a_screen_that_got_no_answer_lets_the_eyes_stop_waiting(backend):
    client = backend.client
    session_id = sharing(client, eyes=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [OSError("down"), OSError("down")]
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    assert kinds(seen) == ["reading", "quiet"]


def missed(backend, session_id):
    async def connect():
        session = backend.store.sessions[session_id]
        queue = session.subscribe()
        session.unsubscribe(queue)
        return [queue.get_nowait() for _ in range(queue.qsize())]
    return kinds(backend.client.portal.call(connect))


def test_a_phone_that_reconnects_gets_the_line_it_never_played(backend):
    session_id = new_session(backend.client)
    words(backend.client, session_id)
    idle(backend, session_id)
    assert missed(backend, session_id) == ["status", "heard", "say"]
    played(backend, session_id, 1)
    assert missed(backend, session_id) == ["status"]


def test_a_phone_that_reconnects_does_not_get_an_older_line(backend):
    session_id = new_session(backend.client)
    words(backend.client, session_id)
    idle(backend, session_id)
    entered, release = blocked(backend)
    try:
        words(backend.client, session_id, "И ещё")
        assert entered.wait(2)
        assert missed(backend, session_id) == ["status"]
    finally:
        release.set()
    idle(backend, session_id)


def test_a_silent_answer_leaves_nothing_to_get_back(backend):
    session_id = new_session(backend.client)
    backend.meta.replies = [line("")]
    words(backend.client, session_id)
    idle(backend, session_id)
    assert missed(backend, session_id) == ["status"]


def changed(client, session_id, at, moved=False):
    assert client.post("/sessions/" + session_id + "/changed", headers=AUTH, json={"at": at, "moved": moved}).status_code == 200


def cut(client, session_id, number, said=""):
    assert client.post("/sessions/" + session_id + "/spoken", headers=AUTH, json={"line": number, "said": said}).status_code == 200


def said_lines(session):
    return [json.loads(turn["text"])["say"] for turn in session.chat if turn["role"] == "assistant"]


def still_screen(backend, session_id, timestamp=1000):
    post_frame(backend.client, session_id, SCREENS[0], timestamp=timestamp)
    idle(backend, session_id)
    played(backend, session_id, 0)


def test_with_no_change_since_the_last_frame_the_model_starts_at_once(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    seen = events_of(backend, session_id)
    words(client, session_id, "Это что?")
    assert "look" not in kinds(seen) and wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    message = backend.meta.calls[1].messages[-1]
    assert texts(message) == ["Это что?"] and pictures(message) == [meta.data_url(SCREENS[0])]


def test_a_phone_that_promised_no_signals_still_sends_the_screen(backend):
    client = backend.client
    session_id = sharing(client)
    still_screen(backend, session_id)
    seen = events_of(backend, session_id)
    words(client, session_id, "Это что?")
    assert "look" in kinds(seen) and len(backend.meta.calls) == 1


def test_a_changed_screen_is_looked_at_first(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    changed(client, session_id, 2000)
    seen = events_of(backend, session_id)
    words(client, session_id, "Это что?")
    assert "look" in kinds(seen) and len(backend.meta.calls) == 1


def test_a_small_change_stops_nothing(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client, signals=True)
    seen = events_of(backend, session_id)
    try:
        post_frame(client, session_id, SCREENS[0], timestamp=1000)
        assert entered.wait(2)
        changed(client, session_id, 2000)
    finally:
        release.set()
    idle(backend, session_id)
    assert spoken(seen) == ["Это кнопка поиска."] and "quiet" not in kinds(seen)


def test_a_change_the_last_frame_already_shows_is_no_change(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    changed(client, session_id, 1000, moved=True)
    seen = events_of(backend, session_id)
    words(client, session_id, "Это что?")
    assert "look" not in kinds(seen)
    idle(backend, session_id)


def test_only_a_frame_from_after_the_change_ends_it(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    session = backend.store.sessions[session_id]
    changed(client, session_id, 2000)
    post_frame(client, session_id, SCREENS[0], timestamp=1500)
    assert session.changed_at == 2000
    post_frame(client, session_id, SCREENS[0], timestamp=2500)
    assert session.changed_at is None


def test_a_frame_of_an_old_broadcast_is_not_the_screen_now(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    assert client.post("/sessions/" + session_id + "/broadcast", headers=AUTH, json={"state": "finished"}).status_code == 200
    assert client.post("/sessions/" + session_id + "/broadcast", headers=AUTH, json={"state": "started"}).status_code == 200
    seen = events_of(backend, session_id)
    words(client, session_id, "Это что?")
    assert "look" in kinds(seen)


def test_a_moved_screen_takes_the_outline_and_a_turn_with_no_words(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Жми сюда.", show=SHOW), line("Уже не нужно.")]
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    idle(backend, session_id)
    assert "hint" in kinds(seen)
    entered, release = blocked(backend, only=2)
    try:
        post_frame(client, session_id, SCREENS[1], timestamp=2000)
        assert entered.wait(2)
        changed(client, session_id, 3000, moved=True)
        assert kinds(seen)[-1] == "quiet" and client.get("/sessions/" + session_id + "/hint", headers=AUTH).status_code == 204
    finally:
        release.set()
    idle(backend, session_id)
    assert spoken(seen) == ["Жми сюда."] and said_lines(backend.store.sessions[session_id]) == ["Жми сюда."]


def test_a_moved_screen_with_nothing_under_way_says_nothing_more(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    seen = events_of(backend, session_id)
    changed(client, session_id, 2000, moved=True)
    assert not seen


def test_their_question_goes_on_to_the_screen_they_moved_to(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    session = backend.store.sessions[session_id]
    entered, release = blocked(backend, only=2)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Про старый экран."), line("Про новый экран.")]
    try:
        words(client, session_id, "Что тут?")
        assert entered.wait(2)
        changed(client, session_id, 2000, moved=True)
        assert post_frame(client, session_id, SCREENS[1], timestamp=1500).json() == {"accepted": False, "reason": "looking"}
        assert post_frame(client, session_id, SCREENS[1], timestamp=2500).json() == {"accepted": True}
        assert wait_until(lambda: len(backend.meta.calls) == 3)
    finally:
        release.set()
    idle(backend, session_id)
    message = backend.meta.calls[2].messages[-1]
    assert texts(message) == ["Что тут?"] and pictures(message) == [meta.data_url(SCREENS[1])]
    assert spoken(seen) == ["Про новый экран."] and said_lines(session)[-1] == "Про новый экран."


def test_their_question_is_answered_even_if_they_came_back_to_the_same_screen(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    entered, release = blocked(backend, only=2)
    try:
        words(client, session_id, "Что тут?")
        assert entered.wait(2)
        changed(client, session_id, 2000, moved=True)
        assert post_frame(client, session_id, SCREENS[0], timestamp=2500).json() == {"accepted": True}
        assert wait_until(lambda: len(backend.meta.calls) == 3)
    finally:
        release.set()
    idle(backend, session_id)


def test_with_no_frame_after_the_move_their_question_still_gets_an_answer(backend, monkeypatch):
    monkeypatch.setattr(friend, "SETTLE_SECONDS", 0.01)
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    entered, release = blocked(backend, only=2)
    try:
        words(client, session_id, "Что тут?")
        assert entered.wait(2)
        changed(client, session_id, 2000, moved=True)
        assert wait_until(lambda: len(backend.meta.calls) == 3)
    finally:
        release.set()
    idle(backend, session_id)


def test_with_no_frame_after_the_move_the_answer_goes_with_any_screen(backend, monkeypatch):
    monkeypatch.setattr(friend, "SETTLE_SECONDS", 0.01)
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    seen = events_of(backend, session_id)
    words(client, session_id, "Что тут?")
    idle(backend, session_id)
    cut(client, session_id, next(event["line"] for event in seen if event["type"] == "say"))
    assert wait_until(lambda: len(backend.meta.calls) == 3)
    idle(backend, session_id)
    assert [event["frame_ts"] for event in seen if event["type"] == "say"] == [1000, None]


def test_new_words_while_the_screen_settles_join_their_question(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    session = backend.store.sessions[session_id]
    entered, release = blocked(backend, only=2)
    seen = events_of(backend, session_id)
    try:
        words(client, session_id, "Что тут?")
        assert entered.wait(2)
        changed(client, session_id, 2000, moved=True)
        words(client, session_id, "И сколько стоит?")
        assert session.heard == "Что тут? И сколько стоит?" and not session.settling and kinds(seen)[-1] == "look"
    finally:
        release.set()
    assert look(client, session_id, [SCREENS[1]], first_taken=3000).status_code == 200
    idle(backend, session_id)
    assert texts(backend.meta.calls[-1].messages[-1]) == ["Что тут? И сколько стоит?"]


def test_a_line_that_has_gone_out_stands_when_the_screen_moves(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    session = backend.store.sessions[session_id]
    words(client, session_id, "Что тут?")
    idle(backend, session_id)
    changed(client, session_id, 2000, moved=True)
    assert not session.heard and said_lines(session)[-1] == "Это кнопка поиска."


def test_a_question_whose_line_never_sounded_goes_on_to_the_new_screen(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Про старый экран."), line("Про новый экран.")]
    words(client, session_id, "Что тут?")
    idle(backend, session_id)
    changed(client, session_id, 2000, moved=True)
    cut(client, session_id, next(event["line"] for event in seen if event["type"] == "say"))
    assert session.heard == "Что тут?" and session.settling
    post_frame(client, session_id, SCREENS[1], timestamp=2500)
    idle(backend, session_id)
    assert texts(backend.meta.calls[-1].messages[-1]) == ["Что тут?"] and said_lines(session)[-1] == "Про новый экран."
    assert "Про старый экран." not in json.dumps(backend.meta.calls[-1].messages, ensure_ascii=False)


def test_the_phone_can_say_so_before_the_server_hears_of_the_move(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    words(client, session_id, "Что тут?")
    idle(backend, session_id)
    cut(client, session_id, next(event["line"] for event in seen if event["type"] == "say"))
    assert session.heard == "Что тут?" and session.settling
    changed(client, session_id, 2000, moved=True)
    assert session.heard == "Что тут?" and session.changed_at == 2000
    assert post_frame(client, session_id, SCREENS[1], timestamp=2500).json() == {"accepted": True}
    idle(backend, session_id)
    assert texts(backend.meta.calls[-1].messages[-1]) == ["Что тут?"]


def test_a_line_cut_short_stays_in_the_chat_as_far_as_it_sounded(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Первая фраза. Вторая фраза.", show=SHOW)]
    words(client, session_id, "Что тут?")
    idle(backend, session_id)
    cut(client, session_id, next(event["line"] for event in seen if event["type"] == "say"), "Первая фраза.")
    assert not session.heard and said_lines(session)[-1] == "Первая фраза."
    assert "Вторая" not in json.dumps([turn.get("text") for turn in session.chat], ensure_ascii=False)


def test_a_line_cut_short_before_the_rest_of_its_answer_came_stays_cut(backend):
    client = backend.client
    session_id = new_session(client)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    release = threading.Event()
    backend.meta.after_text = lambda index: release.wait(5)
    backend.meta.replies = [line("Первая фраза. Вторая фраза.")]
    words(client, session_id, "Что тут?")
    assert wait_until(lambda: "say" in kinds(seen))
    cut(client, session_id, next(event["line"] for event in seen if event["type"] == "say"), "Первая фраза.")
    release.set()
    idle(backend, session_id)
    assert said_lines(session) == ["Первая фраза."] and json.loads(session.chat[-1]["text"])["notes"] == "заметка"


def test_a_line_on_its_own_that_never_sounded_leaves_nothing(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Смотри.", show=SHOW)]
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    idle(backend, session_id)
    cut(client, session_id, next(event["line"] for event in seen if event["type"] == "say"))
    assert not session.heard and said_lines(session) == [""] and json.loads(session.chat[1]["text"]) == {"say": ""}


def test_a_line_cut_by_new_words_does_not_stop_their_answer(backend):
    client = backend.client
    session_id = new_session(client)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Старое."), line("Новое.")]
    words(client, session_id, "Что тут?")
    idle(backend, session_id)
    entered, release = blocked(backend, only=2)
    try:
        words(client, session_id, "И сколько стоит?")
        assert entered.wait(2)
        cut(client, session_id, next(event["line"] for event in seen if event["type"] == "say"))
        assert not session.answer.done() and not session.heard
    finally:
        release.set()
    idle(backend, session_id)
    assert spoken(seen) == ["Старое.", "Новое."]


def test_a_line_someone_else_cut_is_not_known(backend):
    session_id = new_session(backend.client)
    cut(backend.client, session_id, 7)
    assert not backend.store.sessions[session_id].chat


def test_a_line_names_its_screen_and_its_number(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    seen = events_of(backend, session_id)
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    idle(backend, session_id)
    say_event = next(event for event in seen if event["type"] == "say")
    assert say_event["frame_ts"] == 1000 and say_event["line"] == 1


def test_a_line_with_no_screen_is_about_no_screen(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    words(backend.client, session_id)
    idle(backend, session_id)
    say_event = next(event for event in seen if event["type"] == "say")
    assert say_event["frame_ts"] is None and say_event["line"] == 1


def test_a_failed_line_is_not_cut_and_goes_with_any_screen(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [OSError("down"), OSError("down")]
    words(backend.client, session_id)
    idle(backend, session_id)
    say_event = next(event for event in seen if event["type"] == "say")
    assert say_event["text_ru"] == RU["failed"] and "line" not in say_event and "frame_ts" not in say_event


def test_a_ready_text_names_its_screen(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [{"paste": "Готово", "notes": "x"}]
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    idle(backend, session_id)
    say_event = next(event for event in seen if event["type"] == "say")
    assert say_event["paste_text"] == "Готово" and say_event["frame_ts"] == 1000 and say_event["line"] is None


def test_an_answer_about_a_screen_they_left_goes_unsaid(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client, signals=True)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Жми сюда.", paste="Текст", show=SHOW)]
    try:
        post_frame(client, session_id, SCREENS[0], timestamp=1000)
        assert entered.wait(2)
        post_frame(client, session_id, SCREENS[3], timestamp=2000, small=True)
    finally:
        release.set()
    idle(backend, session_id)
    assert not spoken(seen) and kinds(seen)[-1] == "quiet" and not said_lines(session) and not session.texts


def test_an_answer_about_a_screen_they_left_is_not_said_ahead_of_the_rest(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    seen = events_of(backend, session_id)
    backend.meta.hook = lambda index: post_frame(client, session_id, SCREENS[3], timestamp=2000, small=True) if index == 1 else None
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    idle(backend, session_id)
    assert not spoken(seen)


def test_their_question_goes_on_past_an_answer_about_a_screen_they_left(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client, signals=True)
    still = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Про старый экран."), line("Про новый экран.")]
    try:
        words(client, session_id, "Что тут?")
        assert look(client, session_id, [SCREENS[0]], first_taken=1000).status_code == 200
        assert entered.wait(2)
        post_frame(client, session_id, SCREENS[3], timestamp=2000, small=True)
    finally:
        release.set()
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    assert spoken(seen) == ["Про новый экран."] and said_lines(still) == ["Про новый экран."]
    assert texts(backend.meta.calls[1].messages[-1]) == ["Что тут?", RU["silent"]]
    assert pictures(backend.meta.calls[1].messages[-1])[-1] == meta.data_url(SCREENS[3])
    assert next(event for event in seen if event["type"] == "read")["frame_ts"] == 2000


def test_a_screen_that_keeps_moving_still_gets_an_answer_tied_to_nothing(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client, signals=True)
    session = backend.store.sessions[session_id]
    session.restarts = friend.MAX_RESTARTS
    seen = events_of(backend, session_id)
    try:
        post_frame(client, session_id, SCREENS[0], timestamp=1000)
        assert entered.wait(2)
        changed(client, session_id, 2000, moved=True)
        post_frame(client, session_id, SCREENS[3], timestamp=3000, small=True)
    finally:
        release.set()
    idle(backend, session_id)
    say_event = next(event for event in seen if event["type"] == "say")
    assert say_event["text_ru"] == "Это кнопка поиска." and say_event["frame_ts"] is None and session.restarts == 0


def test_a_move_counts_as_a_restart(backend):
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client, signals=True)
    session = backend.store.sessions[session_id]
    try:
        post_frame(client, session_id, SCREENS[0], timestamp=1000)
        assert entered.wait(2)
        changed(client, session_id, 2000, moved=True)
        assert session.restarts == 1
    finally:
        release.set()
    idle(backend, session_id)


def test_a_small_frame_going_to_the_model_is_read_then(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    assert post_frame(client, session_id, SCREENS[0], timestamp=1500, small=True).json() == {"accepted": False, "reason": "small"}
    seen = events_of(backend, session_id)
    words(client, session_id, "Это что?")
    assert next(event for event in seen if event["type"] == "read")["frame_ts"] == 1500
    idle(backend, session_id)


def test_a_small_frame_with_its_words_is_not_read_again(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    post_frame(client, session_id, SCREENS[0], timestamp=1500, small=True)
    assert client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 1500, "lines": [{"text": "Поиск", "words": []}]}).status_code == 200
    seen = events_of(backend, session_id)
    words(client, session_id, "Это что?")
    assert "read" not in kinds(seen)
    idle(backend, session_id)


def test_a_frame_that_stays_on_the_phone_is_not_read(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    seen = events_of(backend, session_id)
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    idle(backend, session_id)
    post_frame(client, session_id, SCREENS[0], timestamp=1500, small=True)
    assert "read" not in kinds(seen)


def test_the_words_of_a_small_frame_read_late_keep_a_payment_screen_out_of_the_log(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    post_frame(client, session_id, SCREENS[0], timestamp=1500, small=True)
    words(client, session_id, "Это что?")
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 1500, "lines": [{"text": "Bayar Sekarang", "words": []}]}).status_code == 200
    assert "1500.jpg" not in [picture.name for picture in (folder / "frames").iterdir()]
    assert all(not turn.get("frame") or turn["frame"].timestamp_ms != 1500 for turn in backend.store.sessions[session_id].chat)



def test_a_move_takes_the_outline_at_once(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Жми сюда.", show=SHOW)]
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    idle(backend, session_id)
    assert "hint" in kinds(seen)
    changed(client, session_id, 2000, moved=True)
    assert kinds(seen)[-1] == "left" and client.get("/sessions/" + session_id + "/hint", headers=AUTH).status_code == 204


def test_a_move_changes_nothing_for_words_waiting_for_their_look(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    session = backend.store.sessions[session_id]
    changed(client, session_id, 2000)
    words(client, session_id, "Что тут?")
    changed(client, session_id, 3000, moved=True)
    assert session.heard == "Что тут?" and not session.settling


def test_a_change_told_late_does_not_take_back_a_newer_one(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    seen = events_of(backend, session_id)
    changed(client, session_id, 3000)
    changed(client, session_id, 2000)
    post_frame(client, session_id, SCREENS[0], timestamp=2500)
    words(client, session_id, "Это что?")
    assert "look" in kinds(seen)


def test_the_timer_of_their_words_does_not_cut_the_wait_for_the_new_screen(backend, monkeypatch):
    monkeypatch.setattr(friend, "LOOK_SECONDS", 0.1)
    monkeypatch.setattr(friend, "SETTLE_SECONDS", 30)
    entered, release = blocked(backend, only=1)
    client = backend.client
    session_id = sharing(client, signals=True)
    session = backend.store.sessions[session_id]
    try:
        words(client, session_id, "Что тут?")
        assert look(client, session_id, [SCREENS[0]], first_taken=1000).status_code == 200
        assert entered.wait(2)
        changed(client, session_id, 2000, moved=True)
        time.sleep(0.3)
        assert session.heard == "Что тут?" and len(backend.meta.calls) == 1
    finally:
        release.set()


def test_answers_left_behind_count_as_restarts(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    next_screen = {1: (SCREENS[3], 2000), 2: (SCREENS[0], 3000), 3: (SCREENS[3], 4000)}

    def move_on(index):
        if index in next_screen:
            post_frame(client, session_id, next_screen[index][0], timestamp=next_screen[index][1], small=True)
    backend.meta.hook = move_on
    words(client, session_id, "Что тут?")
    assert look(client, session_id, [SCREENS[1]], first_taken=1000).status_code == 200
    assert wait_until(lambda: len(backend.meta.calls) == 3)
    idle(backend, session_id)
    say_event = next(event for event in seen if event["type"] == "say")
    assert say_event["frame_ts"] is None and session.restarts == 0


def test_a_ready_text_past_the_restarts_names_no_screen(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    backend.store.sessions[session_id].restarts = friend.MAX_RESTARTS
    seen = events_of(backend, session_id)
    backend.meta.replies = [{"paste": "Готово", "notes": "x"}]
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    idle(backend, session_id)
    say_event = next(event for event in seen if event["type"] == "say")
    assert say_event["paste_text"] == "Готово" and say_event["frame_ts"] is None


def test_a_line_that_comes_with_the_whole_answer_can_be_cut_too(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies = [{"notes": "x", "say": "Это поздняя реплика."}]
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    idle(backend, session_id)
    say_event = next(event for event in seen if event["type"] == "say")
    assert say_event["line"] == 1
    cut(client, session_id, 1)
    assert said_lines(session) == [""]


def test_only_the_last_lines_are_kept(backend):
    session = backend.store.sessions[new_session(backend.client)]
    numbers = [session.remember_line({}) for _ in range(sessions.KEPT_LINES + 1)]
    assert numbers[0] not in session.lines and numbers[-1] in session.lines and len(session.lines) == sessions.KEPT_LINES


def test_a_line_the_phone_dropped_goes_on_at_once_if_the_new_screen_is_here(backend, monkeypatch):
    monkeypatch.setattr(friend, "SETTLE_SECONDS", 30)
    client = backend.client
    session_id = sharing(client, signals=True)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Про старый экран."), line("Про новый экран.")]
    words(client, session_id, "Что тут?")
    assert look(client, session_id, [SCREENS[0]], first_taken=1000).status_code == 200
    idle(backend, session_id)
    post_frame(client, session_id, SCREENS[3], timestamp=2000, small=True)
    cut(client, session_id, 1)
    assert wait_until(lambda: len(backend.meta.calls) == 2)
    idle(backend, session_id)
    assert spoken(seen)[-1] == "Про новый экран." and pictures(backend.meta.calls[1].messages[-1])[-1] == meta.data_url(SCREENS[3])


def test_a_dropped_line_takes_the_rest_of_its_answer_with_it(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    release = threading.Event()
    backend.meta.after_text = lambda index: release.wait(5)
    backend.meta.replies = [line("Жми сюда.", paste="Текст", show=SHOW)]
    post_frame(client, session_id, SCREENS[0], timestamp=1000)
    assert wait_until(lambda: "say" in kinds(seen))
    cut(client, session_id, 1)
    release.set()
    idle(backend, session_id)
    assert said_lines(session) == [""] and json.loads(session.chat[-1]["text"]) == {"say": ""} and not session.texts
    assert "hint" not in kinds(seen) and not [event for event in seen if event.get("paste_text")]


def test_a_small_frame_goes_in_the_log_only_with_its_words(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    post_frame(client, session_id, SCREENS[0], timestamp=1500, small=True)
    words(client, session_id, "Это что?")
    idle(backend, session_id)
    played(backend, session_id, 1)
    post_frame(client, session_id, SCREENS[0], timestamp=2500, small=True)
    assert client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 2500, "lines": [{"text": "Keranjang", "words": []}]}).status_code == 200
    words(client, session_id, "А это?")
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    kept = [picture.name for picture in (folder / "frames").iterdir()]
    assert "1500.jpg" not in kept and "2500.jpg" in kept
    answers = [row for row in lines_of(folder) if row["kind"] == "answer"]
    assert "answer" not in answers[-2] and answers[-2]["frame"] is None and answers[-1]["frame"] == "2500.jpg" and "answer" in answers[-1]


def test_an_outline_on_an_unread_small_frame_stays_out_of_the_log(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    client = backend.client
    session_id = sharing(client, signals=True)
    still_screen(backend, session_id)
    backend.meta.replies = [line("Жми сюда.", show=SHOW)]
    post_frame(client, session_id, SCREENS[0], timestamp=1500, small=True)
    words(client, session_id, "Куда нажать?")
    idle(backend, session_id)
    rows = [row for row in lines_of(next((tmp_path / "calls").iterdir())) if row["kind"] == "show"]
    assert rows and all(row["target"] is None and "frame" not in row for row in rows)


def test_a_line_dropped_after_the_end_of_the_call_starts_nothing(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    words(client, session_id, "Что тут?")
    assert look(client, session_id, [SCREENS[0]], first_taken=1000).status_code == 200
    idle(backend, session_id)
    post_frame(client, session_id, SCREENS[3], timestamp=2000, small=True)
    assert client.post("/sessions/" + session_id + "/end", headers=AUTH).status_code == 200
    cut(client, session_id, 1)
    idle(backend, session_id)
    assert len(backend.meta.calls) == 1 and not backend.store.sessions[session_id].heard


def test_a_line_the_phone_dropped_waits_if_the_screen_is_still_changing(backend, monkeypatch):
    monkeypatch.setattr(friend, "SETTLE_SECONDS", 30)
    client = backend.client
    session_id = sharing(client, signals=True)
    session = backend.store.sessions[session_id]
    words(client, session_id, "Что тут?")
    assert look(client, session_id, [SCREENS[0]], first_taken=1000).status_code == 200
    idle(backend, session_id)
    post_frame(client, session_id, SCREENS[3], timestamp=2000, small=True)
    changed(client, session_id, 3000)
    cut(client, session_id, 1)
    assert session.heard == "Что тут?" and session.settling and len(backend.meta.calls) == 1
