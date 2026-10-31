from __future__ import annotations

import pytest

from app import grounding, meta
from app.sessions import KEPT_FRAME_TEXTS
from conftest import AUTH, new_session, real_locate


def test_the_words_say_which_row_a_radio_button_is_in(monkeypatch):
    lines = [{"text": "Next Day", "words": [{"text": "Next", "box": [0.05, 0.75, 0.1, 0.77]}, {"text": "Day", "box": [0.11, 0.75, 0.16, 0.77]}]},
             {"text": "Reguler", "words": [{"text": "Reguler", "box": [0.05, 0.82, 0.2, 0.84]}]}]
    radios = [[0.9, 0.74, 0.95, 0.78], [0.9, 0.81, 0.95, 0.85]]
    monkeypatch.setattr(meta, "sam", lambda image, phrase: {"boxes": [{"box": [value * 1000 for value in box]} for box in radios], "ms": 1})
    target = {"label": "Reguler", "object": "radio button", "box": [900, 745, 950, 775], "mark_id": None}  # A row too high.
    found = real_locate(b"", 1000, 1000, {"action": "tap", "target": target}, lines, [])
    assert found["box"] == radios[1] and found["source"] == "sam"
    head = {**target, "words_first": True}
    assert real_locate(b"", 1000, 1000, {"action": "tap", "target": head}, lines, [])["source"] == "text"  # No time for SAM.
    monkeypatch.setattr(meta, "sam", lambda image, phrase: {"boxes": [], "ms": 1})
    found = real_locate(b"", 1000, 1000, {"action": "tap", "target": target}, lines, [])
    assert found["box"] == [0.05, 0.82, 0.2, 0.84] and found["source"] == "text"


def test_only_recent_screen_texts_are_kept(backend):
    session_id = new_session(backend.client)
    session = backend.store.sessions[session_id]
    for timestamp in range(12):
        session.remember_text(timestamp, [])
    assert len(session.frame_text) == KEPT_FRAME_TEXTS and 11 in session.frame_text


def test_screen_text_comes_through_the_phone(backend):
    session_id = new_session(backend.client)
    lines = [{"text": "Cari", "words": [{"text": "Cari", "box": [0.1, 0.1, 0.2, 0.12]}]}]
    reply = backend.client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 77, "lines": lines})
    assert reply.status_code == 200
    assert backend.store.sessions[session_id].frame_text[77] == lines


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
