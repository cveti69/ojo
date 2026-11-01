from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app import hint


def source_image():
    image = Image.new("RGB", (1080, 2340), (235, 235, 235))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 100, 100), fill=(220, 20, 20))
    draw.rectangle((980, 2240, 1079, 2339), fill=(20, 20, 220))
    draw.rectangle((10, 1850, 1070, 2050), fill=(20, 150, 70))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


@pytest.mark.parametrize("box", [[0, 0, 0.045, 0.035], [0.01, 0.81, 0.99, 0.89], [0.955, 0.965, 1, 1]])
def test_target_frame_stays_inside_image_and_above_panel(box):
    result = hint.render(source_image(), box, "Нажми на кнопку")
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (720, 960) and image.format == "JPEG"
        yellow = [(x, y) for y in range(image.height) for x in range(image.width)
                  if (lambda c: c[0] > 190 and c[1] > 160 and c[2] < 80)(image.getpixel((x, y)))]
        assert len(yellow) > 150
        assert min(x for x, _ in yellow) >= 12 and max(x for x, _ in yellow) < 708
        assert min(y for _, y in yellow) >= 12 and max(y for _, y in yellow) < 746
        white_text = sum(1 for r, g, b in image.crop((30, 780, 690, 930)).getdata()
                         if min(r, g, b) > 200)
        assert white_text > 300


def test_no_target_shows_the_whole_screen():
    result = hint.render(source_image(), None, "Подожди загрузку")
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (720, 960)
        pixels = list(image.getdata())
        assert sum(1 for r, g, b in pixels if r > 150 and g < 80 and b < 80) > 100
        assert sum(1 for r, g, b in pixels if b > 150 and r < 80 and g < 80) > 100
        assert sum(1 for r, g, b in pixels if r > 190 and g > 160 and b < 80) == 0


def test_long_instruction_wraps_and_keeps_target_visible():
    instruction = "Проверь выбранный адрес доставки, затем нажми на зелёную кнопку внизу экрана"
    result = hint.render(source_image(), [0.01, 0.81, 0.99, 0.89], instruction)
    with Image.open(io.BytesIO(result)) as image:
        rows_with_text = []
        for y in range(600, 925):
            if sum(1 for r, g, b in image.crop((35, y, 685, y + 1)).getdata() if min(r, g, b) > 210) > 8:
                rows_with_text.append(y)
        assert rows_with_text and max(rows_with_text) < 925
        assert len(rows_with_text) > 70


@pytest.mark.parametrize("instruction,shown", [
    ("Нажми на поиск", "Нажми на поиск"),
    ("Вот она. Проверь цену внизу.", "Вот она."),
    ("Проверь выбранный адрес доставки, затем нажми на зелёную кнопку внизу экрана", "Проверь выбранный…"),
    ("Проверьвыбранныйадресдоставкизатемнажминазелёнуюкнопкувнизу", "Проверьвыбранныйадрес…"),
])
def test_with_the_eyes_one_short_line_is_left(instruction, shown):
    assert hint._short_layout(instruction)[1] == [shown]


def test_with_the_eyes_the_line_leaves_the_picture_more_room():
    instruction = "Проверь выбранный адрес доставки, затем нажми на зелёную кнопку внизу экрана"
    panel_top = hint.SIZE[1] - hint._short_layout(instruction)[3] - 12
    assert hint._short_layout(instruction)[3] < hint._text_layout(instruction)[3]
    result = hint.render(source_image(), [0.01, 0.81, 0.99, 0.89], instruction, eyes=True)
    with Image.open(io.BytesIO(result)) as image:
        assert max(image.getpixel((hint.SIZE[0] // 2, panel_top - 30))) > 60
        rows = [y for y in range(panel_top, 950) if sum(1 for pixel in image.crop((40, y, 680, y + 1)).getdata() if min(pixel) > 210) > 8]
        assert rows and max(rows) - min(rows) < 70


def test_without_the_eyes_the_picture_stays_the_same():
    assert hint.render(source_image(), [0.1, 0.1, 0.5, 0.2], "Нажми") == hint.render(source_image(), [0.1, 0.1, 0.5, 0.2], "Нажми", False)
