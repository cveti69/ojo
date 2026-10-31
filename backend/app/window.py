"""Finds our own floating window on a frame by its coloured border, so the agent never takes it for the app."""
from __future__ import annotations

import io
import math
from itertools import combinations
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageStat

BORDER = (255, 0, 170)
BORDER_SHARE = 0.035  # Of the window's width: thick enough to survive a small JPEG, thin enough to leave the hint alone.
HOT_MARGIN = 45  # How far red and blue must rise above green. Dimmed window controls still pass it.
MIN_SIDE = 0.12  # A window side fills at least this share of the frame's width or height.
MAX_THICKNESS = 0.03  # Of the frame: anything thicker is a banner, not a border.
MIN_FILLED = 0.6  # Rounded corners and JPEG eat the rest of each side.
MAX_INSIDE = 0.25  # A window is hollow: a solid colour block is not one.
RATIO = (0.6, 0.9)  # Width over height; the window is 3:4.
GREY = (128, 128, 128)
QUALITY = 70
RULE = ("\nThe grey rectangle {box} on the image, in the same 0-1000 coordinates, is our floating window. "
        "It hides that part of the app, and nothing there can be tapped.")


def add_border(picture: Image.Image) -> Image.Image:
    """Draws the border the way the phone does, inside the picture the window shows."""
    bordered = picture.convert("RGB")
    thickness = round(bordered.width * BORDER_SHARE)
    ImageDraw.Draw(bordered).rectangle((0, 0, bordered.width - 1, bordered.height - 1), outline=BORDER, width=thickness)
    return bordered


def _hot(image: Image.Image) -> Image.Image:
    red, green, blue = image.split()
    rise = ImageChops.darker(ImageChops.subtract(red, green), ImageChops.subtract(blue, green))
    return rise.point(lambda value: 255 if value >= HOT_MARGIN else 0)


def _bands(profile: bytes, thickest: int) -> List[Tuple[int, int]]:
    """Runs of columns or rows with enough border in them."""
    floor = round(255 * MIN_SIDE)
    bands, start = [], None
    for index, value in enumerate(profile + b"\0"):
        if value >= floor and start is None:
            start = index
        elif value < floor and start is not None:
            if index - start <= thickest:
                bands.append((start, index))
            start = None
    return bands


def _share(mask: Image.Image, box: Tuple[int, int, int, int]) -> float:
    return ImageStat.Stat(mask.crop(box)).mean[0] / 255


def _is_window(mask: Image.Image, left: tuple, right: tuple, top: tuple, bottom: tuple) -> bool:
    x1, x2, y1, y2 = left[0], right[1], top[0], bottom[1]
    if not RATIO[0] <= (x2 - x1) / (y2 - y1) <= RATIO[1]:
        return False
    sides = ((x1, y1, left[1], y2), (right[0], y1, x2, y2), (x1, y1, x2, top[1]), (x1, bottom[0], x2, y2))
    return (all(_share(mask, side) >= MIN_FILLED for side in sides)
            and _share(mask, (left[1], top[1], right[0], bottom[0])) <= MAX_INSIDE)


def find(jpeg: bytes) -> Optional[List[float]]:
    """Returns the window's outer edges in 0-1 coordinates, or None when it is not on the frame."""
    with Image.open(io.BytesIO(jpeg)) as original:
        image = original.convert("RGB")
    width, height = image.size
    mask = _hot(image)
    thickest = round(max(width, height) * MAX_THICKNESS)
    columns = _bands(mask.resize((width, 1), Image.Resampling.BOX).tobytes(), thickest)
    rows = _bands(mask.resize((1, height), Image.Resampling.BOX).tobytes(), thickest)
    for left, right in combinations(columns, 2):
        for top, bottom in combinations(rows, 2):
            if _is_window(mask, left, right, top, bottom):
                return [left[0] / width, top[0] / height, right[1] / width, bottom[1] / height]
    return None


def gaze(box: Optional[List[float]], target: Optional[List[float]], width: int, height: int) -> Optional[List[float]]:
    """The way from the middle of our window to the target, as a unit step in the frame's pixels. None means look ahead."""
    if not box or not target:
        return None
    x = ((target[0] + target[2]) - (box[0] + box[2])) / 2 * width
    y = ((target[1] + target[3]) - (box[1] + box[3])) / 2 * height
    length = math.hypot(x, y)
    return [round(x / length, 3), round(y / length, 3)] if length else None


def outside(lines: Sequence[dict], box: List[float]) -> List[dict]:
    """The words read off the frame, without the ones that sit under the window."""
    def under(word: dict) -> bool:
        x, y = (word["box"][0] + word["box"][2]) / 2, (word["box"][1] + word["box"][3]) / 2
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]
    kept = [dict(line, words=[word for word in line["words"] if not under(word)]) for line in lines]
    return [line for line in kept if line["words"]]


def hide(jpeg: bytes, box: List[float]) -> bytes:
    """Paints the window grey, so the model sees only the app and a plain patch."""
    with Image.open(io.BytesIO(jpeg)) as original:
        image = original.convert("RGB")
    width, height = image.size
    ImageDraw.Draw(image).rectangle((box[0] * width, box[1] * height, box[2] * width, box[3] * height), fill=GREY)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=QUALITY)
    return output.getvalue()
