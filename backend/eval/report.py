#!/usr/bin/env python3
"""Bench report: turns results.json into annotated images and a page of misses.

    python3 -m eval.report <report folder>
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
FRAMES = HERE / "frames" / "small"
WIDTH = 360

MASKS = {"03-results": [[0.0, 0.195, 1.0, 0.24]]}  # The delivery line shows a name.

TRUTH_COLOR = "#00ff00"
PALETTE = ["#ff0000", "#0000ff", "#ff9900", "#9900ff", "#00bbbb", "#ff00ff", "#8b4513", "#008080"]
FIXED = {"direct": "#ff0000", "computer": "#0000ff", "sam": "#ff9900", "ocr": "#9900ff",
         "samtext": "#00bbbb", "grounded": "#ff00ff", "marks": "#8b4513"}


def mode_color(mode: str, modes: list[str]) -> str:
    """Maps a mode to its fixed colour, falling back to the palette in order."""
    if mode in FIXED:
        return FIXED[mode]
    return PALETTE[modes.index(mode) % len(PALETTE)]


def is_miss(row: dict, modes: list[str]) -> bool:
    """True when any mode missed on this row."""
    return any(not row.get(f"{mode}_hit") for mode in modes)


def to_unit_truth(truth: list, view_width: float, view_height: float) -> list[float]:
    """Converts a truth box in view pixels to 0-1 coordinates."""
    return [truth[0] / view_width, truth[1] / view_height, truth[2] / view_width, truth[3] / view_height]


def paint_masks(image: Image.Image, frame: str) -> None:
    """Paints the masked areas for a frame solid black on the image data."""
    width, height = image.size
    for area in MASKS.get(frame, []):
        box = [round(area[0] * width), round(area[1] * height),
               round(area[2] * width), round(area[3] * height)]
        ImageDraw.Draw(image).rectangle(box, fill=(0, 0, 0))


def draw_box(draw: ImageDraw.ImageDraw, box: list[float] | None, width: int, height: int, color: str) -> None:
    """Draws one 0-1 box as a thin outline, skipping null boxes."""
    if not box:
        return
    coords = [max(0.0, min(1.0, value)) for value in box]
    draw.rectangle([coords[0] * width, coords[1] * height, coords[2] * width, coords[3] * height],
                   outline=color, width=2)


def outcome(row: dict, mode: str) -> str:
    """Renders hit or miss for one mode."""
    return "hit" if row.get(f"{mode}_hit") else "miss"


def summary_table(summary: dict) -> str:
    """Renders the summary as one HTML table row per mode and group."""
    lines = ["<table><tr><th>mode</th><th>group</th><th>hits</th><th>iou</th>"
             "<th>median ms</th><th>p90 ms</th></tr>"]
    for mode, groups in summary.items():
        for group, stats in groups.items():
            cells = [html.escape(str(mode)), html.escape(str(group)),
                     html.escape(f"{stats.get('hits')}/{stats.get('total')}"),
                     html.escape(str(stats.get("iou"))), html.escape(str(stats.get("median_ms"))),
                     html.escape(str(stats.get("p90_ms")))]
            lines.append("<tr><td>" + "</td><td>".join(cells) + "</td></tr>")
    lines.append("</table>")
    return "\n".join(lines)


def legend(modes: list[str]) -> str:
    """Renders the colour key for the truth box and each mode."""
    items = [f'<span class="key"><i style="background:{html.escape(TRUTH_COLOR)}"></i>truth</span>']
    for mode in modes:
        items.append(f'<span class="key"><i style="background:{html.escape(mode_color(mode, modes))}"></i>'
                     f"{html.escape(mode)}</span>")
    return '<div class="legend">' + " ".join(items) + "</div>"


def private_card(row: dict, modes: list[str]) -> str:
    """Renders a private-frame row with only the case id and per-mode hit or miss."""
    parts = [f'<div class="card"><h3>{html.escape(str(row.get("case")))} · '
             f'{html.escape(str(row.get("repeat")))}</h3>',
             "<p>Private frame: image withheld.</p><ul>"]
    for mode in modes:
        parts.append(f"<li>{html.escape(mode)}: {html.escape(outcome(row, mode))}</li>")
    parts.append("</ul></div>")
    return "\n".join(parts)


def public_card(row: dict, case: dict, modes: list[str], image: str | None) -> str:
    """Renders one row with its image, labels and per-mode hit, time and source."""
    parts = [f'<div class="card"><h3>{html.escape(str(row.get("case")))} · '
             f'{html.escape(str(row.get("repeat")))}</h3>']
    if image:
        parts.append(f'<img src="{html.escape(image)}" alt="{html.escape(str(row.get("case")))}">')
    for key in ("goal", "action", "instruction", "label", "object"):
        value = row.get(key, case.get(key))
        if value is not None:
            parts.append(f"<p><b>{html.escape(key)}</b>: {html.escape(str(value))}</p>")
    parts.append("<ul>")
    for mode in modes:
        line = f"{mode}: {outcome(row, mode)}"
        if row.get(f"{mode}_ms") is not None:
            line += f" · {row.get(f'{mode}_ms')} ms"
        if row.get(f"{mode}_source") is not None:
            line += f" · {row.get(f'{mode}_source')}"
        parts.append(f"<li>{html.escape(line)}</li>")
    parts.append("</ul></div>")
    return "\n".join(parts)


def page(title: str, summary: dict, modes: list[str], cards: list[str]) -> str:
    """Wraps the summary table, legend and cards in one self-contained page."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body{{font-family:sans-serif;margin:24px;color:#222}}
table{{border-collapse:collapse;margin:16px 0}}
th,td{{border:1px solid #999;padding:4px 10px;text-align:left}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}}
.card{{border:1px solid #ccc;border-radius:8px;padding:12px}}
.card img{{width:100%;height:auto;display:block}}
.legend{{margin:12px 0}}
.key{{margin-right:12px;white-space:nowrap}}
.key i{{display:inline-block;width:12px;height:12px;margin-right:4px;border:1px solid #333}}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
{summary_table(summary)}
{legend(modes)}
<div class="grid">
""" + "\n".join(cards) + "\n</div>\n</body>\n</html>\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report")
    args = parser.parse_args()
    folder = Path(args.report)
    results = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    spec = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    view_width, view_height = spec["view"]
    private = set(spec.get("private_frames", []))
    by_id = {case["id"]: case for case in spec["cases"]}
    modes = list(results["summary"].keys())
    rows = sorted(results["rows"], key=lambda row: (not is_miss(row, modes), str(row.get("case")),
                                                   str(row.get("repeat"))))
    images = folder / "images"
    images.mkdir(exist_ok=True)
    cards = []
    for row in rows:
        case = by_id.get(row.get("case"), {})
        frame = str(case.get("frame", ""))
        if frame in private:
            cards.append(private_card(row, modes))
            continue
        name = f"{row.get('case')}-{row.get('repeat')}.jpg"
        rel = None
        if frame and (FRAMES / f"{frame}.jpg").exists():
            image = Image.open(FRAMES / f"{frame}.jpg").convert("RGB")
            truth = to_unit_truth(case["truth"], view_width, view_height)
            paint_masks(image, frame)
            height = round(image.size[1] * WIDTH / image.size[0])
            image = image.resize((WIDTH, height), Image.LANCZOS)
            draw = ImageDraw.Draw(image)
            draw_box(draw, truth, WIDTH, height, TRUTH_COLOR)
            for mode in modes:
                draw_box(draw, row.get(f"{mode}_box"), WIDTH, height, mode_color(mode, modes))
            image.save(images / name)
            rel = f"images/{name}"
        cards.append(public_card(row, case, modes, rel))
    (folder / "report.html").write_text(page(folder.name, results["summary"], modes, cards), encoding="utf-8")
    print(f"wrote {len(cards)} cards to {folder / 'report.html'}")


if __name__ == "__main__":
    main()
