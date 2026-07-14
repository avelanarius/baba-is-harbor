#!/usr/bin/env python3
"""Generate corrected resource-cutoff charts from processed Lake rerun data."""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path


SERIES = [
    ("GPT-5.6 Sol", "#2563eb"),
    ("Claude Fable 5", "#ea580c"),
]
METRICS = [
    ("agent_sec", "Wall-clock cutoff", "minutes", 1 / 60),
    ("cost_usd", "Per-task cost cutoff", "USD", 1),
    ("total_tokens", "Per-task token cutoff", "million tokens", 1 / 1_000_000),
]


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def successful_values(rows: list[dict[str, str]], model: str, field: str, scale: float) -> list[float]:
    return sorted(
        float(row[field]) * scale
        for row in rows
        if row["model"] == model and row["status"] == "success"
    )


def ticks(maximum: float, count: int = 5) -> list[float]:
    return [maximum * index / count for index in range(count + 1)]


def label(value: float, unit: str) -> str:
    if unit == "USD":
        return f"${value:.1f}"
    if unit == "minutes":
        return f"{value:.0f}"
    return f"{value:.1f}"


def svg_chart(rows: list[dict[str, str]], inverted: bool) -> str:
    width, height = 1040, 1180
    panel_x, panel_width = 115, 820
    panel_height, gap, top = 245, 82, 190
    solved_max = max(
        len(successful_values(rows, model, "agent_sec", 1)) for model, _ in SERIES
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Inter,system-ui,sans-serif;fill:#172033}.title{font-size:26px;font-weight:700}.sub{font-size:14px;fill:#526071}.panel{font-size:18px;font-weight:650}.axis{font-size:12px;fill:#526071}.legend{font-size:14px;font-weight:600}.grid{stroke:#dce2ea;stroke-width:1}.frame{stroke:#738097;stroke-width:1.2;fill:none}.line{fill:none;stroke-width:3;stroke-linejoin:round;stroke-linecap:round}.dot{stroke:#fff;stroke-width:1.5}</style>',
        f'<text x="{width / 2}" y="48" text-anchor="middle" class="title">Lake rerun: resource cutoff versus tasks solved</text>',
        f'<text x="{width / 2}" y="76" text-anchor="middle" class="sub">Only successful trials advance a curve; every line stops at its model\'s last observed success.</text>',
    ]
    legend_x = width / 2 - 170
    for index, (model, color) in enumerate(SERIES):
        x = legend_x + index * 255
        parts += [
            f'<line x1="{x}" y1="116" x2="{x + 30}" y2="116" stroke="{color}" stroke-width="4"/>',
            f'<text x="{x + 39}" y="121" class="legend">{html.escape(model)}</text>',
        ]

    for metric_index, (field, title, unit, scale) in enumerate(METRICS):
        y = top + metric_index * (panel_height + gap)
        values = {
            model: successful_values(rows, model, field, scale)
            for model, _ in SERIES
        }
        resource_max = max(max(items) for items in values.values()) * 1.04
        x_max = solved_max if inverted else resource_max
        y_max = resource_max if inverted else solved_max

        sx = lambda value: panel_x + value / x_max * panel_width
        sy = lambda value: y + panel_height - value / y_max * panel_height
        parts.append(f'<text x="{panel_x}" y="{y - 24}" class="panel">{html.escape(title)}</text>')

        x_ticks = range(0, solved_max + 1) if inverted else ticks(resource_max)
        y_ticks = ticks(resource_max) if inverted else range(0, solved_max + 1)
        for value in x_ticks:
            px = sx(float(value))
            parts.append(f'<line x1="{px:.2f}" y1="{y}" x2="{px:.2f}" y2="{y + panel_height}" class="grid"/>')
            tick_text = str(value) if inverted else label(float(value), unit)
            parts.append(f'<text x="{px:.2f}" y="{y + panel_height + 20}" text-anchor="middle" class="axis">{tick_text}</text>')
        for value in y_ticks:
            py = sy(float(value))
            parts.append(f'<line x1="{panel_x}" y1="{py:.2f}" x2="{panel_x + panel_width}" y2="{py:.2f}" class="grid"/>')
            tick_text = label(float(value), unit) if inverted else str(value)
            parts.append(f'<text x="{panel_x - 13}" y="{py + 4:.2f}" text-anchor="end" class="axis">{tick_text}</text>')
        parts.append(f'<rect x="{panel_x}" y="{y}" width="{panel_width}" height="{panel_height}" class="frame"/>')

        for model, color in SERIES:
            points = [(0.0, 0.0)]
            for count, resource in enumerate(values[model], 1):
                if inverted:
                    points.extend([(count - 1, resource), (count, resource)])
                else:
                    points.extend([(resource, count - 1), (resource, count)])
            coords = " ".join(f"{sx(a):.2f},{sy(b):.2f}" for a, b in points)
            parts.append(f'<polyline points="{coords}" stroke="{color}" class="line"/>')
            for count, resource in enumerate(values[model], 1):
                a, b = (count, resource) if inverted else (resource, count)
                parts.append(f'<circle cx="{sx(a):.2f}" cy="{sy(b):.2f}" r="4" fill="{color}" class="dot"/>')

        x_axis = "Tasks solved" if inverted else f"Cutoff ({unit})"
        y_axis = f"Cutoff ({unit})" if inverted else "Tasks solved"
        parts.append(f'<text x="{panel_x + panel_width / 2}" y="{y + panel_height + 49}" text-anchor="middle" class="axis">{html.escape(x_axis)}</text>')
        parts.append(f'<text transform="translate(31 {y + panel_height / 2}) rotate(-90)" text-anchor="middle" class="axis">{html.escape(y_axis)}</text>')

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, nargs="?", default=Path("processed_data.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    rows = load_rows(args.data)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "cutoff-vs-solved.svg": svg_chart(rows, inverted=False),
        "solved-vs-cutoff.svg": svg_chart(rows, inverted=True),
    }
    for name, content in outputs.items():
        path = args.output_dir / name
        path.write_text(content)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
