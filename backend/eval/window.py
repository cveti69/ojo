#!/usr/bin/env python3
"""What our floating window on the frame costs the agent, and what helps.

    python3 -m eval.window --detect
    python3 -m eval.window --model <id> --effort minimal --repeat 2

A window with a real hint from the step before is drawn on every stand frame, clear of the target or
over it. --detect compares finding the window by its border with searching for the picture it shows.
The main run compares three modes on the model: raw (the frame as it is), border (a coloured border
the model is told about) and hidden (the server finds the border, paints the window grey and gives
the model its place). Composites stay in memory; the ones Vision reads live in a folder next to the
frames until the run ends. Frames go only to the Meta API.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import shutil
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageStat

from app import grounding, hint, step, window

from .run import FRAMES, HERE, QUALITY, REPORTS, center_inside, load_lines, to_unit

LONG_SIDE = 1000
WINDOW_AREA = 0.10
MARGIN = 0.03
HIDDEN_ENOUGH = 0.6
SPOTS = {"left-middle": (0, 0.5), "right-middle": (1, 0.5), "left-top": (0, 0.2), "right-top": (1, 0.2),
         "left-bottom": (0, 0.8), "right-bottom": (1, 0.8)}
MOVE_WORDS = re.compile(r"сдвин|подвин|передвин|отодвин|перетащ|перемест|убери|отведи|оттащи")
WINDOW_WORDS = re.compile(r"окн|окош|подсказк|картинк")
BORDER_RULE = "\nOur floating window is the one with a thick magenta border: everything inside the border is ours."
PICTURE_SCALE = 0.25
PICTURE_STEP = 2
PICTURE_TOLERANCE = 30


def window_size(width: int, height: int) -> tuple[int, int]:
    side = math.sqrt(WINDOW_AREA * width * height * 3 / 4)
    return round(side), round(side * 4 / 3)


def spot(name: str, width: int, height: int) -> tuple[int, int, int, int]:
    size = window_size(width, height)
    column, share = SPOTS[name]
    x = round(width * MARGIN) if column == 0 else round(width * (1 - MARGIN)) - size[0]
    y = round((height - size[1]) * share)
    return x, y, x + size[0], y + size[1]


def overlap(first: tuple, second: list[float]) -> float:
    """The share of `second` that `first` covers."""
    across = min(first[2], second[2]) - max(first[0], second[0])
    down = min(first[3], second[3]) - max(first[1], second[1])
    if across <= 0 or down <= 0:
        return 0.0
    return across * down / ((second[2] - second[0]) * (second[3] - second[1]))


def clear_spot(truth: list[float], width: int, height: int) -> tuple[int, int, int, int] | None:
    grown = [truth[0] * width - 16, truth[1] * height - 16, truth[2] * width + 16, truth[3] * height + 16]
    for name in SPOTS:
        rect = spot(name, width, height)
        if overlap(rect, grown) == 0:
            return rect
    return None


def covering_spot(truth: list[float], width: int, height: int) -> tuple[int, int, int, int] | None:
    """The window slides along either edge of the screen, so it hides what sits in those columns."""
    size = window_size(width, height)
    pixels = [truth[0] * width, truth[1] * height, truth[2] * width, truth[3] * height]
    y = round((pixels[1] + pixels[3]) / 2 - size[1] / 2)
    y = min(max(y, round(height * 0.06)), round(height * 0.97) - size[1])
    best, share = None, 0.0
    for x in (round(width * MARGIN), round(width * (1 - MARGIN)) - size[0]):
        rect = (x, y, x + size[0], y + size[1])
        if overlap(rect, pixels) > share and rect[0] <= (pixels[0] + pixels[2]) / 2 <= rect[2]:
            best, share = rect, overlap(rect, pixels)
    return best if share >= HIDDEN_ENOUGH else None


def with_controls(picture: Image.Image) -> Image.Image:
    """iOS dims the window and puts its buttons on top while the person touches it."""
    dimmed = ImageEnhance.Brightness(picture).enhance(0.55)
    draw = ImageDraw.Draw(dimmed)
    size = dimmed.width
    draw.line((size * 0.07, size * 0.07, size * 0.15, size * 0.15), fill="white", width=9)
    draw.line((size * 0.15, size * 0.07, size * 0.07, size * 0.15), fill="white", width=9)
    draw.rectangle((size * 0.82, size * 0.07, size * 0.93, size * 0.15), outline="white", width=7)
    middle = dimmed.height / 2
    draw.polygon([(size * 0.45, middle - 44), (size * 0.45, middle + 44), (size * 0.58, middle)], fill="white")
    return dimmed


def card(text: str) -> Image.Image:
    """A card the phone draws by itself, so the server has no copy of it."""
    picture = Image.new("RGB", hint.SIZE, (25, 35, 65))
    ImageDraw.Draw(picture).text((360, 480), text, fill="white", anchor="mm", font_size=64)
    return picture


def paste(base: Image.Image, picture: Image.Image, rect: tuple[int, int, int, int]) -> Image.Image:
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    radius = round(width * 0.07)
    shape = Image.new("L", (width, height), 0)
    ImageDraw.Draw(shape).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    shadow = Image.new("L", base.size, 0)
    shadow.paste(shape.point(lambda value: value // 2), (rect[0], rect[1] + 12))
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    result = Image.composite(Image.new("RGB", base.size, (0, 0, 0)), base, shadow)
    result.paste(picture.resize((width, height), Image.Resampling.LANCZOS), rect[:2], shape)
    return result


def small_jpeg(image: Image.Image) -> tuple[bytes, int, int]:
    """Shrinks a frame the way the broadcast extension does."""
    scale = LONG_SIDE / max(image.size)
    shrunk = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    shrunk.save(output, format="JPEG", quality=QUALITY)
    return output.getvalue(), shrunk.width, shrunk.height


def load_spec() -> tuple[dict, dict[str, Image.Image], dict[str, Image.Image]]:
    """Reads the cases, the frames and, for each frame, the hint from the step before it."""
    spec = json.loads((HERE / "cases.json").read_text())
    bases = {path.stem: Image.open(path).convert("RGB") for path in sorted(FRAMES.glob("*.png"))}
    steps = [case for case in spec["cases"] if case.get("step")]
    said: dict[str, str] = {}
    for report in sorted(REPORTS.glob("*/results.json"), key=lambda path: path.stat().st_mtime):
        rows = json.loads(report.read_text()).get("rows", [])
        said.update({row["case"]: row["instruction"] for row in rows if row.get("instruction")})
    view = spec["view"]
    pictures = {}
    names = list(bases)
    for index, name in enumerate(names):
        source = next(case for case in steps if case["frame"] == names[max(index - 1, 0)])
        box = to_unit(source["truth"], view[0], view[1])
        jpeg = small_jpeg(bases[source["frame"]])[0]
        rendered = hint.render(jpeg, box, said.get(source["id"], "Нажми на выделенную кнопку"))
        pictures[name] = Image.open(io.BytesIO(rendered)).convert("RGB")
    return spec, bases, pictures


def find_by_picture(frame: bytes, picture: Image.Image, size: tuple[int, int]) -> list[float] | None:
    """The other way: slide the picture the server last sent over the frame, at a nearly known size."""
    with Image.open(io.BytesIO(frame)) as original:
        gray = original.convert("L")
    gray = gray.resize((round(gray.width * PICTURE_SCALE), round(gray.height * PICTURE_SCALE)), Image.Resampling.BOX)
    best, lowest = None, float("inf")
    for grow in (-2, 0, 2):
        width, height = round(size[0] * PICTURE_SCALE) + grow, round(size[1] * PICTURE_SCALE) + grow
        template = picture.convert("L").resize((width, height), Image.Resampling.BOX)
        for y in range(0, gray.height - height, PICTURE_STEP):
            for x in range(0, gray.width - width, PICTURE_STEP):
                difference = ImageChops.difference(gray.crop((x, y, x + width, y + height)), template)
                score = ImageStat.Stat(difference).mean[0]
                if score < lowest:
                    best, lowest = (x, y, x + width, y + height), score
    if lowest > PICTURE_TOLERANCE:
        return None
    return [best[0] / gray.width, best[1] / gray.height, best[2] / gray.width, best[3] / gray.height]


def same_place(found: list[float] | None, truth: list[float]) -> bool:
    if not found:
        return False
    return overlap(tuple(found), truth) >= 0.85 and overlap(tuple(truth), found) >= 0.85


def detect() -> None:
    _, bases, pictures = load_spec()
    tally: dict[str, dict] = {}
    for name, base in bases.items():
        width, height = base.size
        for spot_name in SPOTS:
            rect = spot(spot_name, width, height)
            truth = [rect[0] / width, rect[1] / height, rect[2] / width, rect[3] / height]
            known = pictures[name]
            for state, shown in (("hint", known), ("touched", with_controls(known)), ("card", card("Смотрю экран"))):
                plain = small_jpeg(paste(base, shown, rect))
                marked = small_jpeg(paste(base, with_controls(window.add_border(known)) if state == "touched"
                                          else window.add_border(shown), rect))
                row = tally.setdefault(state, {"total": 0, "border": 0, "picture": 0, "border_ms": [], "picture_ms": []})
                row["total"] += 1
                started = time.perf_counter()
                found = window.find(marked[0])
                row["border_ms"].append((time.perf_counter() - started) * 1000)
                row["border"] += same_place(found, truth)
                started = time.perf_counter()
                found = find_by_picture(plain[0], known, window_size(plain[1], plain[2]))
                row["picture_ms"].append((time.perf_counter() - started) * 1000)
                row["picture"] += same_place(found, truth)
        print(name, {state: (row["border"], row["picture"], row["total"]) for state, row in tally.items()}, flush=True)
    for state, row in tally.items():
        print(f"{state:8} border {row['border']}/{row['total']} {statistics.median(row['border_ms']):.1f} ms   "
              f"picture {row['picture']}/{row['total']} {statistics.median(row['picture_ms']):.0f} ms")
    false_alarms = sum(window.find(small_jpeg(base)[0]) is not None for base in bases.values())
    print(f"no window on the frame: border finds one on {false_alarms} of {len(bases)}")


def asked_to_move(answer: dict | None) -> bool:
    text = (answer or {}).get("instruction", "").lower()
    return bool(MOVE_WORDS.search(text) and WINDOW_WORDS.search(text))


def read_text(shots: dict, folder: Path) -> None:
    """Runs the same Vision engine the iPhone has over the full-size composites."""
    tool = HERE / "tools" / "ocr.swift"
    paths = sorted(str(path) for path in folder.glob("*.jpg"))
    for start in range(0, len(paths), 16):
        subprocess.run(["swift", str(tool), *paths[start:start + 16]], cwd=folder, check=True, capture_output=True)
    for number, shot in enumerate(shots.values()):
        shot["lines"] = load_lines(f"shot{number}", folder)


def prepare(spec: dict, bases: dict, pictures: dict, repeat: int) -> tuple[list[dict], dict]:
    view = spec["view"]
    folder = FRAMES / "window-tmp"
    folder.mkdir(exist_ok=True)
    jobs, shots = [], {}
    try:
        for case in spec["cases"]:
            base = bases[case["frame"]]
            width, height = base.size
            truth = to_unit(case["truth"], view[0], view[1])
            for place, rect in (("clear", clear_spot(truth, width, height)), ("covers", covering_spot(truth, width, height))):
                if rect is None:
                    continue
                for attempt in range(repeat):
                    touched = attempt % 2 == 1
                    key = (case["frame"], rect, touched)
                    if key not in shots:
                        shown = pictures[case["frame"]]
                        plain = paste(base, with_controls(shown) if touched else shown, rect)
                        marked = window.add_border(shown)
                        marked = paste(base, with_controls(marked) if touched else marked, rect)
                        plain.save(folder / f"shot{len(shots)}.jpg", quality=92)
                        jpeg, small_width, small_height = small_jpeg(plain)
                        shots[key] = {"plain": jpeg, "marked": small_jpeg(marked)[0], "width": small_width,
                                      "height": small_height, "rect": [rect[0] / width, rect[1] / height,
                                                                       rect[2] / width, rect[3] / height]}
                    jobs.append({"case": case, "truth": truth, "place": place, "attempt": attempt, "touched": touched,
                                 "shot": shots[key]})
        read_text(shots, folder)
    finally:
        shutil.rmtree(folder)
    return jobs, shots


def run_one(job: dict, mode: str, args) -> dict:
    case, shot, truth = job["case"], job["shot"], job["truth"]
    rect = shot["rect"]
    row = {"case": case["id"], "place": job["place"], "mode": mode, "attempt": job["attempt"], "touched": job["touched"],
           "step": bool(case.get("step")), "icon": bool(case.get("icon"))}
    prompt = step.build_prompt(case["goal"], case["history"])
    jpeg, lines, icons = shot["plain"], shot["lines"], shot["icons"]
    outside = lambda box: not center_inside(box, rect)  # noqa: E731
    if mode == "border":
        jpeg = shot["marked"]
        prompt += BORDER_RULE
    elif mode == "hidden":
        row["found"] = shot["found"] is not None
        if shot["found"]:
            found = shot["found"]
            jpeg = shot["hidden"]
            lines = [line for line in lines if not center_inside(line["box"], found)]
            lines = [dict(line, words=[word for word in line["words"] if not center_inside(word["box"], found)])
                     for line in lines]
            icons = [icon for icon in icons if not center_inside(icon, found)]
            prompt += window.RULE.format(box=[round(value * 1000) for value in found])
        else:
            jpeg = shot["marked"]
    try:
        reply = step.ask(jpeg, prompt, args.model, args.effort)
        answer = reply["answer"]
        row.update(ms=reply["ms"], tries=reply["tries"], action=(answer or {}).get("action"),
                   instruction=(answer or {}).get("instruction"), label=((answer or {}).get("target") or {}).get("label"),
                   asked=asked_to_move(answer))
        box = grounding.locate(jpeg, shot["width"], shot["height"], answer, lines, icons) if answer else {"box": None}
        row.update(box=box["box"], source=box.get("source"), hit=center_inside(box["box"], truth),
                   inside=bool(box["box"]) and not outside(box["box"]))
    except Exception as error:  # A failed call is a result too: it counts as a miss.
        row["error"] = str(error)[:300]
    return row


def summarize(rows: list[dict]) -> dict:
    summary: dict = {}
    for row in rows:
        group = summary.setdefault(f"{row['place']:6} {row['mode']:6}", {"total": 0, "hit": 0, "inside": 0, "asked": 0, "ms": []})
        group["total"] += 1
        group["hit"] += bool(row.get("hit"))
        group["inside"] += bool(row.get("inside"))
        group["asked"] += bool(row.get("asked"))
        if "ms" in row:
            group["ms"].append(row["ms"])
    for group in summary.values():
        times = sorted(group.pop("ms"))
        group["median_ms"] = round(statistics.median(times)) if times else None
        group["p90_ms"] = times[math.ceil(len(times) * 0.9) - 1] if times else None
    return dict(sorted(summary.items()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detect", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", default="minimal")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--modes", type=lambda text: text.split(","), default=["raw", "border", "hidden"])
    args = parser.parse_args()
    if args.detect:
        detect()
        return

    spec, bases, pictures = load_spec()
    jobs, shots = prepare(spec, bases, pictures, args.repeat)
    print(f"{len(jobs)} jobs on {len(shots)} composites", flush=True)
    with ThreadPoolExecutor(args.workers) as pool:
        def look(shot: dict) -> None:
            shot["icons"] = grounding.find_icons(shot["plain"], shot["width"], shot["height"])
            shot["found"] = window.find(shot["marked"])
            shot["hidden"] = window.hide(shot["marked"], shot["found"]) if shot["found"] else None
        list(pool.map(look, shots.values()))
        running = [pool.submit(run_one, job, mode, args) for job in jobs for mode in args.modes]
    rows = [job.result() for job in running]

    summary = summarize(rows)
    missed = sum(1 for shot in shots.values() if shot["found"] is None)
    target = REPORTS / f"window-{args.effort}-{time.strftime('%H%M%S')}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "results.json").write_text(json.dumps(
        {"model": args.model, "effort": args.effort, "summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
    print(args.model, args.effort)
    for name, group in summary.items():
        print(f"{name}  {group['hit']:3}/{group['total']:<3} hit  {group['inside']:3} inside  {group['asked']:3} asked  "
              f"median {group['median_ms']} ms  p90 {group['p90_ms']} ms")
    errors = sum(1 for row in rows if "error" in row)
    print(f"border missed on {missed} of {len(shots)} composites; errors: {errors}",
          next((row["error"][:200] for row in rows if "error" in row), ""))
    print("папка:", target)


if __name__ == "__main__":
    main()
