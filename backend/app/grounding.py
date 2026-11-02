"""Turns the model's idea of the target into an exact box on the frame."""
from __future__ import annotations

import difflib
import io
import math
import unicodedata
from pathlib import Path
from typing import Iterator

from PIL import Image, ImageDraw, ImageFont

from . import meta

MIN_TEXT_MATCH = 0.78
TEXT_DISTANCE_WEIGHT = 1  # A spelling 0.1 worse still wins when it is 10% of the frame closer: the model's box can be a row off.
TEXT_REACH = 0.4  # On the bench and on real calls words outside the model's box sat within 0.33 of its middle or past 0.66: there it is another place.
MISREAD_SIGNS = (3, 4)  # The Chinese words one wrong sign keeps under MIN_TEXT_MATCH: 0.67 and 0.75. From five on they pass by themselves.
MISREAD_REACH = 0.05  # From the model's box. On a real call in Baidu Maps the words it meant stood up to 0.045 off it.
MISREAD_SOLID = 0.6  # The share of its width the words of one word fill: two words at the ends of a line are not one.
MAX_UNSPACED_WORDS = 6
FONT = Path(__file__).parent.parent / "assets" / "Inter-Bold.otf"
MARK_FONT_SIZE = 13
MARK_COLOR = (214, 0, 50)
MARK_QUALITY = 70
ICON_PHRASE = "icon"
ICON_REACH = 0.14  # How far the model's guess may land from the real icon, in shares of the frame.
ICON_MAX_AREA = 0.03  # A target larger than this share of the frame is not an icon.
ICON_MAX_STRETCH = 2.5  # Icons are nearly square, so a longer guess is a bar or a button, however small.
ROW_SLACK = 0.6  # Of the model's box height. Right icons sat within 0.45 of it from the box, icons of another row 0.79 and more.
CAPTION_SLACK = 0.8  # Wider: on a real call a checkbox took the shop's name 0.7 above it for its label.
WHOLE_MAX_AREA = 0.25  # A box past this share of the frame points at nothing. The largest on real calls was 19%.
FIELD_GAP = 0.01  # In shares of the frame. A field's caption stands right on it: 0.005 on a real call, and a box a row off stood 0.013 below its words.
FIELD_INDENT = 0.02  # And starts where the field starts, 0.005 off on that call: the words of a button stand in its middle.


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


def _away(box: list[float], near: list[float]) -> float:
    """How far words stand from the model's box. A row fills the frame and its name stands at one end, so anywhere inside is as close."""
    x, y = center(box)
    return math.hypot(max(near[0] - x, x - near[2], 0), max(near[1] - y, y - near[3], 0))


def _union(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _wrapped(lines: list[dict]) -> list[dict]:
    """A title on two rows is read as two lines, so each line goes on with the one right under it."""
    placed = [(line["words"], _union([word["box"] for word in line["words"]])) for line in lines if line["words"]]
    return [{"words": upper + lower} for upper, top in placed for lower, bottom in placed
            if abs(bottom[1] - top[3]) < top[3] - top[1] and min(top[2], bottom[2]) > max(top[0], bottom[0])]


def _windows(counts: set[int], joiner: str, lines: list[dict]) -> Iterator[tuple[str, list[float], list[dict]]]:
    """Every run of words on a line that has one of these lengths, with its spelling, its box and its words."""
    for line in lines:
        words = line["words"]
        for start in range(len(words)):
            for count in counts:
                window = words[start:start + count]
                if window and len(window) == count:
                    yield joiner.join(_normal(" ".join(word["text"] for word in window)).split()), _union([word["box"] for word in window]), window


def _spelled(wanted: str, counts: set[int], joiner: str, near: list[float] | None, lines: list[dict]) -> list[float] | None:
    best: tuple[tuple[float, float], list[float]] | None = None
    for text, box, _ in _windows(counts, joiner, lines):
        score = difflib.SequenceMatcher(None, wanted, text).ratio()
        if score < MIN_TEXT_MATCH:
            continue
        middle = math.sqrt(_distance(box, near)) if near else 0
        away = _away(box, near) if near else 0
        if away and middle > TEXT_REACH:
            continue
        rank = (score - TEXT_DISTANCE_WEIGHT * away, -middle)  # The same words twice in the box: the ones nearer its middle.
        if best is None or rank > best[0]:
            best = (rank, box)
    return best[1] if best else None


def _is_han(text: str) -> bool:
    return all(unicodedata.name(sign, "").startswith("CJK UNIFIED") for sign in text)


def _one_sign_off(wanted: str, text: str) -> bool:
    """The first sign must stand: 上一页 and 下一页, 未支付 and 已支付 differ only there."""
    return len(text) == len(wanted) and _is_han(text) and text[0] == wanted[0] and sum(sign != other for sign, other in zip(wanted, text)) == 1


def _is_solid(box: list[float], words: list[dict]) -> bool:
    return sum(word["box"][2] - word["box"][0] for word in words) >= MISREAD_SOLID * (box[2] - box[0])


def _misread(wanted: str, counts: set[int], near: list[float] | None, lines: list[dict]) -> list[float] | None:
    """A model can get one sign of a short Chinese word wrong, as 到达去 for the 到这去 on a button. Another word on the screen
    is worse than the model's rough box, so this counts only right at that box, and not if two words could be the one it meant."""
    if not near or not _is_han(wanted) or not MISREAD_SIGNS[0] <= len(wanted) <= MISREAD_SIGNS[1]:
        return None
    found: dict[str, tuple[tuple[float, float], list[float]]] = {}
    for text, box, words in _windows(counts, "", lines + _wrapped(lines)):
        if _one_sign_off(wanted, text) and _is_solid(box, words) and _away(box, near) <= MISREAD_REACH:
            place = ((_away(box, near), _distance(box, near)), box)
            found[text] = min(found.get(text, place), place, key=lambda seen: seen[0])
    return next(iter(found.values()))[1] if len(found) == 1 else None


def snap_to_text(label: str, near: list[float] | None, lines: list[dict]) -> list[float] | None:
    """Finds the words that spell the label, weighing the spelling against the distance from the model's guess.

    `lines` holds {"text", "words": [{"text", "box"}]} with boxes in 0-1 coordinates.
    """
    wanted = _normal(label)
    size = len(wanted.split())
    if not wanted or size == 0:
        return None
    # Chinese and Thai have no spaces, so one label can span several recognised words.
    spaced = wanted.isascii()
    counts = {size - 1, size, size + 1} | (set() if spaced else set(range(1, MAX_UNSPACED_WORDS + 1)))
    joiner = " " if spaced else ""
    wanted = joiner.join(wanted.split())
    return _spelled(wanted, counts, joiner, near, lines) or _spelled(wanted, counts, joiner, near, _wrapped(lines)) or _misread(wanted, counts, near, lines)


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


def _looks_like_icon(box: list[float] | None, frame_ratio: float) -> bool:
    return bool(box) and not _is_large(box) and not _is_stretched(box, frame_ratio)


def _same_row(near: list[float], box: list[float], slack: float = ROW_SLACK) -> bool:
    return max(near[1] - box[3], box[1] - near[3]) <= slack * (near[3] - near[1])


def snap_to_icon(near: list[float] | None, icons: list[list[float]], frame_ratio: float) -> list[float] | None:
    """Takes the closest icon under the model's guess when the guess looks like an icon at all. An icon in another row or
    off to the side is the next button: real calls landed there with a right guess.

    `frame_ratio` is the frame's width over its height: boxes are in shares of the frame, not in real proportions.
    """
    if not _looks_like_icon(near, frame_ratio):
        return None
    under = [icon for icon in icons if _same_row(near, icon) and min(near[2], icon[2]) > max(near[0], icon[0])]
    box = min(under, key=lambda candidate: _distance(candidate, near), default=None)
    return box if box and _distance(box, near) <= ICON_REACH ** 2 else None


def _is_caption(near: list[float] | None, words: list[float], frame_ratio: float) -> bool:
    """Words beside an icon in its row: a target with no words of its own borrows the nearest ones for its label."""
    return (_looks_like_icon(near, frame_ratio) and _same_row(near, words, CAPTION_SLACK)
            and not words[0] <= center(near)[0] <= words[2] and not near[0] <= center(words)[0] <= near[2])


def _whole(near: list[float] | None, words: list[float]) -> str | None:
    """The model means all of its large box: a card or a field, not the words that name it, which are lost in the window.
    Words only half in say the box is off, and there the words stand. On the bench the box covered 87% of the target
    against 10%, and its middle missed 3 of 152 that the words hit. Returns the source of the box, if it is to be taken."""
    if not _is_large(near) or (near[2] - near[0]) * (near[3] - near[1]) > WHOLE_MAX_AREA:
        return None
    if near[0] <= words[0] and words[2] <= near[2] and near[1] <= words[1] and words[3] <= near[3]:
        return "whole"
    return "field" if center(words)[1] < near[1] and near[1] - words[3] <= FIELD_GAP and abs(words[0] - near[0]) <= FIELD_INDENT else None


def locate(jpeg: bytes, width: int, height: int, answer: dict, lines: list[dict] | None = None,
           icons: list[list[float]] | None = None, marks: dict[int, list[float]] | None = None) -> dict:
    """Picks the final box: a mark, text on screen or the large box it stands in, SAM by name, an icon, then the model's own box.

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
    snapped = snap_to_text(target["label"], near, lines or [])
    if snapped and not _is_caption(near, snapped, width / height):
        whole = _whole(near, snapped)
        return {"box": near if whole else snapped, "source": whole or "text", "ms": 0}
    ms = 0
    if target["object"] and not _is_large(near):  # A large target is easy to hit, a search would only cost time.
        try:
            found = snap_with_sam(jpeg, width, height, target["object"], near)
        except meta.MetaError:  # SAM is an upgrade: the hint must still go out without it.
            found = {"box": None, "ms": 0}
        ms = found["ms"]
        if found["box"]:
            return {"box": found["box"], "source": "sam", "ms": ms}
    if icons is None and _looks_like_icon(near, width / height):
        try:
            icons = find_icons(jpeg, width, height)
        except meta.MetaError:
            icons = []
    icon = snap_to_icon(near, icons or [], width / height)
    if icon:
        return {"box": icon, "source": "icon", "ms": ms}
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
