from __future__ import annotations

import io
import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import window

IOS = Path(__file__).parents[2] / "ios"
SIZE = (460, 1000)
VIOLET = window.BORDER
OLD_PINK = (255, 0, 170)
WHITE = (255, 255, 255)
BRIGHT_PAGES = [(124, 58, 237), (76, 52, 148), (236, 72, 153), (190, 30, 150)]


def jpeg(image):
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=60)
    return output.getvalue()


def white():
    return Image.new("RGB", SIZE, WHITE)


def black():
    return Image.new("RGB", SIZE, (0, 0, 0))


def busy():
    picker = random.Random(5)
    image = Image.new("RGB", SIZE, (240, 236, 228))
    draw = ImageDraw.Draw(image)
    for _ in range(60):
        x, y, radius = picker.randrange(460), picker.randrange(1000), picker.randrange(10, 50)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(picker.randrange(256), picker.randrange(140, 256), picker.randrange(256)))
    return image


def picture(dark=True):
    return Image.new("RGB", (720, 960), (14, 14, 18) if dark else (250, 250, 250))


def shot(base, inner, width, spot=(20, 300), dim=1.0):
    height = round(width * 4 / 3)
    small = inner.resize((width, height), Image.Resampling.LANCZOS).point(lambda value: round(value * dim))
    shape = Image.new("L", small.size, 0)
    ImageDraw.Draw(shape).rounded_rectangle((0, 0, width - 1, height - 1), radius=round(width * 0.08), fill=255)
    result = base.copy()
    result.paste(small, spot, shape)
    return result


def near(found, width, spot, size=SIZE):
    x, y = spot
    edges = [x / size[0], y / size[1], (x + width) / size[0], (y + round(width * 4 / 3)) / size[1]]
    return found is not None and all(abs(a - b) < 0.012 for a, b in zip(found, edges))


def card_page(back, width=250, ratio=0.75):
    page = Image.new("RGB", SIZE, back)
    x, y = (460 - width) // 2, 300
    ImageDraw.Draw(page).rounded_rectangle((x, y, x + width, y + round(width / ratio)), radius=14, fill=WHITE)
    return page


def sale_page(back, columns=2, ratio=0.75, gutter=14):
    page = Image.new("RGB", SIZE, back)
    draw = ImageDraw.Draw(page)
    width = (460 - 28 - (columns - 1) * gutter) // columns
    height = round(width / ratio)
    for y in range(120, 1000 - height - 16, height + gutter):
        for column in range(columns):
            x = 14 + column * (width + gutter)
            draw.rounded_rectangle((x, y, x + width, y + height), radius=9, fill=WHITE)
            draw.rectangle((x + 6, y + 6, x + width - 6, y + round(height * 0.62)), fill=(225, 228, 232))
    return page


@pytest.mark.parametrize("page", [white, black, busy])
@pytest.mark.parametrize("width", [124, 180, 202, 285])
@pytest.mark.parametrize("dim", [1.0, 0.55])
@pytest.mark.parametrize("dark", [True, False])
def test_the_border_is_found_bright_or_dimmed(page, width, dim, dark):
    frame = shot(page(), window.add_border(picture(dark)), width, dim=dim)
    assert near(window.find(jpeg(frame)), width, (20, 300))


def test_the_colour_stays_hot_under_the_window_dimming():
    red, green, blue = VIOLET
    assert min(red - green, blue - green) * 0.55 >= window.HOT_MARGIN + 15


def test_the_phone_draws_the_colour_the_server_knows():
    red, green, blue = VIOLET
    drawn = (IOS / "App/Sources/HintWindow/PictureInPictureHintWindow.swift").read_text(encoding="utf-8")
    assert f"UIColor(red: {red}.0 / 255, green: {green}.0 / 255, blue: {blue}.0 / 255, alpha: 1)" in drawn
    assert f"violet: [UInt8] = [{red}, {green}, {blue}, 255]" in (IOS / "Tests/ProtocolChecks.swift").read_text(encoding="utf-8")


def test_the_old_pink_border_is_found_too_as_an_older_build_still_draws_it():
    bordered = picture()
    ImageDraw.Draw(bordered).rectangle((0, 0, 719, 959), outline=OLD_PINK, width=25)
    assert near(window.find(jpeg(shot(white(), bordered, 202))), 202, (20, 300))


@pytest.mark.parametrize("spot, inside", [((360, 24), (360, 25)), ((360, 935), (360, 934)), ((24, 480), (25, 480)), ((695, 480), (694, 480))])
def test_the_line_is_as_thick_as_the_phone_draws_it_on_every_side(spot, inside):
    bordered = window.add_border(picture())
    assert bordered.getpixel(spot) == VIOLET
    assert bordered.getpixel(inside) != VIOLET


@pytest.mark.parametrize("back", BRIGHT_PAGES)
@pytest.mark.parametrize("width", [140, 230, 320, 370])
@pytest.mark.parametrize("ratio", [0.72, 0.75, 0.78])
def test_a_white_card_on_a_bright_page_is_not_a_window(back, width, ratio):
    assert window.find(jpeg(card_page(back, width, ratio))) is None


@pytest.mark.parametrize("back", BRIGHT_PAGES)
@pytest.mark.parametrize("columns", [2, 3])
@pytest.mark.parametrize("ratio", [0.66, 0.75, 0.82])
@pytest.mark.parametrize("gutter", [10, 14, 24])
def test_a_sale_page_of_white_cards_is_not_a_window(back, columns, ratio, gutter):
    assert window.find(jpeg(sale_page(back, columns, ratio, gutter))) is None


def test_the_window_is_found_on_a_sale_page_of_a_colour_unlike_the_border():
    frame = shot(sale_page((76, 52, 148)), window.add_border(picture()), 180, spot=(260, 400))
    assert near(window.find(jpeg(frame)), 180, (260, 400))


@pytest.mark.parametrize("back", [(124, 58, 237), (236, 72, 153)])
def test_a_card_is_never_taken_for_the_window_on_a_page_of_the_borders_colour(back):
    frame = shot(sale_page(back), window.add_border(picture()), 180, spot=(260, 400))
    found = window.find(jpeg(frame))
    assert found is None or near(found, 180, (260, 400))


@pytest.mark.parametrize("band", [(0, 900, 460, 1000), (0, 0, 460, 100)])
def test_a_low_bright_band_away_from_the_window_does_not_hide_it(band):
    page = white()
    ImageDraw.Draw(page).rectangle(band, fill=(120, 20, 200))
    frame = shot(page, window.add_border(picture()), 180, spot=(260, 400))
    assert near(window.find(jpeg(frame)), 180, (260, 400))


def test_a_low_band_behind_the_window_does_not_hide_it():
    page = white()
    ImageDraw.Draw(page).rectangle((0, 440, 460, 540), fill=(120, 20, 200))
    frame = shot(page, window.add_border(picture()), 202, spot=(246, 360))
    assert near(window.find(jpeg(frame)), 202, (246, 360))


@pytest.mark.parametrize("width", [140, 160, 180, 300])
def test_the_window_is_found_on_a_turned_screen(width):
    turned = Image.new("RGB", (1000, 460), WHITE)
    frame = shot(turned, window.add_border(picture()), width, spot=(840 - width, 50))
    assert near(window.find(jpeg(frame)), width, (840 - width, 50), (1000, 460))


def violet_page():
    page = white()
    draw = ImageDraw.Draw(page)
    draw.rectangle((0, 0, 460, 110), fill=VIOLET)
    for y in range(150, 800, 90):
        draw.rounded_rectangle((20, y, 440, y + 60), radius=24, fill=(139, 92, 246))
    for x in (20, 160, 300):
        draw.rounded_rectangle((x, 830, x + 120, 870), radius=20, fill=(167, 139, 250))
    draw.rectangle((0, 920, 460, 1000), fill=(109, 40, 217))
    return page


def test_violet_buttons_and_bars_are_not_a_window():
    assert window.find(jpeg(violet_page())) is None


@pytest.mark.parametrize("shape", [(100, 300, 300, 567), (60, 200, 200, 387)])
def test_a_solid_violet_block_is_not_a_window(shape):
    page = white()
    ImageDraw.Draw(page).rectangle(shape, fill=VIOLET)
    assert window.find(jpeg(page)) is None


@pytest.mark.parametrize("box", [(100, 300, 220, 530), (100, 300, 300, 400), (100, 300, 300, 800), (100, 300, 290, 500)])
def test_a_frame_of_another_shape_is_not_a_window(box):
    page = white()
    ImageDraw.Draw(page).rectangle(box, outline=VIOLET, width=8)
    assert window.find(jpeg(page)) is None


@pytest.mark.parametrize("color", [(200, 180, 205), (40, 60, 230), (230, 60, 40), (60, 200, 90)])
def test_a_frame_of_another_colour_is_not_a_window(color):
    page = white()
    ImageDraw.Draw(page).rectangle((100, 300, 350, 633), outline=color, width=8)
    assert window.find(jpeg(page)) is None


def test_a_frame_of_the_window_colour_and_shape_does_count_as_one():
    page = white()
    ImageDraw.Draw(page).rectangle((100, 300, 350, 633), outline=VIOLET, width=8)
    assert window.find(jpeg(page)) is not None


@pytest.mark.parametrize("hole", [(100, 340, 110, 560), (340, 340, 350, 560), (140, 300, 310, 310), (140, 625, 310, 633)])
def test_each_side_has_to_be_there(hole):
    page = white()
    draw = ImageDraw.Draw(page)
    draw.rectangle((100, 300, 350, 633), outline=VIOLET, width=8)
    draw.rectangle(hole, fill=WHITE)
    assert window.find(jpeg(page)) is None
