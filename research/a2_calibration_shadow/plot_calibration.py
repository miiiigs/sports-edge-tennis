"""Render reliability diagrams from the committed calibration curve data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _points(bins: list[dict]) -> tuple[list[float], list[float], list[int]]:
    xs: list[float] = []
    ys: list[float] = []
    counts: list[int] = []
    for bucket in bins:
        if not bucket["n"]:
            continue
        xs.append(float(bucket["mean_probability"]))
        ys.append(float(bucket["observed"]))
        counts.append(int(bucket["n"]))
    return xs, ys, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--curves",
        default=Path("research/a2_calibration_shadow/artifacts/calibration_curves.json"),
        type=Path,
    )
    parser.add_argument(
        "--output-dir",
        default=Path("research/a2_calibration_shadow/artifacts"),
        type=Path,
    )
    args = parser.parse_args()

    curves = json.loads(args.curves.read_text(encoding="utf-8"))
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.8), sharey=True)
    for axis, dataset, title in zip(
        axes,
        ("validation_2022", "test"),
        ("Validation 2022", "Test 2023-2025"),
    ):
        block = curves[dataset]
        axis.plot([0.5, 1.0], [0.5, 1.0], color="#888888", linewidth=1, linestyle="--")
        for method, label, colour in (
            ("baseline", "Agent 2 baseline", "#1f77b4"),
            ("selected", "Temperature-calibrated", "#d62728"),
        ):
            xs, ys, counts = _points(block[method])
            sizes = [max(12.0, min(120.0, count / 90.0)) for count in counts]
            axis.plot(xs, ys, marker="o", markersize=4, linewidth=1.6, color=colour, label=label)
            axis.scatter(xs, ys, s=sizes, color=colour, alpha=0.18, edgecolors="none")
        axis.set_title(title)
        axis.set_xlabel("Mean predicted confidence")
        axis.set_xlim(0.5, 1.0)
        axis.set_ylim(0.45, 1.0)
        axis.grid(alpha=0.25)
        axis.legend(loc="upper left", fontsize=8)
    axes[0].set_ylabel("Observed win rate")
    figure.suptitle("Agent 2 selected-side reliability")
    figure.tight_layout()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("svg", "png"):
        figure.savefig(args.output_dir / f"calibration_reliability.{suffix}", dpi=160)
    plt.close(figure)
    print(f"wrote {args.output_dir / 'calibration_reliability.svg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
