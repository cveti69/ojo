from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app import hint, window


def page(band):
    """A light page with a coloured band along its foot."""
    image = Image.new("RGB", (921, 2000), (245, 245, 245))
    ImageDraw.Draw(image).rectangle((0, 1880, 921, 2000), fill=band)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85)
    return output.getvalue()


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
def test_target_frame_stays_inside_image(box):
    result = hint.render(source_image(), box)
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (720, 960) and image.format == "JPEG"
        yellow = [(x, y) for y in range(image.height) for x in range(image.width)
                  if (lambda c: c[0] > 190 and c[1] > 160 and c[2] < 80)(image.getpixel((x, y)))]
        assert len(yellow) > 150
        assert min(x for x, _ in yellow) >= 12 and max(x for x, _ in yellow) < 708
        assert min(y for _, y in yellow) >= 12 and max(y for _, y in yellow) < 948


def test_no_target_shows_the_whole_screen():
    result = hint.render(source_image(), None)
    with Image.open(io.BytesIO(result)) as image:
        assert image.size == (720, 960)
        pixels = list(image.getdata())
        assert sum(1 for r, g, b in pixels if r > 150 and g < 80 and b < 80) > 100
        assert sum(1 for r, g, b in pixels if b > 150 and r < 80 and g < 80) > 100
        assert sum(1 for r, g, b in pixels if r > 190 and g > 160 and b < 80) == 0


def test_the_picture_fills_the_window_with_nothing_written_under_it():
    with Image.open(io.BytesIO(hint.render(source_image(), [0.1, 0.4, 0.5, 0.45]))) as image:
        assert all(min(image.getpixel((x, y))) > 200 for x in range(36, 688, 40) for y in (36, 924))
        assert max(image.getpixel((8, 8))) < 40 and max(image.getpixel((712, 952))) < 40


def test_the_outline_goes_round_the_target():
    picture = Image.new("RGB", (921, 2000), (245, 245, 245))
    ImageDraw.Draw(picture).rectangle((368, 800, 552, 880), fill=(200, 20, 20))
    output = io.BytesIO()
    picture.save(output, format="JPEG", quality=90)
    with Image.open(io.BytesIO(hint.render(output.getvalue(), [0.4, 0.4, 0.6, 0.44]))) as image:
        pixels = [(x, y, image.getpixel((x, y))) for y in range(image.height) for x in range(image.width)]
    red = [(x, y) for x, y, c in pixels if c[0] > 150 and c[1] < 70 and c[2] < 70]
    yellow = [(x, y) for x, y, c in pixels if c[0] > 190 and c[1] > 160 and c[2] < 80]
    for read in (lambda spot: spot[0], lambda spot: spot[1]):
        assert 0 < min(map(read, red)) - min(map(read, yellow)) <= 24
        assert 0 < max(map(read, yellow)) - max(map(read, red)) <= 24


def test_a_wide_target_fills_the_window_too():
    with Image.open(io.BytesIO(hint.render(page((245, 245, 245)), [0.05, 0.6, 0.95, 0.65]))) as image:
        assert min(image.getpixel((36, 480))) > 200 and min(image.getpixel((684, 480))) > 200


@pytest.mark.parametrize("color", [(240, 0, 90), (120, 20, 200)])
def test_a_bright_band_at_the_foot_of_the_page_does_not_hide_the_window(color):
    picture = Image.open(io.BytesIO(hint.render(page(color), [0.3, 0.8, 0.7, 0.84])))
    shot = Image.new("RGB", (460, 1000), (245, 245, 245))
    shot.paste(window.add_border(picture).resize((214, 285), Image.Resampling.LANCZOS), (231, 150))
    output = io.BytesIO()
    shot.save(output, format="JPEG", quality=60)
    assert window.find(output.getvalue())


def test_a_tall_target_keeps_its_outline_whole():
    with Image.open(io.BytesIO(hint.render(source_image(), [0.1, 0.1, 0.9, 0.9]))) as image:
        rows = [y for y in range(image.height) if (lambda c: c[0] > 190 and c[1] > 160 and c[2] < 80)(image.getpixel((360, y)))]
        assert rows and min(rows) < 200 and max(rows) > 760
