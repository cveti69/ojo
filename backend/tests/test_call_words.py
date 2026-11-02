"""What the model is told at the start of a call, and how one line stays one line on its two roads."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

import pytest

from app import config, friend
from conftest import FakeEars, idle, line, new_session, say, words
from test_friend import blocked, events_of, kinds, played, spoken, wait_until

TRIAL = "Если человек спрашивает, что делать дальше, или ждёт, назови один конкретный шаг на экране, а не вопрос."


def log_of(tmp_path):
    [folder] = list((tmp_path / "calls").iterdir())
    return [json.loads(row) for row in (folder / "call.log").read_text(encoding="utf-8").splitlines()]


def first_system(backend):
    return backend.meta.calls[0].messages[0]["content"]


def by_stream(backend, session_id, text):
    """A line the speech stream brings. It takes the socket to start one, and only the words matter here."""
    async def turn():
        backend.app.state.friend.heard(backend.store.sessions[session_id], text, road="stream")
        await asyncio.sleep(0)
    backend.client.portal.call(turn)


def by_file(backend, session_id, text):
    backend.meta.asr_reply = {"transcript": text, "audioDurationMs": 100, "turns": []}
    assert say(backend.client, session_id).status_code == 200


def ago(backend, session_id, seconds):
    for line in backend.store.sessions[session_id].heard_lines:
        line.at -= seconds


# The trial line.

def test_the_role_and_the_rules_are_those_of_the_last_version_byte_for_byte():
    assert hashlib.sha256(friend.ROLE.encode()).hexdigest() == "f946dfc00d9cbcd2cad94ae6e91e3a9b20d1178eeb5e7b4f5a9076251f17810c"
    assert hashlib.sha256(friend.RULES.encode()).hexdigest() == "341627b4fb830e05c4b5f36cf8326007eabfc65fb728a8a7c493081625b6057c"
    assert len(friend.ROLE) == 798 and len(friend.RULES) == 948


@pytest.mark.parametrize("changes", [{}, {"next_step": False}])
def test_without_the_tick_the_model_is_told_what_it_always_was(backend, changes):
    session_id = new_session(backend.client, **changes)
    words(backend.client, session_id)
    idle(backend, session_id)
    assert first_system(backend) == friend.ROLE + "\n\n" + friend.RULES
    assert TRIAL not in first_system(backend)


def test_with_the_tick_only_the_one_line_is_added(backend):
    session_id = new_session(backend.client, next_step=True)
    words(backend.client, session_id)
    idle(backend, session_id)
    assert friend.NEXT_STEP == "\n" + TRIAL
    assert first_system(backend) == friend.ROLE + "\n" + TRIAL + "\n\n" + friend.RULES


def test_the_line_stands_on_every_turn_of_its_call_and_in_no_other(backend):
    client = backend.client
    trial = new_session(client, next_step=True)
    usual = new_session(client)
    for session_id in (trial, usual, trial):
        words(client, session_id)
        idle(backend, session_id)
        played(backend, session_id, backend.store.sessions[session_id].turn)
    assert [TRIAL in call.messages[0]["content"] for call in backend.meta.calls] == [True, False, True]


def test_the_tone_of_the_call_stays_with_it(backend):
    session_id = new_session(backend.client, next_step=True, character="brief")
    words(backend.client, session_id)
    idle(backend, session_id)
    system = first_system(backend)
    assert system.startswith(friend.ROLE + "\n" + TRIAL) and system.endswith(friend.RULES)
    assert len(system) > len(friend.ROLE + "\n" + TRIAL + "\n\n" + friend.RULES)


@pytest.mark.parametrize("changes, written", [({}, False), ({"next_step": True}, True)])
def test_the_log_of_the_call_says_if_the_trial_was_on(backend, monkeypatch, tmp_path, changes, written):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    new_session(backend.client, **changes)
    assert log_of(tmp_path)[0]["kind"] == "call" and log_of(tmp_path)[0]["next_step"] is written


# One line, two roads. The numbers come from a call where the stream was 0.3 s past the wait and the file took 3.7 s.

STREAM = "Давай выберем шампунь для собаки ей шесть месяцев она пудель"
FILE = "Давай выберем шампунь для собаки, ей шесть месяцев, это пудель."


def test_a_file_that_returns_what_the_stream_brought_in_other_words_is_no_new_turn(backend):
    session_id = new_session(backend.client)
    entered, release = blocked(backend, only=1)
    by_stream(backend, session_id, STREAM)
    assert entered.wait(2)
    by_file(backend, session_id, FILE)
    release.set()
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    assert session.turn == 1 and len(backend.meta.calls) == 1 and session.open_words == STREAM


def test_the_file_stops_no_answer_and_says_it_heard_words(backend):
    session_id = new_session(backend.client)
    seen = events_of(backend, session_id)
    entered, release = blocked(backend, only=1)
    by_stream(backend, session_id, STREAM)
    assert entered.wait(2)
    backend.meta.asr_reply = {"transcript": FILE, "audioDurationMs": 100, "turns": []}
    reply = say(backend.client, session_id)
    assert reply.json()["heard"] is True
    release.set()
    idle(backend, session_id)
    assert kinds(seen) == ["heard", "say", "say"] or kinds(seen) == ["heard", "say"]
    assert spoken(seen)[0]


def test_a_file_after_the_first_sound_is_no_new_turn_either(backend):
    session_id = new_session(backend.client)
    by_stream(backend, session_id, STREAM)
    idle(backend, session_id)
    played(backend, session_id, 1)
    by_file(backend, session_id, FILE)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 1 and len(backend.meta.calls) == 1


def test_the_stream_late_behind_the_file_is_no_new_turn(backend):
    session_id = new_session(backend.client)
    entered, release = blocked(backend, only=1)
    by_file(backend, session_id, FILE)
    assert entered.wait(2)
    by_stream(backend, session_id, STREAM)
    release.set()
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 1 and len(backend.meta.calls) == 1


def test_the_log_says_a_line_came_twice(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = new_session(backend.client)
    by_stream(backend, session_id, STREAM)
    idle(backend, session_id)
    by_file(backend, session_id, FILE)
    assert [row["turn"] for row in log_of(tmp_path) if row["kind"] == "heard twice"] == [1]


def test_their_next_line_by_file_goes_on_the_old_way(backend):
    session_id = new_session(backend.client)
    entered, release = blocked(backend, only=1)
    by_stream(backend, session_id, STREAM)
    assert entered.wait(2)
    by_file(backend, session_id, "И лучше без отдушки, пожалуйста.")
    release.set()
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2
    assert backend.store.sessions[session_id].open_words == STREAM + " И лучше без отдушки, пожалуйста."


def test_a_line_cut_short_by_the_stream_keeps_the_rest_from_the_file(backend):
    session_id = new_session(backend.client)
    entered, release = blocked(backend, only=1)
    by_stream(backend, session_id, "Давай выберем")
    assert entered.wait(2)
    by_file(backend, session_id, STREAM)
    release.set()
    idle(backend, session_id)
    assert "пудель" in backend.store.sessions[session_id].open_words


def test_the_last_word_is_not_lost_with_the_copy(backend):
    session_id = new_session(backend.client)
    entered, release = blocked(backend, only=1)
    by_stream(backend, session_id, "Отправь отчёт Алексею до конца дня")
    assert entered.wait(2)
    by_file(backend, session_id, "Отправь отчёт Алексею до конца дня завтра")
    release.set()
    idle(backend, session_id)
    assert backend.store.sessions[session_id].open_words.endswith("завтра")


def test_a_shorter_copy_with_a_word_of_its_own_is_still_a_copy(backend):
    session_id = new_session(backend.client)
    by_stream(backend, session_id, "Давай выберем шампунь для собаки ей шесть месяцев она пудель и ей нужен мягкий")
    idle(backend, session_id)
    by_file(backend, session_id, "Давай выберем шампунь для собаки ей шесть месяцев это пудель")
    assert backend.store.sessions[session_id].turn == 1


def test_a_file_late_behind_their_next_line_is_still_a_copy(backend):
    session_id = new_session(backend.client)
    by_stream(backend, session_id, STREAM)
    idle(backend, session_id)
    played(backend, session_id, 1)
    by_stream(backend, session_id, "Покажи что-нибудь без отдушки и красителей")
    idle(backend, session_id)
    by_file(backend, session_id, FILE)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2 and len(backend.meta.calls) == 2


def test_a_line_has_one_copy_and_the_same_words_after_it_are_theirs(backend):
    session_id = new_session(backend.client)
    by_file(backend, session_id, FILE)
    idle(backend, session_id)
    played(backend, session_id, 1)
    by_stream(backend, session_id, STREAM)
    assert backend.store.sessions[session_id].turn == 1
    by_stream(backend, session_id, STREAM)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2


def test_a_word_or_two_said_again_is_no_copy(backend):
    session_id = new_session(backend.client)
    by_file(backend, session_id, "Да, нажал")
    idle(backend, session_id)
    played(backend, session_id, 1)
    by_stream(backend, session_id, "Да нажал")
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2


def test_past_the_time_of_the_two_roads_they_said_it_again(backend):
    session_id = new_session(backend.client)
    by_stream(backend, session_id, STREAM)
    idle(backend, session_id)
    played(backend, session_id, 1)
    ago(backend, session_id, friend.TWO_ROADS_SECONDS)
    by_file(backend, session_id, FILE)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2 and len(backend.meta.calls) == 2


def test_one_road_can_say_a_line_twice(backend):
    session_id = new_session(backend.client)
    by_stream(backend, session_id, STREAM)
    idle(backend, session_id)
    played(backend, session_id, 1)
    by_stream(backend, session_id, FILE)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2


def test_the_same_words_after_their_second_road_are_theirs_again(backend):
    session_id = new_session(backend.client)
    by_stream(backend, session_id, STREAM)
    idle(backend, session_id)
    played(backend, session_id, 1)
    by_file(backend, session_id, FILE)
    assert backend.store.sessions[session_id].turn == 1
    ago(backend, session_id, friend.TWO_ROADS_SECONDS)
    by_stream(backend, session_id, FILE)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2


def test_the_first_line_of_a_call_has_nothing_to_be_the_same_as(backend):
    session_id = new_session(backend.client)
    by_file(backend, session_id, STREAM)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 1 and len(backend.meta.calls) == 1


def test_the_stream_itself_takes_this_road_in_the_service(backend):
    """`friend.heard` is what the socket calls, with no word about a file."""
    session_id = new_session(backend.client)
    with backend.client.websocket_connect("/sessions/" + session_id + "/listen", headers={"X-Token": config.SHARED_TOKEN}) as socket:
        socket.send_bytes(b"\1" * 3200)
        assert wait_until(lambda: FakeEars.made)

    async def turn():
        FakeEars.made[0].on_turn(STREAM)
        await asyncio.sleep(0)
    backend.client.portal.call(turn)
    idle(backend, session_id)
    assert [(line.road, line.said) for line in backend.store.sessions[session_id].heard_lines] == [("stream", STREAM)]
    played(backend, session_id, 1)
    by_file(backend, session_id, FILE)
    assert backend.store.sessions[session_id].turn == 1


def test_words_typed_are_not_a_second_road_of_the_file(backend):
    session_id = new_session(backend.client)
    by_stream(backend, session_id, STREAM)
    idle(backend, session_id)
    played(backend, session_id, 1)
    words(backend.client, session_id, FILE)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2


def test_words_typed_after_a_file_are_theirs_too(backend):
    session_id = new_session(backend.client)
    by_file(backend, session_id, FILE)
    idle(backend, session_id)
    played(backend, session_id, 1)
    words(backend.client, session_id, FILE)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2


def test_words_typed_are_no_first_line_for_a_copy(backend):
    session_id = new_session(backend.client)
    words(backend.client, session_id, FILE)
    idle(backend, session_id)
    played(backend, session_id, 1)
    by_stream(backend, session_id, STREAM)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].turn == 2


def test_the_time_of_a_line_is_when_it_was_heard(backend):
    session_id = new_session(backend.client)
    before = time.perf_counter()
    by_stream(backend, session_id, STREAM)
    assert before <= backend.store.sessions[session_id].heard_lines[-1].at <= time.perf_counter()
    idle(backend, session_id)
