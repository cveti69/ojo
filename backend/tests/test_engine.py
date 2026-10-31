from __future__ import annotations

import base64
import io
import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import config, engine, grounding, meta
from app.sessions import KEPT_FRAME_TEXTS
from conftest import AUTH, answer, idle, jpeg, new_session, post_frame


def with_floating_window(color):
    image = Image.open(io.BytesIO(jpeg())).convert("RGB")
    ImageDraw.Draw(image).rectangle((180, 500, 380, 830), fill=color)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def wait_until(condition, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("pending_changed", [True, False])
def test_busy_keeps_only_latest_frame(backend, pending_changed):
    entered, release = threading.Event(), threading.Event()

    def block_first(index):
        if index == 1:
            entered.set()
            assert release.wait(5)
    backend.meta.hook = block_first
    moved = {"label": "Beli", "object": "green button", "box": [30, 500, 970, 570], "mark_id": None}
    backend.meta.answers = [answer(), answer(target=moved, instruction="Выбери шампунь")]
    session_id = new_session(backend.client)
    first = jpeg()
    last = with_floating_window((20, 40, 60)) if pending_changed else first
    try:
        assert post_frame(backend.client, session_id, first).json() == {"accepted": True}
        assert entered.wait(2)
        assert backend.client.get("/health").status_code == 200
        assert post_frame(backend.client, session_id, with_floating_window((200, 30, 30))).json() == {
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


def blocked_first_answer(backend):
    entered, release = threading.Event(), threading.Event()

    def block_first(index):
        if index == 1:
            entered.set()
            assert release.wait(5)
    backend.meta.hook = block_first
    return entered, release


def test_new_screen_replaces_the_step_for_the_old_one(backend):
    entered, release = blocked_first_answer(backend)
    backend.meta.answers = [answer(instruction="Открой приложение"), answer(instruction="Нажми на поиск")]
    session_id = new_session(backend.client)
    try:
        post_frame(backend.client, session_id)
        assert entered.wait(2)
        assert post_frame(backend.client, session_id, jpeg((60, 70, 90))).json()["reason"] == "busy"
        idle(backend, session_id)  # The second answer arrives while the first call is still running.
        assert len(backend.meta.vision_calls) == 2 and backend.meta.active == 1
    finally:
        release.set()
    session = backend.store.sessions[session_id]
    assert [item["instruction_ru"] for item in session.history] == ["Нажми на поиск"]
    assert session.last_hint["instruction_ru"] == "Нажми на поиск" and session.last_hint["step_id"] == "s1"


def test_new_screen_during_the_box_drops_the_hint(backend, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    locate = grounding.locate

    def blocked_locate(*args):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return locate(*args)
    monkeypatch.setattr(grounding, "locate", blocked_locate)
    backend.meta.answers = [answer(instruction="Открой приложение"), answer(instruction="Нажми на поиск")]
    session_id = new_session(backend.client)
    try:
        post_frame(backend.client, session_id)
        assert entered.wait(2)
        post_frame(backend.client, session_id, jpeg((60, 70, 90)))
    finally:
        release.set()
    idle(backend, session_id)
    history = backend.store.sessions[session_id].history
    assert [item["instruction_ru"] for item in history] == ["Нажми на поиск"]


def test_new_payment_screen_is_not_lost_with_the_old_answer(backend):
    entered, release = blocked_first_answer(backend)
    backend.meta.answers = [answer(done=True), answer(needs_user_confirm=True)]
    session_id = new_session(backend.client)
    try:
        post_frame(backend.client, session_id)
        assert entered.wait(2)
        post_frame(backend.client, session_id, jpeg((60, 70, 90)))
        idle(backend, session_id)
    finally:
        release.set()
    session = backend.store.sessions[session_id]
    assert session.needs_user_confirm and not session.completed


def with_keyboard():
    image = Image.open(io.BytesIO(jpeg())).convert("RGB")
    ImageDraw.Draw(image).rectangle((0, 520, 390, 844), fill=(20, 40, 60))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def test_keyboard_counts_as_moving_on(backend):
    entered, release = blocked_first_answer(backend)
    session_id = new_session(backend.client)
    try:
        post_frame(backend.client, session_id)
        assert entered.wait(2)
        post_frame(backend.client, session_id, with_keyboard())
        assert wait_until(lambda: len(backend.meta.vision_calls) == 2)
    finally:
        release.set()
    idle(backend, session_id)


def test_status_tells_the_window_about_a_new_screen(backend):
    session_id = new_session(backend.client)
    statuses = []
    session = backend.store.sessions[session_id]
    publish = session.publish
    session.publish = lambda event: (statuses.append(event), publish(event))[1]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    post_frame(backend.client, session_id, with_floating_window((20, 40, 60)))
    idle(backend, session_id)
    post_frame(backend.client, session_id, jpeg((60, 70, 90)))
    idle(backend, session_id)
    assert [event["new_screen"] for event in statuses if event.get("state") == "analyzing"] == [False, False, True]


def test_our_own_window_does_not_stop_the_answer(backend):
    entered, release = blocked_first_answer(backend)
    session_id = new_session(backend.client)
    try:
        post_frame(backend.client, session_id)
        assert entered.wait(2)
        post_frame(backend.client, session_id, with_floating_window((20, 40, 60)))
        assert len(backend.meta.vision_calls) == 1 and not backend.store.sessions[session_id].stale.is_set()
    finally:
        release.set()
    idle(backend, session_id)
    assert backend.store.sessions[session_id].last_hint["step_id"] == "s1"


def test_screen_that_never_settles_still_gets_a_hint(backend):
    release = threading.Event()

    def block_early(index):
        if index <= engine.MAX_RESTARTS + 1:
            assert release.wait(5)
    backend.meta.hook = block_early
    session_id = new_session(backend.client)
    session = backend.store.sessions[session_id]
    try:
        post_frame(backend.client, session_id)
        for shade in range(engine.MAX_RESTARTS):
            post_frame(backend.client, session_id, jpeg((40 * (shade % 2), 200 - 160 * (shade % 2), 90)))
            assert wait_until(lambda: len(backend.meta.vision_calls) == shade + 2)
        post_frame(backend.client, session_id, jpeg((230, 20, 20)))
        assert session.busy and not session.stale.is_set()
    finally:
        release.set()
    idle(backend, session_id)
    assert session.last_hint is not None and session.restarts == 0


def test_nearly_identical_frames_and_duplicate_steps(backend):
    session_id = new_session(backend.client)
    assert post_frame(backend.client, session_id).json()["accepted"] is True
    idle(backend, session_id)
    assert post_frame(backend.client, session_id).json() == {"accepted": False, "reason": "unchanged"}
    assert post_frame(backend.client, session_id, jpeg((231, 236, 241))).json()["accepted"] is False
    assert len(backend.meta.vision_calls) == 1


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


@pytest.mark.parametrize("eyes,removed", [(False, True), (True, False)])
def test_a_video_on_the_page_keeps_the_hint_with_the_eyes(backend, monkeypatch, eyes, removed):
    monkeypatch.setattr(engine, "changed_share", lambda gray, previous: 0.28)
    session_id = new_session(backend.client, eyes=eyes)
    moved = {"label": "Beli", "object": "green button", "box": [30, 500, 970, 570], "mark_id": None}
    backend.meta.answers = [answer(), answer(target=moved)]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    published = []
    publish = session.publish
    monkeypatch.setattr(session, "publish", lambda event: (published.append(event), publish(event))[1])
    post_frame(backend.client, session_id, jpeg((30, 60, 90)))
    idle(backend, session_id)
    assert [event["new_screen"] for event in published if event.get("state") == "analyzing"][0] is removed


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


def test_new_goal_during_broadcast_reads_the_screen_at_once(backend):
    session_id = new_session(backend.client)
    prefix = "/sessions/" + session_id
    backend.client.post(prefix + "/broadcast", headers=AUTH, json={"state": "started"})
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    calls = len(backend.meta.vision_calls)
    backend.client.post(prefix + "/goal", headers=AUTH, json={"goal": "Найти кондиционер"})
    idle(backend, session_id)
    assert len(backend.meta.vision_calls) == calls + 1
    assert "кондиционер" in str(backend.meta.vision_calls[-1])
    assert backend.store.sessions[session_id].last_status["state"] == "broadcasting"


def test_new_goal_before_broadcast_still_asks_to_start_it(backend):
    session_id = new_session(backend.client)
    backend.client.post("/sessions/" + session_id + "/goal", headers=AUTH, json={"goal": "Найти кондиционер"})
    idle(backend, session_id)
    session = backend.store.sessions[session_id]
    assert session.last_status["text_ru"] == "Включи демонстрацию экрана"
    assert backend.meta.vision_calls == []


def test_call_ended_during_the_goal_reply_reads_no_screen(backend, monkeypatch):
    session_id = new_session(backend.client)
    prefix = "/sessions/" + session_id
    backend.client.post(prefix + "/broadcast", headers=AUTH, json={"state": "started"})
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    calls = len(backend.meta.vision_calls)

    def chat_then_end(*args, **kwargs):
        backend.store.sessions[session_id].finish()
        return backend.meta.chat(*args, **kwargs)
    monkeypatch.setattr(meta, "chat", chat_then_end)
    reply = backend.client.post(prefix + "/goal", headers=AUTH, json={"goal": "Найти кондиционер"})
    assert reply.status_code == 409
    assert len(backend.meta.vision_calls) == calls


def test_status_after_done_does_not_promise_more(backend):
    backend.meta.answers.append(answer(done=True))
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert backend.store.sessions[session_id].last_status["text_ru"] == "Готово, цель достигнута"


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


def lines_of(folder):
    return [json.loads(line) for line in (folder / "call.log").read_text(encoding="utf-8").splitlines()]


def test_a_call_is_kept_only_when_asked_for_and_outside_the_repository(backend, monkeypatch, tmp_path):
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    assert not list(tmp_path.glob("calls/*"))
    monkeypatch.setattr(config, "CALLS_DIR", str(Path(engine.__file__).parents[2] / "calls"))
    new_session(backend.client)
    assert not (Path(engine.__file__).parents[2] / "calls").exists()


def test_the_log_of_a_call_says_what_the_pointer_saw(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    session_id = new_session(backend.client)
    image = jpeg()
    post_frame(backend.client, session_id, image)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    assert (tmp_path / "calls").stat().st_mode & 0o777 == 0o700 and folder.stat().st_mode & 0o777 == 0o700
    assert (folder / "frames").stat().st_mode & 0o777 == 0o700 and (folder / "call.log").stat().st_mode & 0o777 == 0o600
    lines = lines_of(folder)
    assert [line["kind"] for line in lines[:2]] == ["call", "frame"]
    step = next(line for line in lines if line["kind"] == "step")
    assert step["outcome"] == "hint" and step["step"] == "s1" and step["frame"] == "123.jpg" and step["age_ms"] > 0
    assert (folder / "frames" / "123.jpg").read_bytes() == image
    assert (folder / "frames" / "123.jpg").stat().st_mode & 0o777 == 0o600
    assert config.SHARED_TOKEN not in (folder / "call.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("changes", [
    {"needs_user_confirm": True},
    {"instruction": "Введи пароль"},
    {"screen": "Экран оплаты: номер карты"},
])
def test_a_private_screen_is_not_written_down(backend, monkeypatch, tmp_path, changes):
    monkeypatch.setattr(config, "CALLS_DIR", str(tmp_path / "calls"))
    backend.meta.answers = [answer(**changes)]
    session_id = new_session(backend.client)
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    folder = next((tmp_path / "calls").iterdir())
    step = next(line for line in lines_of(folder) if line["kind"] == "step")
    assert step["private"] is True and step["frame"] is None and step["screen"] is None
    assert not list((folder / "frames").iterdir())


def test_debug_escapes_model_text(backend):
    session_id = new_session(backend.client)
    backend.meta.answers = [answer(screen='<script>alert("x")</script>')]
    post_frame(backend.client, session_id)
    idle(backend, session_id)
    page = backend.client.get("/debug/" + session_id, headers=AUTH)
    assert "<script>" not in page.text and "&lt;script&gt;" in page.text


def text_line(*words):
    return {"text": " ".join(words), "words": [
        {"text": word, "box": [0.1 + index * 0.1, 0.8, 0.2 + index * 0.1, 0.85]} for index, word in enumerate(words)
    ]}


@pytest.mark.parametrize("label,words,left", [
    ("Beli Langsung", ("Beli", "Langsung"), 0.1),
    ("立即购买", ("立即", "购买"), 0.1),
    ("加入购物车", ("其他", "加入", "购", "物", "车"), 0.2),
    ("ซื้อเลย", ("ซื้อ", "เลย"), 0.1),
    ("Đặt xe", ("Đặt", "xe"), 0.1),
])
def test_text_match_works_in_any_script(label, words, left):
    box = grounding.snap_to_text(label, [0.1, 0.8, 0.4, 0.85], [text_line(*words)])
    assert box and box[0] == pytest.approx(left)


def test_text_match_keeps_different_chinese_labels_apart():
    assert grounding.snap_to_text("取消订单", [0.1, 0.8, 0.3, 0.85], [text_line("立即", "购买")]) is None


def test_long_guess_stays_off_the_icon_next_to_it():
    icon = [0.49, 0.128, 0.6, 0.177]
    search_bar = [0.295, 0.03, 0.654, 0.047]
    assert grounding.snap_to_icon(search_bar, [icon], 0.462) is None
    assert grounding.snap_to_icon([0.5, 0.13, 0.58, 0.17], [icon], 0.462) == icon
