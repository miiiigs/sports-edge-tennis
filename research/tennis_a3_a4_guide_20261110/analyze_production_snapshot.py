"""Reproducible aggregate-only original vs >=70% Tennis Model Picks analysis.

Frozen Railway runner evidence is NOT an A4 backtest. Never silently discard
pending picks or change the original match/selected-side population.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from math import ceil, isclose
from pathlib import Path
from typing import Any

COUNTS = ("picks", "won", "lost", "void", "pending", "graded")
THRESHOLD = .73
MIN_GRADED = 5
MIN_COVERAGE = .45


def _check_row(row: dict[str, Any], label: str) -> None:
    for key in COUNTS:
        if type(row.get(key)) is not int or row[key] < 0:
            raise ValueError(f"{label}: invalid {key}")
    if row["won"] + row["lost"] != row["graded"]:
        raise ValueError(f"{label}: graded != won + lost")
    if row["graded"] + row["void"] + row["pending"] != row["picks"]:
        raise ValueError(f"{label}: total != graded + void + pending")
    if row["graded"] > 0 and "reported_win_rate_pct" in row:
        if not isclose(row["won"] * 100 / row["graded"],
                       float(row["reported_win_rate_pct"]), abs_tol=.11):
            raise ValueError(f"{label}: report rate conflicts with counts")


def _grade(won: int, lost: int) -> float | None:
    return won / (won + lost) if won + lost else None


def _candidate(day: str, row: dict[str, Any], original_picks: int) -> dict[str, Any]:
    n, w, lost, pending = (row[k] for k in
                           ("graded", "won", "lost", "pending"))
    coverage = row["picks"] / original_picks if original_picks else None
    observed = _grade(w, lost)
    worst = w / (n + pending) if n + pending else None
    best = (w + pending) / (n + pending) if n + pending else None
    if pending:
        status = (
            "UNSETTLED_MAX_BELOW_73" if best is not None and best < THRESHOLD
            else "UNSETTLED_OUTCOME_DEPENDENT"
        )
    elif n < MIN_GRADED:
        status = "INSUFFICIENT_GRADED"
    elif coverage is None or coverage < MIN_COVERAGE:
        status = "INSUFFICIENT_COVERAGE"
    else:
        status = "MET_73" if observed is not None and observed >= THRESHOLD else "BELOW_73"
    wins_needed_if_all_pending_grade = (
        max(0, ceil(THRESHOLD * (n + pending) - 1e-12) - w)
        if pending else 0
    )
    return {
        "date": day,
        "picks": row["picks"],
        "won": w, "lost": lost, "void": row["void"], "pending": pending,
        "graded": n, "observed_win_rate": observed, "coverage": coverage,
        "worst_if_all_pending_lose": worst,
        "best_if_all_pending_win": best,
        "wins_needed_if_all_pending_grade": wins_needed_if_all_pending_grade,
        "research_day_status": status,
    }


def analyze_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("kind") != "FROZEN_PRODUCTION_AGGREGATE_RESEARCH_EVIDENCE":
        raise ValueError("wrong frozen snapshot type")
    dates = payload.get("daily")
    if not isinstance(dates, dict) or not dates:
        raise ValueError("missing daily evidence")
    start = date.fromisoformat(payload["period"]["start"])
    end = date.fromisoformat(payload["period"]["end"])
    if start > end:
        raise ValueError("reversed snapshot")
    expected_dates = {
        (start + timedelta(days=i)).isoformat()
        for i in range((end - start).days + 1)
    }
    if set(dates) != expected_dates:
        raise ValueError("snapshot must include every calendar day without gaps")

    totals = {
        kind: {key: 0 for key in COUNTS}
        for kind in ("original", "confidence70")
    }
    days = []
    avoided_losses = missed_winners = 0
    for day in sorted(dates):
        pair = dates[day]
        old, new = pair["original"], pair["confidence70"]
        _check_row(old, f"{day}.original")
        _check_row(new, f"{day}.confidence70")
        for key in ("picks", "won", "lost", "void", "pending"):
            if new[key] > old[key]:
                raise ValueError(f"{day}: filtered population is not a subset for {key}")
        avoided = old["lost"] - new["lost"]
        missed = old["won"] - new["won"]
        if avoided != new.get("avoided_losses") or missed != new.get("missed_winners"):
            raise ValueError(f"{day}: discarded winner/loss accounting mismatch")
        avoided_losses += avoided
        missed_winners += missed
        for kind, src in (("original", old), ("confidence70", new)):
            for key in COUNTS:
                totals[kind][key] += src[key]
        days.append({
            "date": day,
            "original": _candidate(day, old, old["picks"]),
            "confidence70": _candidate(day, new, old["picks"]),
            "avoided_losses": avoided,
            "missed_winners": missed,
        })

    orig, qual = totals["original"], totals["confidence70"]
    original_rate = _grade(orig["won"], orig["lost"])
    qualified_rate = _grade(qual["won"], qual["lost"])
    coverage = qual["picks"] / orig["picks"] if orig["picks"] else None
    day_met = {
        kind: sum(row[kind]["research_day_status"] == "MET_73" for row in days)
        for kind in totals
    }
    return {
        "status": "OBSERVATIONAL_BASELINE_ONLY_NOT_A4_V3_VALIDATION",
        "source_as_of": payload["as_of"],
        "scope": payload["scope"],
        "timezone": payload["timezone"],
        "period": payload["period"],
        "totals": totals,
        "original_win_rate": original_rate,
        "confidence70_win_rate": qualified_rate,
        "graded_accuracy_gap_percentage_points": (
            100 * (qualified_rate - original_rate)
            if original_rate is not None and qualified_rate is not None else None
        ),
        "confidence70_selection_coverage": coverage,
        "avoided_losses": avoided_losses,
        "missed_winners": missed_winners,
        "fully_settled_days_met_73": day_met,
        "daily": days,
        "pending_triage_latest": payload.get("pending_triage_latest"),
        "guide_odds_latest": payload.get("odds_latest"),
        "warnings": [
            "Observed conditional win rates are not out-of-sample A4 performance.",
            "Results on unsettled dates may change as matches finish.",
            "Best/worst bounds assume all currently pending picks become graded; actual voids may alter denominators.",
            "Research >=70% excludes 30 currently known original winners and 27 currently known original losses.",
            "Guide Odds are market context, not a vote or an estimated tennis win probability.",
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshot", type=Path)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    if args.out is not None and args.out.exists():
        p.error("refusing to overwrite a report")
    result = analyze_snapshot(json.loads(args.snapshot.read_text("utf-8")))
    body = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(body, "utf-8")
        print(f"REPORT={args.out}")
    else:
        print(body)


if __name__ == "__main__":
    main()
