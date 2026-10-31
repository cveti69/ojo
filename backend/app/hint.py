"""Render a readable 720 x 960 hint with room around every target."""
from __future__ import annotations

import io
import math
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import config

SIZE = (720, 960)
QUALITY = 60  # The text still reads well, and the picture weighs half as much on the phone's network.
BACKGROUND = (27, 35, 48)
PANEL = (13, 20, 32)
OUTLINE = (255, 223, 0)
BLACK = (0, 0, 0)
EYES_ROOM = 200  # At the left of the panel: the phone draws small eyes there.
EYES_SIDE = 170


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> List[str]:
    lines = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            pieces = []
            part = ""
            for character in word:
                if part and font.getlength(part + character) > width:
                    pieces.append(part)
                    part = ""
                part += character
            pieces.append(part)
            for piece in pieces:
                candidate = (line + " " + piece).strip()
                if line and font.getlength(candidate) > width:
                    lines.append(line)
                    line = piece
                else:
                    line = candidate
        lines.append(line)
    return lines


def _text_layout(text: str, room: int = 0) -> tuple:
    width = SIZE[0] - 80 - room
    for size in range(52, 35, -2):
        font = ImageFont.truetype(str(config.HINT_FONT), size)
        lines = _wrap(text, font, width)
        line_height = math.ceil(size * 1.24)
        if len(lines) * line_height <= 360:
            break
    max_lines = 360 // line_height
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while font.getlength(lines[-1] + "…") > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    height = max(190, len(lines) * line_height + 60)
    return font, lines, line_height, height


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
    crop_width = max(width * 0.5, (x2 - x1) * 1.8)
    crop_height = max((y2 - y1) * 1.8, crop_width / aspect)
    crop_width = max(crop_width, crop_height * aspect)
    crop_width, crop_height = min(width, crop_width), min(height, crop_height)
    left = min(max(0.0, (x1 + x2 - crop_width) / 2), width - crop_width)
    top = min(max(0.0, (y1 + y2 - crop_height) / 2), height - crop_height)
    return math.floor(left), math.floor(top), math.ceil(left + crop_width), math.ceil(top + crop_height)


def eyes_box(instruction_ru: str) -> List[float]:
    """Where the phone draws the eyes on a picture rendered with room for them, in 0-1 of the picture."""
    panel_height = _text_layout(instruction_ru, EYES_ROOM)[3]
    top = SIZE[1] - 12 - panel_height + (panel_height - EYES_SIDE) / 2
    left = 12 + (EYES_ROOM - EYES_SIDE) / 2
    return [round(value, 4) for value in (left / SIZE[0], top / SIZE[1], (left + EYES_SIDE) / SIZE[0], (top + EYES_SIDE) / SIZE[1])]


def render(frame_jpeg: bytes, box: Optional[List[float]], instruction_ru: str, eyes: bool = False) -> bytes:
    room = EYES_ROOM if eyes else 0
    font, lines, line_height, panel_height = _text_layout(instruction_ru, room)
    panel_top = SIZE[1] - panel_height - 12
    canvas = Image.new("RGB", SIZE, BLACK if eyes else BACKGROUND)
    with Image.open(io.BytesIO(frame_jpeg)) as original:
        source = original.convert("RGB")
    target = _target(box, *source.size)
    area = (32, 24, SIZE[0] - 32, panel_top - 24)
    area_width, area_height = area[2] - area[0], area[3] - area[1]
    crop = _crop(target, *source.size, area_width / area_height) if target else (0, 0, *source.size)
    cropped = source.crop(crop)
    preview = ImageOps.contain(cropped, (area_width, area_height), Image.Resampling.LANCZOS)
    left = area[0] + (area_width - preview.width) // 2
    top = area[1] + (area_height - preview.height) // 2
    canvas.paste(preview, (left, top))
    draw = ImageDraw.Draw(canvas)
    if target:
        sx, sy = preview.width / cropped.width, preview.height / cropped.height
        x1, y1, x2, y2 = target
        rectangle = (
            max(12, left + (x1 - crop[0]) * sx - 8),
            max(12, top + (y1 - crop[1]) * sy - 8),
            min(SIZE[0] - 12, left + (x2 - crop[0]) * sx + 8),
            min(panel_top - 12, top + (y2 - crop[1]) * sy + 8),
        )
        radius = min(22, (rectangle[2] - rectangle[0]) / 3, (rectangle[3] - rectangle[1]) / 3)
        draw.rounded_rectangle(rectangle, radius=radius, outline=(0, 0, 0), width=16)
        draw.rounded_rectangle(rectangle, radius=radius, outline=OUTLINE, width=10)
    draw.rounded_rectangle((12, panel_top, SIZE[0] - 12, SIZE[1] - 12), radius=28, fill=BLACK if eyes else PANEL)
    text_top = panel_top + (panel_height - len(lines) * line_height) // 2
    for index, line in enumerate(lines):
        draw.text((40 + room, text_top + index * line_height), line, font=font, fill="white", anchor="lt")
    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=QUALITY)
    return output.getvalue()
