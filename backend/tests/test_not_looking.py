from __future__ import annotations

import json
import threading
import time

from app import config, friend, language
from conftest import AUTH, Stream, idle, jpeg, line, look, new_session, post_frame, sharing, words
from test_friend import PAYMENT_SCREEN, SCREENS, SHOW, events_of, kinds, spoken, texts, wait_until

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
    assert look(client, session_id, [SCREENS[1]], first_taken=150).status_code == 200
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


CLOSED = {"type": "not_looking", "on": True}


def line_first():
    return Stream('{"say": "Не смотрю."', ', "look": false}')


def field_first():
    return Stream("{", '"look": false, "say": "Не смотрю."}')


def a_new_answer(say="Вижу новый экран."):
    return Stream("", json.dumps(line(say), ensure_ascii=False))


def on_a_screen(backend, tmp_path, monkeypatch):
    """A call with one screen in the log. The phone has seen no change, so their words go on that frame."""
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path))
    client = backend.client
    session_id = sharing(client, signals=True)
    backend.meta.replies.append(line("Вижу экран."))
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    played(backend, session_id)
    assert list(tmp_path.glob("*/frames/100.jpg"))
    return client, session_id, backend.store.sessions[session_id], events_of(backend, session_id)


def moved(client, session_id, at=200):
    assert client.post("/sessions/" + session_id + "/changed", headers=AUTH, json={"at": at, "moved": True}).status_code == 200


def write_on(backend, session_id, stream):
    """The rest of the answer comes, and whatever it set going has run."""
    stream.go.set()
    assert stream.ended.wait(5)
    idle(backend, session_id)


def no_frame_left(session, folder):
    assert session.last_frame is None and session.open_screen is None and session.seen_gray is None
    assert not any(turn.get("frame") for turn in session.chat)
    assert not list(folder.glob("*/frames/*")) and not any(row.get("frame") for row in lines_of(folder))


def test_the_field_shuts_the_eyes_as_it_is_written(backend):
    client = backend.client
    session_id = sharing(client)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    stream = Stream('{"look": false', ', "say": "Не смотрю."}')
    backend.meta.replies.append(stream)
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[0]]).status_code == 200
    assert stream.written.wait(5) and wait_until(lambda: session.not_looking)
    assert post_frame(client, session_id, SCREENS[1], 300).json() == {"accepted": False, "reason": "not_looking"}
    assert spoken(seen) == []
    write_on(backend, session_id, stream)
    assert spoken(seen) == ["Не смотрю."] and seen.count(CLOSED) == 1
    assert kinds(seen).index("not_looking") < kinds(seen).index("say") and len(backend.meta.calls) == 1


def test_a_field_after_the_line_needs_no_second_closing(backend):
    client = backend.client
    session_id = sharing(client, eyes=True)
    seen = events_of(backend, session_id)
    stream = line_first()
    backend.meta.replies.append(stream)
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[0]]).status_code == 200
    assert wait_until(lambda: spoken(seen))
    assert CLOSED not in seen
    write_on(backend, session_id, stream)
    assert seen.count(CLOSED) == 1 and spoken(seen) == ["Не смотрю."] and len(backend.meta.calls) == 1


def test_a_stopped_answer_still_shuts_the_eyes_when_its_line_went_out(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stream = line_first()
    backend.meta.replies.append(stream)
    words(client, session_id, "Не смотри")
    assert wait_until(lambda: spoken(seen))
    moved(client, session_id)
    assert session.answer is None and not session.not_looking
    write_on(backend, session_id, stream)
    assert session.not_looking and seen.count(CLOSED) == 1
    assert spoken(seen) == ["Не смотрю."] and len(backend.meta.calls) == 2
    no_frame_left(session, tmp_path)


def test_the_field_stops_the_answer_a_new_screen_began(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stream, taken_over = line_first(), Stream("", '{"look": false, "say": "Вижу новый экран."}')
    backend.meta.replies += [stream, taken_over]
    words(client, session_id, "Не смотри")
    assert wait_until(lambda: spoken(seen))
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]
    assert taken_over.written.wait(5)
    write_on(backend, session_id, stream)
    assert session.not_looking and session.answer is None
    no_frame_left(session, tmp_path)
    write_on(backend, session_id, taken_over)  # It answered the same words, so it closes too, and that changes nothing.
    assert spoken(seen) == ["Не смотрю."] and seen.count(CLOSED) == 1 and len(backend.meta.calls) == 3
    no_frame_left(session, tmp_path)


def test_their_words_get_one_line_with_no_picture_when_no_frame_came(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stream = field_first()
    backend.meta.replies += [stream, line("Хорошо, не смотрю.")]
    words(client, session_id, "Не смотри")
    assert stream.written.wait(5)
    moved(client, session_id)
    assert session.heard == "Не смотри" and session.settling
    write_on(backend, session_id, stream)
    assert session.not_looking and session.heard == "" and not session.settling
    assert post_frame(client, session_id, SCREENS[2], 300).json() == {"accepted": False, "reason": "not_looking"}
    assert look(client, session_id, [SCREENS[2]], first_taken=400).status_code == 409
    messages = asked_with(backend, -1)
    assert len(backend.meta.calls) == 3 and pictures_in(messages) == 0 and last_words(messages) == "Не смотри " + RU["closed"]
    assert spoken(seen) == ["Хорошо, не смотрю."] and seen.count(CLOSED) == 1
    no_frame_left(session, tmp_path)


def test_a_frame_that_came_in_time_goes_with_the_answer_it_began(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stream, taken_over = field_first(), a_new_answer()
    backend.meta.replies += [stream, taken_over, line("Хорошо, не смотрю.")]
    words(client, session_id, "Не смотри")
    assert stream.written.wait(5)
    moved(client, session_id)
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]  # This one was not in the log, the one before it was.
    assert taken_over.written.wait(5)
    write_on(backend, session_id, stream)
    assert session.not_looking
    messages = asked_with(backend, -1)
    assert len(backend.meta.calls) == 4 and pictures_in(messages) == 0 and last_words(messages) == "Не смотри " + RU["closed"]
    write_on(backend, session_id, taken_over)
    assert spoken(seen) == ["Хорошо, не смотрю."] and seen.count(CLOSED) == 1 and len(backend.meta.calls) == 4
    no_frame_left(session, tmp_path)


def test_a_frame_with_no_signal_ahead_of_it_goes_the_same_way(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stream, taken_over = field_first(), a_new_answer()
    backend.meta.replies += [stream, taken_over, line("Хорошо, не смотрю.")]
    words(client, session_id, "Не смотри")
    assert stream.written.wait(5)
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]
    assert taken_over.written.wait(5)
    write_on(backend, session_id, stream)
    write_on(backend, session_id, taken_over)
    assert spoken(seen) == ["Хорошо, не смотрю."] and pictures_in(asked_with(backend, -1)) == 0
    no_frame_left(session, tmp_path)


def test_the_screen_of_their_words_goes_when_only_the_answer_on_the_next_one_closes(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stopped, taken_over = a_new_answer(), field_first()
    backend.meta.replies += [stopped, taken_over]
    words(client, session_id, "Не смотри")
    assert stopped.written.wait(5)
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]
    assert taken_over.written.wait(5)
    write_on(backend, session_id, taken_over)
    write_on(backend, session_id, stopped)
    assert session.not_looking and spoken(seen) == ["Не смотрю."]
    no_frame_left(session, tmp_path)


def test_the_screen_of_their_words_goes_when_the_phone_saw_the_change_first(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stopped, taken_over = a_new_answer(), field_first()
    backend.meta.replies += [stopped, taken_over]
    words(client, session_id, "Не смотри")
    assert stopped.written.wait(5)
    moved(client, session_id)
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]
    assert taken_over.written.wait(5)
    write_on(backend, session_id, taken_over)
    write_on(backend, session_id, stopped)
    assert session.not_looking and spoken(seen) == ["Не смотрю."]
    no_frame_left(session, tmp_path)


def test_the_screen_stays_the_one_their_words_began_on_when_they_go_on(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    first, second, closing = a_new_answer(), a_new_answer(), field_first()
    backend.meta.replies += [first, second, closing]
    words(client, session_id, "Не")
    assert first.written.wait(5)
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]
    assert second.written.wait(5)
    words(client, session_id, "смотри")
    assert closing.written.wait(5)
    for stream in (closing, first, second):
        write_on(backend, session_id, stream)
    assert session.not_looking and spoken(seen) == ["Не смотрю."]
    no_frame_left(session, tmp_path)


def test_the_screen_that_goes_is_that_of_their_last_words(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    backend.meta.replies += [line("Это первый экран."), line("Вижу второй экран.")]
    words(client, session_id, "Что тут?")
    idle(backend, session_id)
    played(backend, session_id)
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]
    idle(backend, session_id)
    played(backend, session_id)
    assert list(tmp_path.glob("*/frames/300.jpg"))
    stopped, taken_over = a_new_answer(), field_first()
    backend.meta.replies += [stopped, taken_over]
    words(client, session_id, "Не смотри")
    assert stopped.written.wait(5)
    assert post_frame(client, session_id, SCREENS[3], 400).json()["accepted"]
    assert taken_over.written.wait(5)
    write_on(backend, session_id, taken_over)
    write_on(backend, session_id, stopped)
    assert session.not_looking and session.open_screen is None
    assert not list(tmp_path.glob("*/frames/300.jpg")) and not list(tmp_path.glob("*/frames/400.jpg"))
    assert not any(turn.get("frame") and turn["frame"].timestamp_ms == 300 for turn in session.chat)


def test_the_field_stops_an_answer_that_has_spoken_and_asks_no_more(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stream, taken_over = field_first(), Stream('{"say": "Вижу новый экран."', ', "notes": "x"}')
    backend.meta.replies += [stream, taken_over]
    words(client, session_id, "Не смотри")
    assert stream.written.wait(5)
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]
    assert wait_until(lambda: spoken(seen))
    write_on(backend, session_id, stream)
    assert session.not_looking and len(backend.meta.calls) == 3 and spoken(seen) == ["Вижу новый экран."]
    no_frame_left(session, tmp_path)
    write_on(backend, session_id, taken_over)


def test_those_words_inside_a_text_close_nothing(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies.append(line('Здесь написано "look": false.', notes='поле "look": false'))
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    assert not backend.store.sessions[session_id].not_looking and "not_looking" not in kinds(seen) and spoken(seen)


def test_the_last_frame_goes_when_a_look_at_the_same_screen_took_its_place(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path))
    client = backend.client
    session_id = sharing(client)
    backend.meta.replies.append(line("Вижу экран."))
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    played(backend, session_id)
    session = backend.store.sessions[session_id]
    assert session.last_frame.timestamp_ms == 100
    backend.meta.replies.append(turned())
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[0]], first_taken=150).status_code == 200  # The same screen keeps the frame that is there.
    idle(backend, session_id)
    assert session.not_looking
    no_frame_left(session, tmp_path)


def test_every_frame_of_the_turn_goes_not_only_the_first_and_the_last(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stream, last = line_first(), a_new_answer()
    backend.meta.replies += [stream, line("Вижу другой экран."), last]
    words(client, session_id, "Не смотри")
    assert wait_until(lambda: spoken(seen))
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]
    idle(backend, session_id)
    assert list(tmp_path.glob("*/frames/300.jpg"))
    assert post_frame(client, session_id, SCREENS[3], 400).json()["accepted"]
    assert last.written.wait(5)
    write_on(backend, session_id, stream)
    assert session.not_looking
    no_frame_left(session, tmp_path)
    write_on(backend, session_id, last)


def test_a_screen_an_earlier_turn_saw_does_not_come_back_with_the_eyes(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    backend.meta.replies.append(turned())
    words(client, session_id, "Не смотри")  # The screen has not changed, so this turn and the one before it share a frame.
    idle(backend, session_id)
    played(backend, session_id)
    no_frame_left(session, tmp_path)
    backend.meta.replies += [back(), line("Вижу другой экран.")]
    words(client, session_id, "Посмотри")
    idle(backend, session_id)
    assert look(client, session_id, [SCREENS[2]], first_taken=300).status_code == 200
    idle(backend, session_id)
    assert not session.not_looking and pictures_in(asked_with(backend, -1)) == 1


def test_a_frame_with_no_tie_to_its_answer_still_goes(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    session.restarts = friend.MAX_RESTARTS
    stream, taken_over = line_first(), a_new_answer()
    backend.meta.replies += [stream, taken_over, line("Хорошо, не смотрю.")]
    words(client, session_id, "Не смотри")
    assert wait_until(lambda: spoken(seen))
    assert post_frame(client, session_id, SCREENS[2], 300).json()["reason"] == "busy"
    number = max(session.lines)
    assert client.post("/sessions/" + session_id + "/spoken", headers=AUTH, json={"line": number, "said": ""}).status_code == 200
    assert taken_over.written.wait(5)
    write_on(backend, session_id, stream)
    write_on(backend, session_id, taken_over)
    assert session.not_looking and spoken(seen) == ["Не смотрю.", "Хорошо, не смотрю."]
    no_frame_left(session, tmp_path)


def test_a_line_with_no_tie_goes_out_whatever_the_screen_shows(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    session.restarts = friend.MAX_RESTARTS
    stream, rest = a_new_answer("Ответ."), threading.Event()
    backend.meta.after_text = lambda index: rest.wait(5)
    backend.meta.replies.append(stream)
    words(client, session_id, "Что это?")
    assert stream.written.wait(5)
    assert post_frame(client, session_id, SCREENS[2], 300).json()["reason"] == "busy"
    stream.go.set()
    assert wait_until(lambda: spoken(seen)) and session.last_answer["say"] == "Вижу экран."  # The rest of the answer is still to come.
    rest.set()
    idle(backend, session_id)
    assert spoken(seen).count("Ответ.") == 1


def test_a_late_call_of_an_earlier_turn_changes_nothing(backend):
    client = backend.client
    session_id = sharing(client, signals=True)
    backend.meta.replies.append(line("Вижу экран."))
    post_frame(client, session_id, SCREENS[0], 100)
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    stream = field_first()
    backend.meta.replies += [stream, line("Цена сто.")]
    words(client, session_id, "Не смотри")
    assert stream.written.wait(5)
    words(client, session_id, "А сколько стоит?")
    idle(backend, session_id)
    write_on(backend, session_id, stream)
    assert not session.not_looking and "not_looking" not in kinds(seen) and session.last_frame
    assert spoken(seen) == ["Цена сто."]


def test_the_field_with_the_eyes_shut_sends_nothing(backend):
    client = backend.client
    session_id = sharing(client)
    away(backend, client, session_id)
    seen = events_of(backend, session_id)
    stream = field_first()
    backend.meta.replies.append(stream)
    words(client, session_id, "Точно не смотри")
    write_on(backend, session_id, stream)
    assert "not_looking" not in kinds(seen) and spoken(seen) == ["Не смотрю."]


def test_a_second_field_leaves_the_line_on_its_way_alone(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stream, taken_over, reply = field_first(), Stream("", '{"look": false, "say": "Вижу."}'), a_new_answer("Хорошо, не смотрю.")
    backend.meta.replies += [stream, taken_over, reply]
    words(client, session_id, "Не смотри")
    assert stream.written.wait(5)
    assert post_frame(client, session_id, SCREENS[2], 300).json()["accepted"]
    assert taken_over.written.wait(5)
    stream.go.set()
    assert stream.ended.wait(5) and reply.written.wait(5)
    taken_over.go.set()
    assert taken_over.ended.wait(5)
    write_on(backend, session_id, reply)
    assert spoken(seen) == ["Хорошо, не смотрю."] and seen.count(CLOSED) == 1 and len(backend.meta.calls) == 4


def test_a_field_in_the_moment_of_the_stop_finds_the_answer_stopped(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    stream = field_first()
    backend.meta.replies += [stream, line("Хорошо, не смотрю.")]
    words(client, session_id, "Не смотри")
    assert stream.written.wait(5)
    session.shown = True
    publish = session.publish

    def in_that_moment(event):  # The field reaches the loop after this event and ahead of the stop that follows it.
        publish(event)
        if event["type"] == "left":
            stream.go.set()
            assert stream.ended.wait(5)
    session.publish = in_that_moment
    moved(client, session_id)
    idle(backend, session_id)
    assert session.not_looking and session.heard == "" and spoken(seen) == ["Хорошо, не смотрю."]
    no_frame_left(session, tmp_path)


def test_a_field_after_the_end_of_the_call_changes_nothing(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    stream = field_first()
    backend.meta.replies.append(stream)
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[0]]).status_code == 200
    assert stream.written.wait(5)
    assert client.post("/sessions/" + session_id + "/end", headers=AUTH).status_code == 200
    write_on(backend, session_id, stream)
    assert "not_looking" not in kinds(seen) and len(backend.meta.calls) == 1


def test_a_call_that_lost_can_still_close_and_takes_no_frame(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    client = backend.client
    session_id = sharing(client)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    main, spare = Stream("", '{"say": "Вижу."}'), field_first()
    backend.meta.replies += [main, spare, line("Хорошо, не смотрю."), line("Хорошо, не смотрю.")]  # By then the two start together.
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[0]]).status_code == 200
    assert main.written.wait(5) and spare.written.wait(5)
    write_on(backend, session_id, main)
    assert spoken(seen) == ["Вижу."]
    number = max(session.lines)
    assert client.post("/sessions/" + session_id + "/spoken", headers=AUTH, json={"line": number, "said": ""}).status_code == 200
    assert session.heard == "Не смотри"
    write_on(backend, session_id, spare)
    assert session.not_looking and session.heard == ""
    assert look(client, session_id, [SCREENS[1]], first_taken=400).status_code == 409
    assert spoken(seen) == ["Вижу.", "Хорошо, не смотрю."] and pictures_in(asked_with(backend, -1)) == 0


def test_the_spare_model_gets_no_picture_after_the_field(backend, monkeypatch):
    monkeypatch.setattr(friend, "SPARE_SECONDS", 0.05)
    client = backend.client
    session_id = sharing(client)
    session = backend.store.sessions[session_id]
    seen = events_of(backend, session_id)
    main = Stream('{"look": false', ', "say": "Главный."}')
    backend.meta.replies += [main, line("Запасной.")]
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[0]]).status_code == 200
    assert main.written.wait(5) and wait_until(lambda: len(backend.meta.calls) == 2)
    assert pictures_in(asked_with(backend, 0)) == 1 and pictures_in(asked_with(backend, 1)) == 0
    assert wait_until(lambda: session.answer.done())
    write_on(backend, session_id, main)
    assert spoken(seen) == ["Запасной."] and seen.count(CLOSED) == 1


def test_a_field_the_stream_missed_closes_with_the_answer(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    backend.meta.replies.append('{"say": "Не смотрю.", "lo\\u006fk": false}')
    words(client, session_id, "Не смотри")
    idle(backend, session_id)
    assert session.not_looking and seen.count(CLOSED) == 1
    no_frame_left(session, tmp_path)


def test_eyes_shut_by_a_field_keep_nothing_whatever_the_answer_says(backend, tmp_path, monkeypatch):
    client, session_id, session, seen = on_a_screen(backend, tmp_path, monkeypatch)
    backend.meta.replies.append(line("Нажми сюда.", show=SHOW, notes={"look": False}))
    words(client, session_id, "Куда нажать?")
    idle(backend, session_id)
    assert session.not_looking and "hint" not in kinds(seen)
    no_frame_left(session, tmp_path)


def test_the_turn_that_closed_cannot_open(backend):
    client = backend.client
    session_id = sharing(client)
    seen = events_of(backend, session_id)
    backend.meta.replies.append('{"look": false, "say": "Не смотрю.", "look": true}')
    words(client, session_id, "Не смотри")
    assert look(client, session_id, [SCREENS[0]]).status_code == 200
    idle(backend, session_id)
    assert backend.store.sessions[session_id].not_looking and seen.count(CLOSED) == 1
    assert {"type": "not_looking", "on": False} not in seen and spoken(seen) == ["Не смотрю."]


def test_the_rule_stays_out_of_the_language_line():
    session = type("S", (), {"user_lang": "es"})
    rules = friend._rules(session)
    assert rules.count("только на языке es,") == 1
    assert rules.index("look:") < rules.index("say:")
