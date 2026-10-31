from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import config, friend, grounding, hint, meta, server
from conftest import AUTH, idle, jpeg, line, look, new_session, post_frame, real_locate, say, sharing, words

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
    assert texts(message) == ["Какие взять? " + friend.UNSEEN] and not pictures(message)
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
    assert texts(backend.meta.calls[0].messages[-1]) == ["Это что? " + friend.UNSEEN]


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
    assert texts(backend.meta.calls[1].messages[-1]) == ["Два " + friend.UNSEEN]


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
    assert post_frame(client, session_id).json() == {"accepted": True}
    idle(backend, session_id)
    message = backend.meta.calls[0].messages[-1]
    assert texts(message) == [friend.SILENT_TURN] and len(pictures(message)) == 1
    assert post_frame(client, session_id, timestamp=2).json() == {"accepted": False, "reason": "same"}
    assert post_frame(client, session_id, with_video(), timestamp=3).json() == {"accepted": False, "reason": "same"}
    assert post_frame(client, session_id, with_keyboard(), timestamp=4).json() == {"accepted": True}
    idle(backend, session_id)
    assert len(backend.meta.calls) == 2


def test_a_small_change_waits_for_the_next_turn(backend):
    client = backend.client
    session_id = sharing(client)
    post_frame(client, session_id)
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
    assert texts(backend.meta.calls[0].messages[-1]) == ["Видишь? " + friend.UNSEEN]


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
    assert texts(message) == [friend.SILENT_TURN, friend.SILENT_TURN] and len(pictures(message)) == 2


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
    assert session.chat[-2]["text"] == friend.SILENT_TURN and session.chat[-2]["frame"].timestamp_ms == 2 and not session.held


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
    assert not list((folder / "frames").iterdir())
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
        assert look(client, session_id, [SCREENS[0]]).status_code == 200
        assert wait_until(lambda: len(backend.meta.calls) == 2)
        idle(backend, session_id)
    finally:
        release.set()
    assert spoken(seen) == ["Новое."]
    assert texts(backend.meta.calls[1].messages[-1]) == [friend.SILENT_TURN, "Подожди"]


def test_words_before_the_first_sound_are_one_line(backend):
    client = backend.client
    session_id = new_session(client)
    words(client, session_id, "Посмотри цену")
    idle(backend, session_id)
    words(client, session_id, "и доставку")
    idle(backend, session_id)
    call = backend.meta.calls[1]
    assert roles(call) == ["system", "user"] and texts(call.messages[-1]) == ["Посмотри цену и доставку " + friend.UNSEEN]


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
    post_frame(client, session_id, SCREENS[0])
    idle(backend, session_id)
    post_frame(client, session_id, SCREENS[1], timestamp=2)
    idle(backend, session_id)
    again = covered((0, 0, 40, 40), (90, 90, 90))
    for screen in (again, again):
        words(client, session_id)
        assert look(client, session_id, [screen]).status_code == 200
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


def test_a_late_answer_brings_the_spare_model(backend, monkeypatch):
    monkeypatch.setattr(friend, "ANSWER_SECONDS", 0.05)
    entered, release = blocked(backend, only=1)
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [line("Поздно."), line("Запасной.")]
    try:
        words(backend.client, session_id)
        assert wait_until(lambda: spoken(seen))
    finally:
        release.set()
    assert spoken(seen) == ["Запасной."]


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
    assert spoken(seen) == [friend.FAILED_LINE]


def test_no_answer_on_a_screen_stays_silent(backend):
    session_id = sharing(backend.client)
    seen = events_of(backend, session_id)
    backend.meta.replies = [OSError("down"), OSError("down")]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert seen == []


@pytest.mark.parametrize("words_in,cut", [
    ("Первая фраза. " * 60, ("Первая фраза. " * 35).strip()),
    ("а" * 600, "а" * 500),
    ("  Да.  ", "Да."),
    (None, ""),
])
def test_a_reply_over_the_limit_ends_with_a_whole_sentence(words_in, cut):
    assert friend._line(words_in) == cut


@pytest.mark.parametrize("target_lang,app_lang", [("auto", "th"), ("id", "id")])
def test_the_screen_gives_the_app_language_only_when_asked_for(backend, target_lang, app_lang):
    client = backend.client
    session_id = sharing(client, target_lang=target_lang)
    backend.meta.replies = [line(lang="TH"), line(lang="не язык")]
    payload = {"text": "Подождите здесь", "from": "ru", "to": "auto", "session_id": session_id}
    assert client.post("/translate", headers=AUTH, json=payload).status_code == (409 if target_lang == "auto" else 200)
    post_frame(client, session_id)
    idle(backend, session_id)
    assert client.post("/translate", headers=AUTH, json=payload).status_code == 200
    assert json.loads(backend.meta.text_calls[-1][-1]["content"])["to"] == app_lang


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
    assert json.loads(backend.store.sessions[session_id].chat[-1]["text"]) == {"say": "Уже сказал."}


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


def test_a_product_card_is_not_a_bank_card(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = sharing(backend.client)
    backend.meta.replies = [line("Открой карточку товара, посмотрим объём.")]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert list((folder / "frames").iterdir())
    assert next(line for line in lines_of(folder) if line["kind"] == "answer")["private"] is False


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
