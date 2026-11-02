from __future__ import annotations

import pytest

from app import grounding, meta
from app.sessions import KEPT_FRAME_TEXTS
from conftest import AUTH, new_session, real_locate


def asked_sam(monkeypatch, answers):
    asked = []

    def sam(image, phrase):
        asked.append(phrase)
        if isinstance(answers.get(phrase), Exception):
            raise answers[phrase]
        return {"boxes": [{"box": box} for box in answers.get(phrase, [])], "ms": 1}
    monkeypatch.setattr(meta, "sam", sam)
    return asked


NAMELESS = {"label": "", "object": "camera icon in the search bar", "box": [500, 140, 560, 180], "mark_id": None}


def test_sam_looks_for_icons_when_the_name_finds_nothing(monkeypatch):
    asked = asked_sam(monkeypatch, {grounding.ICON_PHRASE: [[480, 130, 520, 170]]})
    found = real_locate(b"", 1000, 1000, {"action": "tap", "target": NAMELESS}, [])
    assert found["box"] == [0.48, 0.13, 0.52, 0.17] and found["source"] == "icon"
    assert asked == [NAMELESS["object"], grounding.ICON_PHRASE]


def test_a_name_found_needs_no_search_for_icons(monkeypatch):
    asked = asked_sam(monkeypatch, {NAMELESS["object"]: [[500, 150, 540, 190]], grounding.ICON_PHRASE: [[480, 130, 520, 170]]})
    found = real_locate(b"", 1000, 1000, {"action": "tap", "target": NAMELESS}, [])
    assert found["source"] == "sam" and asked == [NAMELESS["object"]]


def test_a_long_thin_guess_is_no_icon(monkeypatch):
    asked = asked_sam(monkeypatch, {grounding.ICON_PHRASE: [[480, 130, 520, 170]]})
    target = {**NAMELESS, "box": [100, 140, 700, 180]}
    assert real_locate(b"", 1000, 1000, {"action": "tap", "target": target}, [])["source"] == "model"
    assert asked == [NAMELESS["object"]]


def test_the_outline_goes_out_when_the_search_for_icons_fails(monkeypatch):
    asked_sam(monkeypatch, {grounding.ICON_PHRASE: meta.MetaError(502, "down")})
    found = real_locate(b"", 1000, 1000, {"action": "tap", "target": NAMELESS}, [])
    assert found["source"] == "model" and found["box"] == [0.5, 0.14, 0.56, 0.18]


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


@pytest.mark.parametrize("right,source", [(960, "model"), (300, "text")])
def test_words_far_from_the_model_box_are_another_place(monkeypatch, right, source):
    asked_sam(monkeypatch, {})
    lines = [{"text": "搜索", "words": [{"text": "搜索", "box": [0.1, 0.13, 0.16, 0.14]}]}]
    target = {"label": "搜索", "object": "", "box": [right - 130, 106, right, 140], "mark_id": None}
    found = real_locate(b"", 1000, 1000, {"action": "tap", "target": target}, lines, [])
    assert found["source"] == source


def line_of(*words):
    return {"text": " ".join(text for text, _ in words), "words": [{"text": text, "box": box} for text, box in words]}


GENERAL = [line_of(("такие", [0.093, 0.314, 0.18, 0.327]), ("как", [0.19, 0.314, 0.25, 0.327]), ("обновление", [0.267, 0.311, 0.5, 0.331]), ("ПО", [0.51, 0.311, 0.566, 0.331])),
           line_of(("Обновление", [0.184, 0.478, 0.42, 0.492]), ("ПО", [0.43, 0.478, 0.473, 0.492]))]
CAMERA = [line_of(("Камера", [0.185, top, 0.319, bottom])) for top, bottom in ((0.122, 0.139), (0.176, 0.192), (0.277, 0.29))] + [
    line_of(("Передняя", [0.186, 0.252, 0.36, 0.268]), ("камера", [0.366, 0.253, 0.505, 0.268]), ("зеркально", [0.51, 0.252, 0.701, 0.268]))]


@pytest.mark.parametrize("label,near,lines,row", [
    ("Обновление ПО", [0.046, 0.434, 0.952, 0.494], GENERAL, 0.478),
    ("Обновление ПО", [0.046, 0.429, 0.952, 0.494], GENERAL, 0.478),
    ("Камера", [0.046, 0.096, 0.954, 0.156], CAMERA, 0.122),
])
def test_a_row_keeps_its_name_when_the_same_word_stands_nearer_its_middle(label, near, lines, row):
    assert grounding.snap_to_text(label, near, lines)[1] == row


def test_the_same_words_twice_in_the_box_are_the_ones_nearer_its_middle():
    lines = [line_of(("Beli", [0.1, 0.5, 0.2, 0.52])), line_of(("Beli", [0.4, 0.52, 0.5, 0.54]))]
    assert grounding.snap_to_text("Beli", [0.05, 0.45, 0.95, 0.6], lines)[0] == pytest.approx(0.4)


def test_words_past_the_reach_stay_another_place_however_near_the_edge_of_the_box():
    assert grounding.snap_to_text("Cari", [0.49, 0.09, 0.86, 0.12], [line_of(("Cari", [0.1, 0.13, 0.26, 0.14]))]) is None


def test_words_inside_a_wide_box_are_not_past_the_reach():
    assert grounding.snap_to_text("Cari", [0.02, 0.82, 0.98, 0.89], [line_of(("Cari", [0.03, 0.85, 0.09, 0.86]))])


def test_a_label_found_anywhere_needs_no_model_box():
    assert grounding.snap_to_text("Beli", None, [text_line("Beli")])


@pytest.mark.parametrize("gap", [{"text": " ", "box": [0, 0, 0, 0]}, {"text": "", "box": [0.1, 0.2, 0.3, 0.4]},
                                 {"text": "RUB", "box": [0, 0, 0, 0]}, {"text": "RUB", "box": [0.5, 0.2, 0.5, 0.4]}])
def test_a_gap_read_as_a_word_stays_out_of_the_outline(backend, gap):
    session_id = new_session(backend.client)
    rouble = {"text": "俄罗斯卢布", "box": [0.5, 0.2, 0.7, 0.25]}
    lines = [{"text": "俄罗斯卢布 RUB", "words": [rouble, gap]}]
    reply = backend.client.post("/sessions/" + session_id + "/text", headers=AUTH, json={"frame_ts": 77, "lines": lines})
    assert reply.status_code == 200
    kept = backend.store.sessions[session_id].frame_text[77]
    assert kept == [{"text": "俄罗斯卢布 RUB", "words": [rouble]}]
    assert grounding.snap_to_text("俄罗斯卢布", [0.5, 0.2, 0.7, 0.25], kept) == rouble["box"]


def test_text_match_keeps_different_chinese_labels_apart():
    assert grounding.snap_to_text("取消订单", [0.1, 0.8, 0.3, 0.85], [text_line("立即", "购买")]) is None


def test_long_guess_stays_off_the_icon_next_to_it():
    icon = [0.49, 0.128, 0.6, 0.177]
    search_bar = [0.295, 0.03, 0.654, 0.047]
    assert grounding.snap_to_icon(search_bar, [icon], 0.462) is None
    assert grounding.snap_to_icon([0.5, 0.13, 0.58, 0.17], [icon], 0.462) == icon


def test_an_icon_in_another_row_is_left_alone():
    guess = [0.632, 0.148, 0.72, 0.18]
    above, under = [0.68, 0.079, 0.72, 0.096], [0.69, 0.15, 0.73, 0.18]
    assert grounding.snap_to_icon(guess, [above], 0.46) is None
    assert grounding.snap_to_icon(guess, [above, under], 0.46) == under


def test_an_icon_of_the_next_button_in_the_row_is_left_alone():
    assert grounding.snap_to_icon([0.816, 0.135, 0.898, 0.182], [[0.924, 0.171, 0.952, 0.183]], 0.46) is None


@pytest.mark.parametrize("gap,kept", [(0.25, True), (0.45, True), (0.75, False)])
def test_the_row_ends_between_the_right_icons_and_the_next_row(gap, kept):
    guess = [0.5, 0.14, 0.56, 0.18]
    top = guess[3] + gap * (guess[3] - guess[1])
    icon = [0.5, top, 0.54, top + 0.02]
    assert grounding.snap_to_icon(guess, [icon], 0.46) == (icon if kept else None)


def test_the_closest_icon_under_the_guess_wins():
    close, far = [0.52, 0.15, 0.54, 0.17], [0.5, 0.14, 0.51, 0.15]
    assert grounding.snap_to_icon([0.5, 0.14, 0.56, 0.18], [far, close], 0.46) == close


def test_an_icon_too_far_from_the_middle_of_the_guess_is_left_alone():
    assert grounding.snap_to_icon([0.1, 0.5, 0.39, 0.6], [[0.38, 0.5, 0.39, 0.51]], 0.46) is None


def test_a_large_guess_is_no_icon():
    assert grounding.snap_to_icon([0.2, 0.2, 0.6, 0.6], [[0.38, 0.38, 0.42, 0.42]], 1) is None


TITLE = "Serum Rambut Hitam Alami Ukuran Besar Isi Tiga Botol"
WRAPPED = [
    {"text": "Serum Rambut Hitam Alami", "words": [{"text": word, "box": [0.05 + i * 0.1, 0.5, 0.14 + i * 0.1, 0.52]}
                                                   for i, word in enumerate("Serum Rambut Hitam Alami".split())]},
    {"text": "Rak Sepatu Lipat Besar", "words": [{"text": word, "box": [0.55 + i * 0.1, 0.525, 0.64 + i * 0.1, 0.545]}
                                                 for i, word in enumerate("Rak Sepatu Lipat Besar".split())]},
    {"text": "Ukuran Besar Isi Tiga Botol", "words": [{"text": word, "box": [0.05 + i * 0.08, 0.525, 0.12 + i * 0.08, 0.545]}
                                                      for i, word in enumerate("Ukuran Besar Isi Tiga Botol".split())]},
]


def test_a_title_on_two_rows_is_found_whole():
    assert grounding.snap_to_text(TITLE, [0.05, 0.3, 0.45, 0.65], WRAPPED) == pytest.approx([0.05, 0.5, 0.44, 0.545])


def test_lines_of_the_next_column_do_not_join():
    assert grounding.snap_to_text("Serum Rambut Hitam Alami Rak Sepatu Lipat Besar", [0.05, 0.3, 0.95, 0.65], WRAPPED) is None


def test_only_the_line_right_under_another_goes_on_with_it():
    apart = [WRAPPED[0], {**WRAPPED[2], "words": [{**word, "box": [word["box"][0], 0.56, word["box"][2], 0.58]} for word in WRAPPED[2]["words"]]}]
    assert grounding.snap_to_text(TITLE, [0.05, 0.3, 0.45, 0.65], apart) is None


def test_a_line_does_not_go_on_with_the_one_above_it():
    assert grounding.snap_to_text("Ukuran Besar Isi Tiga Botol Serum Rambut Hitam Alami", [0.05, 0.3, 0.45, 0.65], WRAPPED) is None


def test_a_line_does_not_go_on_with_itself():
    assert grounding.snap_to_text("Alami Alami", [0.05, 0.3, 0.45, 0.65], [text_line("Alami")]) is None


def test_a_label_on_one_line_stays_there_when_a_longer_reading_exists():
    assert grounding.snap_to_text("Serum Rambut Hitam Alami", [0.05, 0.3, 0.45, 0.65], WRAPPED) == pytest.approx([0.05, 0.5, 0.44, 0.52])
    whole = {"text": "Lihat Semua", "words": [{"text": "Lihat", "box": [0.1, 0.2, 0.2, 0.22]}, {"text": "Semua", "box": [0.2, 0.2, 0.3, 0.22]}]}
    upper = {"text": "Lihat", "words": [{"text": "Lihat", "box": [0.1, 0.5, 0.2, 0.52]}]}
    lower = {"text": "Semua", "words": [{"text": "Semua", "box": [0.1, 0.525, 0.2, 0.545]}]}
    assert grounding.snap_to_text("Lihat Semua", [0.1, 0.5, 0.2, 0.545], [upper, lower, whole]) == pytest.approx([0.1, 0.2, 0.3, 0.22])


def test_a_long_title_with_no_ascii_letters_is_found_too():
    words = "Шампунь для волос мягкий с травами и маслами для ежедневного ухода".split()
    placed = [{"text": word, "box": [0.05 + i * 0.05, 0.5, 0.09 + i * 0.05, 0.52]} for i, word in enumerate(words[:6])]
    placed2 = [{"text": word, "box": [0.05 + i * 0.05, 0.525, 0.09 + i * 0.05, 0.545]} for i, word in enumerate(words[6:])]
    lines = [{"text": " ".join(w["text"] for w in row), "words": row} for row in (placed, placed2)]
    assert grounding.snap_to_text(" ".join(words), [0.05, 0.4, 0.45, 0.6], lines) == pytest.approx([0.05, 0.5, 0.34, 0.545])


CLOSE = {"label": "Detail produk", "object": "close button", "box": [55, 139, 83, 163], "mark_id": None}
HEADING = [{"text": "Detail produk", "words": [{"text": "Detail", "box": [0.09, 0.17, 0.2, 0.19]}, {"text": "produk", "box": [0.21, 0.17, 0.365, 0.19]}]}]


def test_words_beside_an_icon_do_not_win_over_it(monkeypatch):
    asked = asked_sam(monkeypatch, {"close button": [[50, 140, 90, 165]]})
    found = real_locate(b"", 1000, 1000, {"action": "tap", "target": CLOSE}, HEADING)
    assert found["source"] == "sam" and found["box"] == [0.05, 0.14, 0.09, 0.165] and asked == ["close button"]


def test_with_nothing_else_found_the_model_keeps_its_icon(monkeypatch):
    asked_sam(monkeypatch, {})
    found = real_locate(b"", 1000, 1000, {"action": "tap", "target": CLOSE}, HEADING, [])
    assert found["source"] == "model" and found["box"] == [0.055, 0.139, 0.083, 0.163]


@pytest.mark.parametrize("box", [[100, 170, 300, 190], [130, 100, 200, 150], [120, 200, 180, 230], [100, 160, 200, 200], [55, 100, 83, 124]])
def test_words_under_or_right_by_the_model_box_win(monkeypatch, box):
    asked = asked_sam(monkeypatch, {})
    found = real_locate(b"", 1000, 1000, {"action": "tap", "target": {**CLOSE, "box": box}}, HEADING)
    assert found["source"] == "text" and asked == []


def test_small_words_inside_the_model_box_win(monkeypatch):
    asked = asked_sam(monkeypatch, {})
    lines = [{"text": "OK", "words": [{"text": "OK", "box": [0.345, 0.51, 0.358, 0.53]}]}]
    target = {**CLOSE, "label": "OK", "box": [300, 500, 360, 540]}
    assert real_locate(b"", 1000, 1000, {"action": "tap", "target": target}, lines)["source"] == "text" and asked == []


def test_the_shape_of_the_frame_decides_what_looks_like_an_icon(monkeypatch):
    asked_sam(monkeypatch, {})
    lines = [{"text": "Buka", "words": [{"text": "Buka", "box": [0.3, 0.5, 0.4, 0.52]}]}]
    answer = {"action": "tap", "target": {"label": "Buka", "object": "", "box": [100, 500, 200, 520], "mark_id": None}}
    assert real_locate(b"", 460, 1000, answer, lines, [])["source"] == "model"
    assert real_locate(b"", 1000, 1000, answer, lines, [])["source"] == "text"


def test_the_shops_name_over_a_card_is_no_label_for_its_checkbox(monkeypatch):
    asked = asked_sam(monkeypatch, {"product checkbox": [[22, 155, 43, 180]]})
    lines = [{"text": "Toko Contoh", "words": [{"text": "Toko", "box": [0.221, 0.114, 0.33, 0.128]}, {"text": "Contoh", "box": [0.34, 0.114, 0.47, 0.128]}]}]
    target = {"label": "Toko Contoh", "object": "product checkbox", "box": [48, 151, 95, 184], "mark_id": None}
    found = real_locate(b"", 460, 1000, {"action": "tap", "target": target}, lines)
    assert found["source"] == "sam" and asked == ["product checkbox"]


def test_a_wide_model_box_beside_the_words_is_no_icon(monkeypatch):
    asked = asked_sam(monkeypatch, {})
    target = {**CLOSE, "box": [380, 165, 700, 195]}
    assert real_locate(b"", 1000, 1000, {"action": "tap", "target": target}, HEADING)["source"] == "text" and asked == []


def outlined(near, words, label="Serum"):
    target = {"label": label, "object": "product card", "box": near, "mark_id": None}
    return real_locate(b"", 500, 1000, {"action": "tap", "target": target}, [line_of((label, words))])


def test_a_word_inside_a_large_box_outlines_the_whole_box():
    found = outlined([20, 700, 480, 990], [0.29, 0.9, 0.39, 0.914])
    assert found == {"box": [0.02, 0.7, 0.48, 0.99], "source": "whole", "ms": 0}


def test_a_word_only_half_in_says_the_box_is_off_and_stands():
    found = outlined([30, 340, 970, 410], [0.43, 0.398, 0.61, 0.414])
    assert found["box"] == [0.43, 0.398, 0.61, 0.414] and found["source"] == "text"


def test_a_word_in_a_small_box_stands():
    found = outlined([12, 68, 735, 105], [0.1, 0.08, 0.37, 0.1])
    assert found["box"] == [0.1, 0.08, 0.37, 0.1] and found["source"] == "text"


def test_a_box_too_large_to_point_at_anything_is_not_taken():
    found = outlined([0, 100, 1000, 400], [0.4, 0.2, 0.5, 0.22])
    assert found["box"] == [0.4, 0.2, 0.5, 0.22] and found["source"] == "text"
    assert outlined([0, 100, 1000, 350], [0.4, 0.2, 0.5, 0.22])["source"] == "whole"


def test_a_caption_on_a_field_gives_way_to_the_field():
    found = outlined([70, 527, 930, 586], [0.074, 0.506, 0.265, 0.522])
    assert found == {"box": [0.07, 0.527, 0.93, 0.586], "source": "field", "ms": 0}


@pytest.mark.parametrize("words", [
    [0.43, 0.506, 0.6, 0.522],  # In the middle of the box: the words of a button that the model drew lower.
    [0.074, 0.49, 0.265, 0.506],  # Further than a caption stands.
    [0.074, 0.59, 0.265, 0.606],  # Under the box.
])
def test_other_words_outside_a_field_stand(words):
    found = outlined([70, 527, 930, 586], words)
    assert found["box"] == words and found["source"] == "text"


def test_a_field_too_small_to_be_large_keeps_the_words_over_it():
    found = outlined([70, 527, 400, 560], [0.074, 0.506, 0.265, 0.522])
    assert found["source"] == "text"
