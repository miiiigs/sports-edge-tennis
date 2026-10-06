"""Read-only daily calibration-health report for aligned Model Picks.

Prints rolling calibration windows, confidence buckets, high-confidence health,
daily-top-pick history, Wide-Spread divergence and unknown-surface cohorts, CLV,
A2/A3 abstention context, sample sufficiency, and read-only filter
counterfactuals.

It never changes a prediction, decision, threshold, selection, Value Bet
eligibility, or stake. Calibration review is human-approved.

Usage:
    TENNIS_DATABASE_URL=... python ops/daily_calibration_report.py
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import date

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from sports_edge_tennis.performance.calibration_monitor import (  # noqa: E402
    build_calibration_health,
    fetch_observations,
    hypothetical_filter,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
DAILY_DIR = REPO / "artifacts" / "calibration" / "daily"


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _pct(value: float | None) -> str:
    return "NA" if value is None else f"{value * 100:.1f}%"


def _summary_line(name: str, summary: dict) -> str:
    return (
        f"{name}: picks={summary['picks']} scored={summary['scored']} "
        f"W={summary['wins']} L={summary['losses']} pending={summary['pending']} "
        f"void={summary['voids']} observed={_pct(summary['observed_hit_rate'])} "
        f"avg_displayed={_pct(summary['average_displayed'])} "
        f"gap={_pct(summary['calibration_gap'])}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only daily calibration report")
    parser.add_argument("--dsn", default=_dsn())
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args(argv)

    if not args.dsn:
        print("CALIBRATION_REPORT_BLOCKED reason=NO_DATABASE")
        return 2

    reference = date.fromisoformat(args.as_of) if args.as_of else date.today()

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        conn.read_only = True
        observations = fetch_observations(conn)
        health = build_calibration_health(observations, reference_date=reference)

    print(f"CALIBRATION_REPORT as_of={reference.isoformat()} observations={len(observations)}")
    for window in ("TODAY", "LAST_3_DAYS", "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS", "ALL_CLEAN_PRODUCTION"):
        w = health["windows"][window]
        print(f"-- {window} --")
        print("  " + _summary_line("combined", w["combined"]))
        print("  " + _summary_line("manager", w["manager"]))
        print("  " + _summary_line("wide", w["wide"]))

    allw = health["windows"]["ALL_CLEAN_PRODUCTION"]
    print("-- HIGH CONFIDENCE (ALL) --")
    for label, row in allw["high_confidence"].items():
        print(
            f"  {label}: scored={row['scored']} observed={_pct(row['observed_hit_rate'])} "
            f"avg_displayed={_pct(row['average_displayed'])} gap={_pct(row['calibration_gap'])} "
            f"health={row['health']}"
        )

    print("-- CONFIDENCE BUCKETS (ALL) --")
    for label, row in allw["confidence_buckets"].items():
        if not row.get("scored"):
            print(f"  {label}: n={row['n']} scored=0 sparse={row['sparse']}")
            continue
        print(
            f"  {label}: scored={row['scored']} observed={_pct(row['observed_hit_rate'])} "
            f"avg_displayed={_pct(row['average_displayed'])} gap={_pct(row['calibration_gap'])} "
            f"brier={row['brier']:.4f} ece={row['ece']:.4f} sparse={row['sparse']}"
        )

    print("-- WIDE DIVERGENCE (ALL) --")
    for label, row in allw["wide_divergence"].items():
        print(
            f"  {label}: scored={row['scored']} observed={_pct(row['observed_hit_rate'])} "
            f"avg_displayed={_pct(row['average_displayed'])} gap={_pct(row['calibration_gap'])}"
        )
    print(
        "  unknown_surface_wide: " + _summary_line("unknown", allw["unknown_surface_wide"])
    )

    daily = health["daily_top_pick"]
    print(
        f"-- DAILY TOP PICK -- decided={daily['decided']} W={daily['wins']} "
        f"L={daily['losses']} hit={_pct(daily['hit_rate'])} "
        f"current_streak={daily['current_loss_streak']} longest={daily['longest_loss_streak']}"
    )
    for row in daily["last_7"]:
        print(
            f"  {row['date']} {row['player']} {_pct(row['probability'])} "
            f"{row['type']} {row['result']}"
        )

    print("-- CLV (ALL) --")
    print(
        f"  value_bets={allw['clv']['value_bets']} clv_n={allw['clv']['clv_n']} "
        f"pos={allw['clv']['clv_positive']} neg={allw['clv']['clv_negative']} "
        f"mean={_pct(allw['clv']['clv_mean'])}"
    )

    print("-- SAMPLE SUFFICIENCY --")
    print(json.dumps(health["sample_sufficiency"], indent=2))
    print(f"-- FLAGS -- {','.join(health['flags']) or 'NONE'}")

    print("-- HYPOTHETICAL FILTERS (read-only, ALL clean) --")
    filters = {
        "exclude_unknown_surface_wide": hypothetical_filter(
            observations, exclude_unknown_surface_wide=True
        ),
        "exclude_high_divergence_wide": hypothetical_filter(
            observations, exclude_high_divergence_wide=True
        ),
        "exclude_both": hypothetical_filter(
            observations,
            exclude_unknown_surface_wide=True,
            exclude_high_divergence_wide=True,
        ),
    }
    for name, result in filters.items():
        print(
            f"  {name}: retained={result['retained_picks']} removed={result['removed_picks']} "
            f"retained_hit={_pct(result['retained_hit_rate'])} "
            f"removed_hit={_pct(result['removed_hit_rate'])} "
            f"coverage_reduction={_pct(result['coverage_reduction'])}"
        )

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "generated_at": reference.isoformat(),
        "dataset_observations": len(observations),
        "health": health,
        "hypothetical_filters": filters,
    }
    out = DAILY_DIR / f"{reference.isoformat()}.json"
    out.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    print(f"CALIBRATION_SNAPSHOT path={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
