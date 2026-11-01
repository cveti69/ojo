from __future__ import annotations

import json
import time

from app import config, friend, language
from conftest import AUTH, idle, jpeg, line, look, new_session, post_frame, sharing, words
from test_friend import PAYMENT_SCREEN, SCREENS, SHOW, events_of, kinds, spoken, texts

RU = language.words("ru")


def turned(say="Не смотрю.", **fields):
    """The field comes first, as the rules list it, so the line waits for the whole answer."""
    return {"look": False, **line(say, **fields)}


def back(say="Смотрю."):
    return {"look": True, **line(say)}


def asked_with(backend, index):
    return backend.meta.calls[index].messages


def pictures_in(messages):
    return sum(1 for message in messages if isinstance(message["content"], list) for part in message["content"] if part["type"] == "image_url")


def last_words(messages):
    return next(part["text"] for part in reversed(messages[-1]["content"]) if part["type"] == "text")


def lines_of(folder):
    return [json.loads(row) for path in folder.glob("*/call.log") for row in path.read_text().splitlines()]


def away(backend, client, session_id, screen=None, said="Не смотри"):
    """A call whose agent saw one screen, and then was told to look away."""
    backend.meta.replies.append(line("Вижу экран."))
    post_frame(client, session_id, SCREENS[0] if screen is None else screen, 100)
    idle(backend, session_id)
    backend.meta.replies.append(turned())
    words(client, session_id, said)
    assert look(client, session_id, [SCREENS[1]]).status_code == 200
    idle(backend, session_id)
    played(backend, session_id)


def played(backend, session_id):
    """The phone says the answer has sounded, so their next words are a new line."""
    turn = backend.store.sessions[session_id].turn
    assert backend.client.post("/sessions/" + session_id + "/played", headers=AUTH, json={"turn": turn}).status_code == 200


def test_closing_tells_the_phone_ahead_of_the_line(backend):
    client = backend.client
    session_id = sharing(client, eyes=True)
    seen = events_of(backend, session_id)
    away(backend, client, session_id)
    assert seen.index({"type": "not_looking", "on": True}) < next(index for index, event in enumerate(seen) if event["type"] == "say" and event["text_ru"] == "Не смотрю.")
    assert backend.store.sessions[session_id].not_looking


def test_frames_go_nowhere_once_closed(backend):
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    session = backend.store.sessions[session_id]
    calls = len(backend.meta.calls)
    reply = post_frame(client, session_id, SCREENS[2], 200).json()
    assert reply == {"accepted": False, "reason": "not_looking"}
    idle(backend, session_id)
    assert len(backend.meta.calls) == calls and session.last_frame is None and session.seen_gray is None
    assert client.post("/sessions/" + session_id + "/changed", headers=AUTH, json={"at": 300, "moved": True}).status_code == 200
    assert session.changed_at is None
    assert look(client, session_id, [SCREENS[3]]).status_code == 409


def test_nothing_of_the_closing_turn_is_kept(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path))
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    session = backend.store.sessions[session_id]
    assert all(turn.get("frame") is None for turn in session.chat[session.open_mark:])
    answers = [row for row in lines_of(tmp_path) if row["kind"] == "answer"]
    assert len(answers) == 2 and answers[0]["frame"] and answers[1]["frame"] is None and "answer" not in answers[1]
    assert any(row["kind"] == "not looking" and row["on"] is True for row in lines_of(tmp_path))
    assert len(list(tmp_path.glob("*/frames/*"))) == 1


def test_the_screen_they_turn_away_from_leaves_the_log(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path))
    client = backend.client
    session_id = sharing(client, signals=True)
    backend.meta.replies.append(line("Вижу экран."))
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    played(backend, session_id)
    assert len(list(tmp_path.glob("*/frames/*"))) == 1
    backend.meta.replies.append(turned())
    words(client, session_id, "Не смотри")  # Nothing has changed, so the answer goes on the frame that is there.
    idle(backend, session_id)
    assert not list(tmp_path.glob("*/frames/*")) and not any(row.get("frame") for row in lines_of(tmp_path))


def test_the_hint_goes_with_the_eyes(backend):
    client = backend.client
    session_id = sharing(client, eyes=True)
    backend.meta.replies.append(line("Нажми сюда.", show=SHOW))
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    assert session.shown and session.last_hint
    seen = events_of(backend, session_id)
    backend.meta.replies.append(turned(show=SHOW))
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[0]]).status_code == 200
    idle(backend, session_id)
    assert not session.shown and session.last_hint is None
    assert "hint" not in kinds(seen) and kinds(seen).index("left") < kinds(seen).index("not_looking")
    assert client.get("/sessions/" + session_id + "/hint", headers=AUTH).status_code == 204


def test_words_go_on_with_no_picture_and_a_mark(backend):
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    backend.meta.replies.append(line("Хорошо, поговорим."))
    words(client, session_id, "Как дела?")
    idle(backend, session_id)
    messages = asked_with(backend, -1)
    assert pictures_in(messages) == 0 and last_words(messages) == "Как дела? " + RU["closed"]
    assert backend.store.sessions[session_id].not_looking


def test_a_language_of_its_own(backend):
    client = backend.client
    session_id = sharing(client, user_lang="en-US")
    away(backend, client, session_id)
    backend.meta.replies.append(line("Sure."))
    words(client, session_id, "How are you?")
    idle(backend, session_id)
    assert last_words(asked_with(backend, -1)) == "How are you? " + language.words("en")["closed"]


def test_no_line_goes_ahead_with_the_eyes_shut(backend):
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    seen = events_of(backend, session_id)
    backend.meta.replies.append(line("Это без экрана.", look=True))  # The field that takes the line back comes after it.
    words(client, session_id, "Посмотри")
    idle(backend, session_id)
    assert spoken(seen) == []
    backend.meta.replies.append(line("Вижу."))
    assert look(client, session_id, [SCREENS[2]]).status_code == 200
    idle(backend, session_id)
    assert spoken(seen) == ["Вижу."]


def test_a_look_opens_with_one_answer_on_a_fresh_frame(backend):
    client = backend.client
    session_id = sharing(client, eyes=True)
    away(backend, client, session_id)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    backend.meta.replies.append(back("Это я сказал бы без экрана."))
    words(client, session_id, "Посмотри, что скажешь")
    idle(backend, session_id)
    assert not session.not_looking and {"type": "not_looking", "on": False} in seen
    assert "Это я сказал бы без экрана." not in spoken(seen)
    assert kinds(seen).index("not_looking") < kinds(seen).index("look")
    calls = len(backend.meta.calls)
    backend.meta.replies.append(line("Вижу новый экран."))
    assert look(client, session_id, [SCREENS[2]]).status_code == 200
    idle(backend, session_id)
    messages = asked_with(backend, -1)
    assert len(backend.meta.calls) == calls + 1 and spoken(seen).count("Вижу новый экран.") == 1
    assert texts(messages[-1]) == ["Посмотри, что скажешь"] and messages[-1]["content"][-1]["type"] == "image_url"


def test_opening_waits_for_a_frame_that_comes_late(backend, monkeypatch):
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    monkeypatch.setattr(friend, "LOOK_SECONDS", 0.01)
    seen = events_of(backend, session_id)
    backend.meta.replies.append(back())
    words(client, session_id, "Посмотри")
    idle(backend, session_id)
    calls = len(backend.meta.calls)
    time.sleep(0.05)
    assert len(backend.meta.calls) == calls and spoken(seen) == []
    backend.meta.replies.append(line("Вижу."))
    assert look(client, session_id, [SCREENS[2]]).status_code == 200
    idle(backend, session_id)
    assert spoken(seen) == ["Вижу."] and pictures_in(asked_with(backend, -1)) >= 1


def test_opening_with_no_frame_at_all_says_so_once(backend, monkeypatch):
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    monkeypatch.setattr(friend, "SETTLE_SECONDS", 0.01)
    seen = events_of(backend, session_id)
    backend.meta.replies += [back(), line("Экрана нет.")]
    words(client, session_id, "Посмотри")
    idle(backend, session_id)
    time.sleep(0.05)
    idle(backend, session_id)
    assert spoken(seen) == ["Экрана нет."] and last_words(asked_with(backend, -1)) == "Посмотри " + RU["unseen"]


def test_opening_with_no_screen_shared_says_so_once(backend):
    client = backend.client
    session_id = new_session(client)
    backend.meta.replies.append(turned())
    words(client, session_id, "Не смотри")
    idle(backend, session_id)
    played(backend, session_id)
    seen = events_of(backend, session_id)
    backend.meta.replies += [back(), line("Экрана нет.")]
    words(client, session_id, "Посмотри")
    idle(backend, session_id)
    assert spoken(seen) == ["Экрана нет."] and last_words(asked_with(backend, -1)) == "Посмотри " + RU["unseen"]


def test_a_field_written_after_the_line_still_closes(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies.append(line("Не смотрю.", look=False))
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[0]]).status_code == 200
    idle(backend, session_id)
    assert spoken(seen).count("Не смотрю.") == 1 and backend.store.sessions[session_id].not_looking


def test_look_true_means_nothing_with_the_eyes_open(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies.append(back("Вижу."))
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    assert spoken(seen) == ["Вижу."] and "not_looking" not in kinds(seen)


def test_look_false_twice_tells_once(backend):
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    seen = events_of(backend, session_id)
    backend.meta.replies.append(turned())
    words(client, session_id, "Точно не смотри")
    idle(backend, session_id)
    assert "not_looking" not in kinds(seen) and spoken(seen) == ["Не смотрю."]


def test_a_new_call_starts_open_and_a_lost_link_stays_shut(backend):
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    with client.websocket_connect("/sessions/" + session_id + "/events", headers=AUTH) as socket:
        kinds_seen = [socket.receive_json()["type"], socket.receive_json()]
    assert kinds_seen[1] == {"type": "not_looking", "on": True}
    backend.meta.replies.append(back("Вижу."))
    words(client, session_id, "Смотри")
    idle(backend, session_id)
    with client.websocket_connect("/sessions/" + session_id + "/events", headers=AUTH) as socket:
        socket.receive_json()
        assert socket.receive_json() == {"type": "not_looking", "on": False}
    other = sharing(client)
    assert not backend.store.sessions[other].not_looking
    with client.websocket_connect("/sessions/" + other + "/events", headers=AUTH) as socket:
        assert socket.receive_json()["type"] == "status"


def test_a_cut_line_with_the_eyes_shut_asks_again_with_no_frame(backend):
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    session = backend.store.sessions[session_id]
    backend.meta.replies.append(line("Хорошо."))
    words(client, session_id, "Как дела?")
    idle(backend, session_id)
    line_number = max(session.lines)
    backend.meta.replies.append(line("Ещё раз."))
    assert client.post("/sessions/" + session_id + "/spoken", headers=AUTH, json={"line": line_number, "said": ""}).status_code == 200
    idle(backend, session_id)
    assert session.heard == "" and last_words(asked_with(backend, -1)) == "Как дела? " + RU["closed"]


def test_a_screen_gone_by_the_closing_answer_keeps_the_answer(backend):
    client = backend.client
    session_id = sharing(client)
    backend.meta.replies.append(line("Вижу."))
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    played(backend, session_id)
    session = backend.store.sessions[session_id]
    other = session.last_frame
    seen = events_of(backend, session_id)
    backend.meta.hook = lambda index: setattr(session, "last_frame", other)
    backend.meta.replies.append(turned())
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[3]]).status_code == 200
    idle(backend, session_id)
    say_event = next(event for event in seen if event["type"] == "say")
    assert say_event["text_ru"] == "Не смотрю." and say_event["frame_ts"] is None and session.not_looking


def test_a_payment_screen_stays_calm(backend):
    client = backend.client
    session_id = sharing(client, eyes=True)
    backend.meta.replies.append(turned())
    backend.store.sessions[session_id].remember_text(100, PAYMENT_SCREEN)
    seen = events_of(backend, session_id)
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    assert seen[-1]["type"] == "face" and seen[-1]["calm"] is True


def test_the_rule_stays_out_of_the_language_line():
    session = type("S", (), {"user_lang": "es"})
    rules = friend._rules(session)
    assert rules.count("только на языке es,") == 1
    assert rules.index("look:") < rules.index("say:")
