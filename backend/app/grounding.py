"""Turns the model's idea of the target into an exact box on the frame."""
from __future__ import annotations

import difflib
import io
import math
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import meta

MIN_TEXT_MATCH = 0.78
TEXT_DISTANCE_WEIGHT = 1  # A spelling 0.1 worse still wins when it is 10% of the frame closer: the model's box can be a row off.
TEXT_REACH = 0.4  # On the bench and on real calls words sat within 0.33 of the model's box or past 0.66: there it is another place.
MAX_UNSPACED_WORDS = 6
FONT = Path(__file__).parent.parent / "assets" / "Inter-Bold.otf"
MARK_FONT_SIZE = 13
MARK_COLOR = (214, 0, 50)
MARK_QUALITY = 70
ICON_PHRASE = "icon"
ICON_REACH = 0.14  # How far the model's guess may land from the real icon, in shares of the frame.
ICON_MAX_AREA = 0.03  # A target larger than this share of the frame is not an icon.
ICON_MAX_STRETCH = 2.5  # Icons are nearly square, so a longer guess is a bar or a button, however small.
SHAPE_WORDS = ("icon", "checkbox", "toggle", "switch", "radio")  # Text next to these is a caption, not the target.


def _normal(text: str) -> str:
    """Keeps letters, marks and digits of any script, so Chinese or Thai labels survive."""
    text = unicodedata.normalize("NFKC", text.lower())
    return " ".join("".join(char if unicodedata.category(char)[0] in "LMN" else " " for char in text).split())


def center(box: list[float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _distance(first: list[float], second: list[float]) -> float:
    ax, ay = center(first)
    bx, by = center(second)
    return (ax - bx) ** 2 + (ay - by) ** 2


def _union(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def snap_to_text(label: str, near: list[float] | None, lines: list[dict], reach: float = TEXT_REACH) -> list[float] | None:
    """Finds the words that spell the label, weighing the spelling against the distance from the model's guess.

    `lines` holds {"text", "words": [{"text", "box"}]} with boxes in 0-1 coordinates.
    """
    wanted = _normal(label)
    size = len(wanted.split())
    if not wanted or size == 0:
        return None
    # Chinese and Thai have no spaces, so one label can span several recognised words.
    spaced = wanted.isascii()
    counts = {size - 1, size, size + 1} if spaced else range(1, MAX_UNSPACED_WORDS + 1)
    joiner = " " if spaced else ""
    wanted = joiner.join(wanted.split())
    best: tuple[float, list[float]] | None = None
    for line in lines:
        words = line["words"]
        for start in range(len(words)):
            for count in counts:
                window = words[start:start + count]
                if not window or len(window) != count:
                    continue
                text = joiner.join(_normal(" ".join(word["text"] for word in window)).split())
                score = difflib.SequenceMatcher(None, wanted, text).ratio()
                if score < MIN_TEXT_MATCH:
                    continue
                box = _union([word["box"] for word in window])
                distance = math.sqrt(_distance(box, near)) if near else 0
                if distance > reach:
                    continue
                rank = score - TEXT_DISTANCE_WEIGHT * distance
                if best is None or rank > best[0]:
                    best = (rank, box)
    return best[1] if best else None


def snap_with_sam(jpeg: bytes, width: int, height: int, phrase: str, near: list[float] | None) -> dict:
    """Asks SAM for everything matching the phrase and keeps the box closest to the model's guess."""
    found = meta.sam(jpeg, phrase)
    boxes = [[item["box"][0] / width, item["box"][1] / height, item["box"][2] / width, item["box"][3] / height]
             for item in found["boxes"]]
    box = None
    if near and boxes:
        box = min(boxes, key=lambda candidate: _distance(candidate, near))
    elif len(boxes) == 1:  # Without a guess only a single match is safe to trust.
        box = boxes[0]
    return {"box": box, "ms": found["ms"], "found": len(boxes)}


def find_icons(jpeg: bytes, width: int, height: int) -> list[list[float]]:
    """Finds every icon on the frame. It runs next to the model, so an icon target costs no extra time."""
    return [[box[0] / width, box[1] / height, box[2] / width, box[3] / height]
            for box in (item["box"] for item in meta.sam(jpeg, ICON_PHRASE)["boxes"])]


def _is_large(box: list[float] | None) -> bool:
    return bool(box) and (box[2] - box[0]) * (box[3] - box[1]) > ICON_MAX_AREA


def _is_stretched(box: list[float], frame_ratio: float) -> bool:
    width, height = (box[2] - box[0]) * frame_ratio, box[3] - box[1]
    return max(width, height) > ICON_MAX_STRETCH * min(width, height)


def snap_to_icon(near: list[float] | None, icons: list[list[float]], frame_ratio: float) -> list[float] | None:
    """Takes the icon closest to the model's guess when the guess looks like an icon at all.

    `frame_ratio` is the frame's width over its height: boxes are in shares of the frame, not in real proportions.
    """
    if not near or not icons or _is_large(near) or _is_stretched(near, frame_ratio):
        return None
    box = min(icons, key=lambda candidate: _distance(candidate, near))
    return box if _distance(box, near) <= ICON_REACH ** 2 else None


def locate(jpeg: bytes, width: int, height: int, answer: dict, lines: list[dict] | None = None,
           icons: list[list[float]] | None = None, marks: dict[int, list[float]] | None = None) -> dict:
    """Picks the final box: a mark, text on screen, SAM by name, an icon, then the model's own box.

    Without `icons` SAM looks for them here, and only when nothing before it found the place. On the bench that order hit 35
    of 40 against 33 with icons first: a name found is nearly always right, and an icon can be a neighbour of the target.
    Returns {"box": 0-1 coordinates or None, "source", "ms"}.
    """
    target = answer["target"]
    if answer["action"] not in ("tap", "type"):
        return {"box": None, "source": "none", "ms": 0}
    near = [value / 1000 for value in target["box"]] or None
    if marks and target.get("mark_id") in marks:
        return {"box": marks[target["mark_id"]], "source": "mark", "ms": 0}
    shape = not target.get("words_first") and any(word in target["object"].lower() for word in SHAPE_WORDS)
    snapped = snap_to_text(target["label"], near, lines or [], math.inf if shape else TEXT_REACH)
    if snapped and not shape:
        return {"box": snapped, "source": "text", "ms": 0}
    if snapped:  # The model's box can be a row off, and the words next to a radio button say which row it is.
        near = [near[0], snapped[1], near[2], snapped[3]] if near else snapped
    ms = 0
    if target["object"] and not _is_large(near):  # A large target is easy to hit, a search would only cost time.
        try:
            found = snap_with_sam(jpeg, width, height, target["object"], near)
        except meta.MetaError:  # SAM is an upgrade: the hint must still go out without it.
            found = {"box": None, "ms": 0}
        ms = found["ms"]
        if found["box"]:
            return {"box": found["box"], "source": "sam", "ms": ms}
    if icons is None and near and not _is_large(near) and not _is_stretched(near, width / height):
        try:
            icons = find_icons(jpeg, width, height)
        except meta.MetaError:
            icons = []
    icon = snap_to_icon(near, icons or [], width / height)
    if icon:
        return {"box": icon, "source": "icon", "ms": ms}
    if snapped:
        return {"box": snapped, "source": "text", "ms": ms}
    return {"box": near, "source": "model" if near else "none", "ms": ms}


def draw_marks(jpeg: bytes, lines: list[dict]) -> tuple[bytes, dict[int, list[float]]]:
    """Draws a numbered mark on each text line and returns the marked copy with {number: box}."""
    image = Image.open(io.BytesIO(jpeg)).convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    font = ImageFont.truetype(str(FONT), MARK_FONT_SIZE)
    marks: dict[int, list[float]] = {}
    for number, line in enumerate(lines, 1):
        box = _union([word["box"] for word in line["words"]]) if line["words"] else line["box"]
        box = [max(box[0], 0.0), max(box[1], 0.0), min(box[2], 1.0), min(box[3], 1.0)]
        marks[number] = box
        x1, y1, x2, y2 = box[0] * width, box[1] * height, box[2] * width, box[3] * height
        draw.rectangle((x1 - 2, y1 - 2, x2 + 2, y2 + 2), outline=MARK_COLOR, width=1)
        tag = str(number)
        left, top, right, bottom = draw.textbbox((0, 0), tag, font=font)
        tag_width, tag_height = right - left + 4, bottom - top + 4
        tag_x = min(max(x1 - 2, 0), width - tag_width)
        tag_y = max(y1 - tag_height - 1, 0)
        draw.rectangle((tag_x, tag_y, tag_x + tag_width, tag_y + tag_height), fill=MARK_COLOR)
        draw.text((tag_x + 2 - left, tag_y + 2 - top), tag, font=font, fill="white")
    out = io.BytesIO()
    image.save(out, "JPEG", quality=MARK_QUALITY)
    return out.getvalue(), marks
