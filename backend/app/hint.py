"""Render a 720 x 960 hint: the place on the frame, outlined, with room around it. The voice says the words."""
from __future__ import annotations

import io
import math
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageOps

SIZE = (720, 960)
AREA = (32, 32, SIZE[0] - 32, SIZE[1] - 32)  # Clear of the phone's 25 px border: the window is found by it, and a bright page must not touch it.
QUALITY = 60  # The text still reads well, and the picture weighs half as much on the phone's network.
OUTLINE = (255, 223, 0)
BLACK = (0, 0, 0)


def _target(box: Optional[List[float]], width: int, height: int) -> Optional[Tuple[float, ...]]:
    if not box or len(box) != 4:
        return None
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in box):
        return None
    x1, y1, x2, y2 = [max(0.0, min(1.0, value)) for value in box]
    if x1 >= x2 or y1 >= y2:
        return None
    return x1 * width, y1 * height, x2 * width, y2 * height


def _crop(target: Tuple[float, ...], width: int, height: int, aspect: float) -> tuple:
    x1, y1, x2, y2 = target
    crop_width = min(width, max(width * 0.5, (x2 - x1) * 1.8))
    crop_height = max((y2 - y1) * 1.8, crop_width / aspect)
    crop_width = max(crop_width, crop_height * aspect)
    crop_width, crop_height = min(width, crop_width), min(height, crop_height)
    left = min(max(0.0, (x1 + x2 - crop_width) / 2), width - crop_width)
    top = min(max(0.0, (y1 + y2 - crop_height) / 2), height - crop_height)
    return math.floor(left), math.floor(top), math.ceil(left + crop_width), math.ceil(top + crop_height)


def render(frame_jpeg: bytes, box: Optional[List[float]]) -> bytes:
    canvas = Image.new("RGB", SIZE, BLACK)
    with Image.open(io.BytesIO(frame_jpeg)) as original:
        source = original.convert("RGB")
    target = _target(box, *source.size)
    area_width, area_height = AREA[2] - AREA[0], AREA[3] - AREA[1]
    crop = _crop(target, *source.size, area_width / area_height) if target else (0, 0, *source.size)
    cropped = source.crop(crop)
    preview = ImageOps.contain(cropped, (area_width, area_height), Image.Resampling.LANCZOS)
    left = AREA[0] + (area_width - preview.width) // 2
    top = AREA[1] + (area_height - preview.height) // 2
    canvas.paste(preview, (left, top))
    draw = ImageDraw.Draw(canvas)
    if target:
        sx, sy = preview.width / cropped.width, preview.height / cropped.height
        x1, y1, x2, y2 = target
        rectangle = (
            left + (x1 - crop[0]) * sx - 8,
            top + (y1 - crop[1]) * sy - 8,
            left + (x2 - crop[0]) * sx + 8,
            top + (y2 - crop[1]) * sy + 8,
        )
        radius = min(22, (rectangle[2] - rectangle[0]) / 3, (rectangle[3] - rectangle[1]) / 3)
        draw.rounded_rectangle(rectangle, radius=radius, outline=(0, 0, 0), width=16)
        draw.rounded_rectangle(rectangle, radius=radius, outline=OUTLINE, width=10)
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=QUALITY)
    return output.getvalue()
