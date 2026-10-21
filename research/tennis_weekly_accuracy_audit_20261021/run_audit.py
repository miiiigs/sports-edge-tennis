"""CLI entry point for the read-only weekly accuracy audit.

Example::

    python -m research.tennis_weekly_accuracy_audit_20261021.run_audit `
        --dataset tmp/tennis_forensics_20261021.jsonl `
        --start 2026-10-19 --end 2026-10-21 `
        --output-dir research/tennis_weekly_accuracy_audit_20261021/artifacts
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from .dataset import dataset_manifest_summary, load_dataset
from .reconciliation import (
    DEFAULT_HOLDOUT_START,
    DEFAULT_MINIMUM_PROBABILITY,
    reconcile_week,
)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}") from exc


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, digits: int = 4) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _manager_table(populations: dict[str, Any]) -> list[str]:
    rows = [
        ("Manager >=60% (official)", "manager_primary"),
        ("Manager all probabilities", "manager_all_probabilities"),
        ("Aligned headline", "aligned"),
        ("  aligned Manager subset", "aligned_manager_subset"),
        ("  aligned tracking subset", "aligned_tracking_subset"),
    ]
    lines = [
        "| Population | Picks | Scored | W | L | Pending | Void | Accuracy | 95% CI | Mean p | Gap | Brier | Log loss |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for label, key in rows:
        item = populations[key]
        ci = (
            f"{_pct(item['ci_low'])}-{_pct(item['ci_high'])}"
            if item.get("ci_low") is not None
            else "-"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    label,
                    str(item["picks"]),
                    str(item["scored"]),
                    str(item["wins"]),
                    str(item["losses"]),
                    str(item["pending"]),
                    str(item["voids"]),
                    _pct(item["accuracy"]),
                    ci,
                    _pct(item["mean_probability"]),
                    (
                        f"{item['calibration_gap'] * 100:+.1f}pp"
                        if item["calibration_gap"] is not None
                        else "-"
                    ),
                    _num(item["brier"]),
                    _num(item["log_loss"]),
                ]
            )
            + " |"
        )
    return lines


def _band_table(subgroups: dict[str, Any]) -> list[str]:
    lines = [
        "| Probability band | Scored | W | L | Accuracy | Mean p | Gap | Brier |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in subgroups["bands"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["band"],
                    str(row["n"]),
                    str(row["wins"]),
                    str(row["losses"]),
                    _pct(row["accuracy"]),
                    _pct(row["mean_probability"]),
                    (
                        f"{row['calibration_gap'] * 100:+.1f}pp"
                        if row["calibration_gap"] is not None
                        else "-"
                    ),
                    _num(row["brier"]),
                ]
            )
            + " |"
        )
    return lines


def _filter_table(experiments: dict[str, Any]) -> list[str]:
    lines = [
        "| Filter | Full keep (n / acc) | Full delta | Holdout keep (n / acc) | Holdout delta | Coverage (holdout) | Week keep (n / acc) | Week rejected (n / acc) |",
        "| --- | --- | ---: | --- | ---: | ---: | --- | --- |",
    ]
    for row in experiments["filters"]:
        full = row["full"]["kept"]
        hold = row["holdout"]["kept"]
        week = row["week"]["kept"]
        rejected = row["week"]["rejected"]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["filter"],
                    f"{full['n']} / {_pct(full['accuracy'])}",
                    f"{row['full']['accuracy_delta'] * 100:+.1f}pp"
                    if row["full"]["accuracy_delta"] is not None
                    else "-",
                    f"{hold['n']} / {_pct(hold['accuracy'])}",
                    f"{row['holdout']['accuracy_delta'] * 100:+.1f}pp"
                    if row["holdout"]["accuracy_delta"] is not None
                    else "-",
                    _pct(row["holdout"]["coverage"]),
                    f"{week['n']} / {_pct(week['accuracy'])}",
                    f"{rejected['n']} / {_pct(rejected['accuracy'])}",
                ]
            )
            + " |"
        )
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    window = report["window"]
    populations = report["populations"]
    experiments = report["experiments"]
    best_case = report["best_case"]
    lines = [
        "# Weekly Tennis Manager Accuracy Audit",
        "",
        f"- Window: {window['start']} to {window['end']} ({window['timezone']})",
        f"- Minimum probability: {window['minimum_probability']}",
        f"- Dataset: `{report['dataset']['sha256']}` ({report['dataset']['rows']} rows)",
        f"- Extract cutoff: {report['dataset']['manifest'].get('extract_cutoff')}",
        "",
        "## Population reconciliation",
        "",
        *_manager_table(populations),
        "",
        "## Probability bands (Manager >=60%, full history)",
        "",
        *_band_table(report["subgroups"]),
        "",
        "## Predeclared selective filters",
        "",
        *_filter_table(experiments),
        "",
        "## Settlement bounds for the reporting window",
        "",
        (
            f"- Official Manager picks: {populations['manager_primary']['wins']} wins / "
            f"{populations['manager_primary']['scored']} scored "
            f"({_pct(populations['manager_primary']['accuracy'])}); "
            f"best case with all {populations['manager_primary']['pending']} pending wins: "
            f"{_pct(best_case['manager_primary'])}."
        ),
        (
            f"- Aligned headline: {populations['aligned']['wins']} wins / "
            f"{populations['aligned']['scored']} scored "
            f"({_pct(populations['aligned']['accuracy'])}); "
            f"best case with all {populations['aligned']['pending']} pending wins: "
            f"{_pct(best_case['aligned'])}."
        ),
        "",
        "Accuracy uses Wins / (Wins + Losses). Pending, void, and conflicted rows are",
        "reported separately and never enter the denominator.",
        "",
    ]
    return "\n".join(lines)


def build_report(
    dataset_path: str | Path,
    *,
    start: date,
    end: date,
    timezone_name: str,
    minimum_probability: float,
    holdout_start: date,
) -> dict[str, Any]:
    dataset = load_dataset(dataset_path)
    report = reconcile_week(
        dataset,
        start=start,
        end=end,
        timezone_name=timezone_name,
        minimum_probability=minimum_probability,
        holdout_start=holdout_start,
    )
    report["dataset"]["manifest_summary"] = dataset_manifest_summary(dataset)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Frozen JSONL dataset path")
    parser.add_argument("--start", type=_parse_date, default=date(2026, 10, 19))
    parser.add_argument("--end", type=_parse_date, default=date(2026, 10, 21))
    parser.add_argument("--timezone", default="Asia/Manila")
    parser.add_argument(
        "--minimum-probability", type=float, default=DEFAULT_MINIMUM_PROBABILITY
    )
    parser.add_argument(
        "--holdout-start", type=_parse_date, default=DEFAULT_HOLDOUT_START
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    report = build_report(
        args.dataset,
        start=args.start,
        end=args.end,
        timezone_name=args.timezone,
        minimum_probability=args.minimum_probability,
        holdout_start=args.holdout_start,
    )
    markdown = render_markdown(report)
    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "week_reconciliation.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (args.output_dir / "tables.md").write_text(markdown.rstrip() + "\n", encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
