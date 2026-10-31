from __future__ import annotations

import base64
import io
import threading

import pytest
from PIL import Image, ImageDraw

from app import config, grounding, meta
from app.sessions import KEPT_FRAME_TEXTS
from conftest import AUTH, answer, idle, jpeg, new_session, post_frame


@pytest.mark.parametrize("pending_changed", [True, False])
def test_busy_keeps_only_latest_frame(backend, pending_changed):
    entered, release = threading.Event(), threading.Event()

    def block_first(index):
        if index == 1:
            entered.set()
            assert release.wait(5)
    backend.meta.hook = block_first
    backend.meta.answers = [answer(), answer(screen="Результаты поиска", instruction="Выбери шампунь")]
    session_id = new_session(backend.client)
    first = jpeg()
    last = jpeg((60, 70, 90)) if pending_changed else first
    try:
        assert post_frame(backend.client, session_id, first).json() == {"accepted": True}
        assert entered.wait(2)
        assert backend.client.get("/health").status_code == 200
        assert post_frame(backend.client, session_id, jpeg((100, 100, 100))).json() == {
            "accepted": False, "reason": "busy"
        }
        assert post_frame(backend.client, session_id, last).json() == {"accepted": False, "reason": "busy"}
        assert backend.meta.active == 1 and backend.meta.max_active == 1
    finally:
        release.set()
    idle(backend, session_id)
    assert backend.meta.max_active == 1
    assert len(backend.meta.vision_calls) == (2 if pending_changed else 1)
    if pending_changed:
        sent = backend.meta.vision_calls[-1][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
        assert base64.b64decode(sent) == last
        assert backend.store.sessions[session_id].step_id == "s2"


def test_nearly_identical_frames_and_duplicate_steps(backend):
    session_id = new_session(backend.client)
    assert post_frame(backend.client, session_id).json()["accepted"] is True
    idle(backend, session_id)
    assert post_frame(backend.client, session_id).json() == {"accepted": False, "reason": "unchanged"}
    assert post_frame(backend.client, session_id, jpeg((231, 236, 241))).json()["accepted"] is False
    assert len(backend.meta.vision_calls) == 1


def with_floating_window(color):
    image = Image.open(io.BytesIO(jpeg())).convert("RGB")
    ImageDraw.Draw(image).rectangle((180, 500, 380, 830), fill=color)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def test_our_own_window_does_not_repeat_the_step(backend):
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    reworded = answer(screen="Другими словами", instruction="Открой поиск")
    backend.meta.answers.append(reworded)
    assert post_frame(backend.client, session_id, with_floating_window((20, 40, 60))).json()["accepted"] is True
    idle(backend, session_id)
    assert len(backend.meta.vision_calls) == 2
    assert len(backend.store.sessions[session_id].history) == 1
    assert backend.client.get("/sessions/" + session_id + "/hint?after=s1", headers=AUTH).status_code == 204


def test_same_step_on_a_new_screen_gets_a_new_hint(backend):
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert post_frame(backend.client, session_id, jpeg((80, 90, 100))).json()["accepted"] is True
    idle(backend, session_id)
    assert len(backend.store.sessions[session_id].history) == 2


def test_another_target_on_the_same_screen_gets_a_new_hint(backend):
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    moved = answer(target={"label": "Beli", "object": "green button", "box": [30, 500, 970, 570], "mark_id": None})
    backend.meta.answers.append(moved)
    post_frame(backend.client, session_id, with_floating_window((20, 40, 60)))
    idle(backend, session_id)
    assert len(backend.store.sessions[session_id].history) == 2


def test_typed_text_reaches_the_model_as_history(backend):
    session_id = new_session(backend.client, text_entry="clipboard")
    backend.meta.answers.append(answer(action="type"))
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    backend.meta.answers.append(answer(instruction="Нажми Cari"))
    post_frame(backend.client, session_id, jpeg((80, 90, 100)))
    idle(backend, session_id)
    assert 'typed "sampo anti ketombe" into the field' in str(backend.meta.vision_calls[-1])


def test_model_retries_bad_json_once(backend):
    backend.meta.answers = ["bad JSON", answer()]
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert len(backend.meta.vision_calls) == 2
    assert "rejected" in backend.meta.vision_calls[1][0]["content"][0]["text"]
    assert backend.store.sessions[session_id].last_hint is not None


def test_bad_json_twice_produces_error_and_allows_retry(backend):
    backend.meta.answers = ["bad JSON", "still bad"]
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    assert len(backend.meta.vision_calls) == 2
    assert session.last_status["state"] == "error" and session.last_hint is None
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert session.last_hint is not None


def test_other_session_runs_while_model_is_blocked(backend):
    entered, release = threading.Event(), threading.Event()

    def block_first(index):
        if index == 1:
            entered.set()
            assert release.wait(5)
    backend.meta.hook = block_first
    first, second = new_session(backend.client), new_session(backend.client)
    try:
        post_frame(backend.client, first)
        assert entered.wait(2)
        post_frame(backend.client, second)
        idle(backend, second)
        assert backend.store.sessions[second].last_hint is not None
        assert backend.store.sessions[first].busy
    finally:
        release.set()
    idle(backend, first)


def test_grounding_does_not_block_event_loop(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def locate(*args):
        entered.set()
        assert release.wait(5)
        return {"box": [0.1, 0.1, 0.9, 0.2], "source": "test-sam", "ms": 7}
    monkeypatch.setattr(grounding, "locate", locate)
    session_id = new_session(backend.client)
    try:
        post_frame(backend.client, session_id)
        assert entered.wait(2)
        assert backend.client.get("/health").status_code == 200
        assert post_frame(backend.client, session_id).json()["reason"] == "busy"
    finally:
        release.set()
    idle(backend, session_id)
    assert backend.store.sessions[session_id].grounding["source"] == "test-sam"


def test_failed_grounding_allows_same_frame_to_be_retried(backend, monkeypatch):
    calls = []

    def locate(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("Temporary failure")
        return {"box": None, "source": "test", "ms": 0}
    monkeypatch.setattr(grounding, "locate", locate)
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].last_status["state"] == "error"
    assert post_frame(backend.client, session_id).json()["accepted"] is True
    idle(backend, session_id)
    assert backend.store.sessions[session_id].last_hint is not None


def test_new_goal_discards_inflight_result(backend):
    entered, release = threading.Event(), threading.Event()

    def block_first(index):
        if index == 1:
            entered.set()
            assert release.wait(5)
    backend.meta.hook = block_first
    backend.meta.answers = [answer(instruction="Старая цель"), answer(instruction="Новая цель")]
    session_id = new_session(backend.client)
    try:
        post_frame(backend.client, session_id)
        assert entered.wait(2)
        response = backend.client.post("/sessions/" + session_id + "/goal", headers=AUTH,
                                       json={"goal": "Помоги написать водителю"})
        assert response.status_code == 200
        assert post_frame(backend.client, session_id).json()["reason"] == "busy"
    finally:
        release.set()
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    assert len(session.history) == 1 and session.last_hint["instruction_ru"] == "Новая цель"
    assert backend.meta.max_active == 1


def test_end_discards_inflight_result(backend):
    entered, release = threading.Event(), threading.Event()

    def block(index):
        entered.set()
        assert release.wait(5)
    backend.meta.hook = block
    session_id = new_session(backend.client)
    try:
        post_frame(backend.client, session_id)
        assert entered.wait(2)
        assert backend.client.post("/sessions/" + session_id + "/end", headers=AUTH).status_code == 200
    finally:
        release.set()
    idle(backend, session_id)
    assert backend.store.sessions[session_id].last_hint is None


def test_screen_text_and_icons_reach_locate(backend, monkeypatch):
    calls = []

    def locate(image, width, height, model_answer, lines=None, icons=None):
        calls.append((lines, icons))
        return {"box": None, "source": "test", "ms": 1}
    monkeypatch.setattr(grounding, "locate", locate)
    monkeypatch.setattr(grounding, "find_icons", lambda image, width, height: [[0.1, 0.1, 0.2, 0.2]])
    session_id = new_session(backend.client)
    lines = [{"text": "Cari", "words": [{"text": "Cari", "box": [0.1, 0.1, 0.2, 0.12]}]}]
    reply = backend.client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 77, "lines": lines})
    assert reply.status_code == 200
    assert post_frame(backend.client, session_id, timestamp=77).json()["accepted"] is True
    idle(backend, session_id)
    assert calls == [(lines, [[0.1, 0.1, 0.2, 0.2]])]


def test_failed_icon_search_does_not_cost_the_hint(backend, monkeypatch):
    def find_icons(image, width, height):
        raise meta.MetaError(500, "нет")
    monkeypatch.setattr(grounding, "find_icons", find_icons)
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].last_hint is not None


def test_only_recent_screen_texts_are_kept(backend):
    session_id = new_session(backend.client)
    session = backend.store.sessions[session_id]
    for timestamp in range(12):
        session.remember_text(timestamp, [])
    assert len(session.frame_text) == KEPT_FRAME_TEXTS and 11 in session.frame_text


def test_frame_storage_is_opt_in_and_outside_repository(backend, monkeypatch):
    first = new_session(backend.client)
    post_frame(backend.client, first)
    idle(backend, first)
    assert not config.FRAMES_DIR.exists()
    monkeypatch.setattr(config, "SAVE_FRAMES", True)
    second = new_session(backend.client)
    image = jpeg()
    post_frame(backend.client, second, image)
    idle(backend, second)
    saved = list(config.FRAMES_DIR.glob("*.jpg"))
    assert len(saved) == 1 and saved[0].read_bytes() == image


def test_debug_escapes_model_text(backend):
    session_id = new_session(backend.client)
    backend.meta.answers = [answer(screen='<script>alert("x")</script>')]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    page = backend.client.get("/debug/" + session_id, headers=AUTH)
    assert "<script>" not in page.text and "&lt;script&gt;" in page.text
