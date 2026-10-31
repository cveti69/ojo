#!/usr/bin/env python3
"""Accuracy bench: runs each case through the pointing modes and writes results.json.

    python3 -m eval.run --modes direct,sam,ocr,samtext --model <id> --effort minimal --repeat 2

Modes: direct (the model's box), sam (SAM from the model's box), ocr (the label snapped to Apple
Vision text), samtext (SAM finds the object by name, closest to the model's box), computer (the
model's computer tool, a single tap point), grounded (grounding.locate from the plain answer),
marks (numbered marks on the frame, then grounding.locate). Icons for grounded and marks are found
once per frame, like the server does next to the model. Needs Pillow, so run it from .venv.
Frames stay on this machine and go only to the Meta API.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import grounding, meta, step

HERE = Path(__file__).parent
FRAMES = HERE / "frames"
OCR = FRAMES / "ocr"
REPORTS = HERE / "reports"
QUALITY = 60


def prepare_frames(long_side: int) -> dict:
    """Shrinks every frame the way the broadcast extension will."""
    folder = FRAMES / ("small" if long_side == 1000 else f"small-{long_side}")
    folder.mkdir(exist_ok=True)
    frames = {}
    for source in sorted(FRAMES.glob("*.png")):
        target = folder / f"{source.stem}.jpg"
        if not target.exists():
            subprocess.run(
                ["sips", "-Z", str(long_side), "-s", "format", "jpeg", "-s", "formatOptions", str(QUALITY),
                 str(source), "--out", str(target)],
                check=True, capture_output=True,
            )
        output = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(target)],
                                check=True, capture_output=True, text=True).stdout
        numbers = [int(line.split()[-1]) for line in output.splitlines()[1:]]
        frames[source.stem] = {"jpeg": target.read_bytes(), "width": numbers[0], "height": numbers[1],
                               "lines": load_lines(source.stem)}
    return frames


def load_lines(name: str) -> list[dict]:
    """Groups Vision words back into lines; a line record starts with the \\u0001 marker."""
    path = OCR / f"{name}.json"
    if not path.exists():
        return []
    lines, words = [], []
    for item in json.loads(path.read_text()):
        if item["text"].startswith("\u0001"):
            lines.append({"text": item["text"][1:], "box": item["box"], "words": words})
            words = []
        else:
            words.append(item)
    return lines


def to_unit(box: list, width: float, height: float) -> list[float]:
    return [box[0] / width, box[1] / height, box[2] / width, box[3] / height]


def center_inside(box: list[float] | None, truth: list[float]) -> bool:
    if not box:
        return False
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return truth[0] <= x <= truth[2] and truth[1] <= y <= truth[3]


def iou(box: list[float] | None, truth: list[float]) -> float:
    if not box:
        return 0.0
    width = min(box[2], truth[2]) - max(box[0], truth[0])
    height = min(box[3], truth[3]) - max(box[1], truth[1])
    if width <= 0 or height <= 0:
        return 0.0
    common = width * height
    return common / ((box[2] - box[0]) * (box[3] - box[1]) + (truth[2] - truth[0]) * (truth[3] - truth[1]) - common)


def run_direct(case: dict, frame: dict, args) -> dict:
    prompt = step.build_prompt(case["goal"], case["history"])
    reply = step.ask(frame["jpeg"], prompt, args.model, args.effort)
    answer = reply["answer"]
    box = None
    if answer and answer["target"]["box"]:
        box = [value / 1000 for value in answer["target"]["box"]]
    return {"box": box, "ms": reply["ms"], "tries": reply["tries"], "answer": answer, "usage": reply["usage"]}


def refine_with_sam(frame: dict, box: list[float]) -> dict:
    """Sends the model's box to SAM and takes the box of the mask it finds."""
    width, height = frame["width"], frame["height"]
    pixels = [round(box[0] * width), round(box[1] * height), round(box[2] * width), round(box[3] * height)]
    pixels = [max(pixels[0], 0), max(pixels[1], 0), min(pixels[2], width), min(pixels[3], height)]
    found = meta.sam(frame["jpeg"], meta.sam_box_prompt(pixels, width, height))
    if not found["boxes"]:
        return {"box": box, "ms": found["ms"]}
    return {"box": to_unit(found["boxes"][0]["box"], width, height), "ms": found["ms"]}


def run_computer(case: dict, frame: dict, args) -> dict:
    image = meta.data_url(frame["jpeg"])
    text = (f"A person uses this iPhone app. Their goal: {case['goal']}. "
            f"Steps done so far: {'; '.join(case['history']) or 'none'}. "
            "Click the ONE element they should tap next.")
    history: list = [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": text}, {"type": "input_image", "image_url": image},
    ]}]
    started = time.perf_counter()
    for _ in range(3):
        reply = meta.respond({
            "model": args.model, "store": False, "reasoning": {"effort": args.effort},
            "tools": [{"type": "computer"}], "input": history,
        })
        call = next((item for item in reply["output"] if item["type"] == "computer_call"), None)
        if call is None:
            break
        points = [action for action in call["actions"] if "x" in action]
        if points:
            x, y = points[0]["x"] / frame["width"], points[0]["y"] / frame["height"]
            return {"box": [x, y, x, y], "ms": round((time.perf_counter() - started) * 1000)}
        if call.get("pending_safety_checks"):
            break  # Meta asks a person to confirm these, so the bench does not answer them.
        history += [item for item in reply["output"] if item["type"] != "reasoning"]
        history.append({"type": "computer_call_output", "call_id": call["call_id"],
                        "acknowledged_safety_checks": [],
                        "output": {"type": "computer_screenshot", "image_url": image}})
    return {"box": None, "ms": round((time.perf_counter() - started) * 1000)}


def run_marks(case: dict, frame: dict, args) -> dict:
    prompt = step.build_prompt(case["goal"], case["history"], marks=True)
    reply = step.ask(frame["marked"], prompt, args.model, args.effort)
    answer = reply["answer"]
    if not answer:
        return {"box": None, "ms": reply["ms"], "source": "failed"}
    found = grounding.locate(frame["jpeg"], frame["width"], frame["height"], answer, frame["lines"], frame["icons"],
                             frame["marks"])
    return {"box": found["box"], "ms": reply["ms"] + found["ms"], "source": found["source"]}


def record(row: dict, mode: str, result: dict, truth: list[float]) -> None:
    row[f"{mode}_box"] = result["box"]
    if "source" in result:
        row[f"{mode}_source"] = result["source"]
    row[f"{mode}_hit"] = center_inside(result["box"], truth)
    row[f"{mode}_iou"] = iou(result["box"], truth)
    row[f"{mode}_ms"] = result["ms"]


def run_case(case: dict, frame: dict, truth: list[float], args, repeat: int) -> dict:
    row = {"case": case["id"], "repeat": repeat, "step": bool(case.get("step")), "icon": bool(case.get("icon"))}
    try:
        if "computer" in args.modes:
            record(row, "computer", run_computer(case, frame, args), truth)
        if "marks" in args.modes:
            record(row, "marks", run_marks(case, frame, args), truth)
        if not set(args.modes) - {"computer", "marks"}:
            return row
        direct = run_direct(case, frame, args)
        answer = direct["answer"] or {}
        target = answer.get("target") or {}
        record(row, "direct", direct, truth)
        row.update({
            "tries": direct["tries"],
            "action": answer.get("action"),
            "label": target.get("label"),
            "object": target.get("object"),
            "instruction": answer.get("instruction"),
            "insert_texts": answer.get("insert_texts"),
            "stop": answer.get("needs_user_confirm"),
            "tokens": direct["usage"].get("total_tokens"),
        })
        near = direct["box"]
        snapped = grounding.snap_to_text(target.get("label") or "", near, frame["lines"])
        if "ocr" in args.modes:
            record(row, "ocr", {"box": snapped or near, "ms": direct["ms"]}, truth)
            row["ocr_found"] = bool(snapped)
        if "sam" in args.modes and near:
            record(row, "sam", refine_with_sam(frame, near), truth)
        if "samtext" in args.modes and target.get("object"):
            found = grounding.snap_with_sam(frame["jpeg"], frame["width"], frame["height"], target["object"], near)
            row["samtext_found"] = found["found"]
            record(row, "samtext", {"box": found["box"] or near, "ms": found["ms"]}, truth)
        if "grounded" in args.modes and direct["answer"]:
            found = grounding.locate(frame["jpeg"], frame["width"], frame["height"], direct["answer"], frame["lines"],
                                     frame["icons"])
            record(row, "grounded", {"box": found["box"], "ms": direct["ms"] + found["ms"], "source": found["source"]}, truth)
    except Exception as error:  # A failed call is a result too: it counts as a miss.
        row["error"] = str(error)[:300]
    return row


def summarize(rows: list[dict], key: str) -> dict:
    groups = {"all": rows, "steps": [r for r in rows if r["step"]], "icons": [r for r in rows if r["icon"]],
              "text": [r for r in rows if not r["icon"]]}
    summary = {}
    for name, group in groups.items():
        hits = [bool(r.get(f"{key}_hit")) for r in group]
        scores = [r[f"{key}_iou"] for r in group if f"{key}_iou" in r]
        times = sorted(r[f"{key}_ms"] for r in group if f"{key}_ms" in r)
        summary[name] = {
            "hits": sum(hits),
            "total": len(hits),
            "iou": round(statistics.mean(scores), 3) if scores else None,
            "median_ms": round(statistics.median(times)) if times else None,
            "p90_ms": times[math.ceil(len(times) * 0.9) - 1] if times else None,
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", type=lambda text: text.split(","), default=["direct"])
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", default="minimal")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--long-side", type=int, default=1000)
    parser.add_argument("--only", default="", help="comma-separated case ids")
    args = parser.parse_args()

    spec = json.loads((HERE / "cases.json").read_text())
    view_width, view_height = spec["view"]
    frames = prepare_frames(args.long_side)
    if "marks" in args.modes:
        for frame in frames.values():  # The marked copies stay in memory, they are never written out.
            frame["marked"], frame["marks"] = grounding.draw_marks(frame["jpeg"], frame["lines"])
    if "grounded" in args.modes or "marks" in args.modes:
        for frame in frames.values():
            frame["icons"] = grounding.find_icons(frame["jpeg"], frame["width"], frame["height"])
    only = set(filter(None, args.only.split(",")))
    cases = [case for case in spec["cases"] if not only or case["id"] in only]

    jobs = []
    with ThreadPoolExecutor(args.workers) as pool:
        for repeat in range(args.repeat):
            for case in cases:
                truth = to_unit(case["truth"], view_width, view_height)
                jobs.append(pool.submit(run_case, case, frames[case["frame"]], truth, args, repeat))
    rows = [job.result() for job in jobs]

    keys = [mode for mode in ("direct", "computer", "sam", "ocr", "samtext", "grounded", "marks") if mode in args.modes]
    summary = {key: summarize(rows, key) for key in keys}
    label = f"{args.model}-{args.effort}-{args.long_side}-{time.strftime('%H%M%S')}"
    target = REPORTS / label
    target.mkdir(parents=True, exist_ok=True)
    (target / "results.json").write_text(json.dumps(
        {"model": args.model, "effort": args.effort, "long_side": args.long_side, "summary": summary, "rows": rows},
        ensure_ascii=False, indent=1))
    print(args.model, args.effort, args.long_side)
    for key, groups in summary.items():
        for name, group in groups.items():
            print(f"{key:9} {name:6} {group['hits']:3}/{group['total']:<3} iou {group['iou']}  "
                  f"median {group['median_ms']} ms  p90 {group['p90_ms']} ms")
    errors = sum(1 for row in rows if "error" in row)
    print("ошибок:", errors, next((row["error"][:200] for row in rows if "error" in row), ""))
    print("папка:", target)


if __name__ == "__main__":
    main()
