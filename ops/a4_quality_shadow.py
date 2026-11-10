"""Offline Sports Edge Tennis A3/A4 + Guide Odds validation.

Run with an AUTHORIZED READ-ONLY original-picks export from
ops/calibration_dataset.sql. Optional odds must be two-sided market quotes
known before ORIGINAL issuance (not final/closing prices).

Example:
python ops/a4_quality_shadow.py --ledger /secure/first_issued.json \\
  --start 2026-10-27 --end 2026-11-06 \\
  --evaluated-at 2026-11-07T09:04:42+08:00 \\
  --guide-quotes /secure/guide_quotes.json \\
  --expected-original research/tennis_v2_20261107/original_daily_scorecard_20261027_1106.json

The public aggregate JSON never contains player names, match IDs or raw
bookmaker lines. This script performs no production action or DB writes.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sports_edge_tennis.performance.a4_quality_shadow import (  # noqa: E402
    GuideQuote, MODEL_VERSION, report_day,
)
from sports_edge_tennis.performance.v2_quality_research import (  # noqa: E402
    MANILA, _datetime, parse_export,
)
from sports_edge_tennis.performance.v2_walkforward import (  # noqa: E402
    reconcile_original_days,
)


def _load_guide_quotes(path: Path | None) -> tuple[GuideQuote, ...]:
    if path is None:
        return ()
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or not isinstance(raw.get("quotes"), list):
        raise ValueError("guide-quotes must be a JSON object with quotes list")
    quotes: list[GuideQuote] = []
    for item in raw["quotes"]:
        if not isinstance(item, dict):
            raise ValueError("guide quote must be a JSON object")
        quotes.append(GuideQuote(
            match_id=int(item["match_id"]),
            selected_side=int(item["selected_side"]),
            selected_decimal=float(item["selected_decimal"]),
            opposite_decimal=float(item["opposite_decimal"]),
            source=str(item["source"]),
            source_observed_at=_datetime(
                item["source_observed_at"], required=True,
            ),
            ingested_at=_datetime(item["ingested_at"], required=True),
            market_status=str(item.get("market_status", "OPEN")),
        ))
    return tuple(quotes)


def build_report(
    rows, *, start: date, end: date, evaluated_at: datetime, quotes=(),
    expected: dict | None = None,
) -> dict:
    if start > end:
        raise ValueError("reversed dates")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluation instant needs timezone")
    today = evaluated_at.astimezone(MANILA).date()
    if end > today:
        raise ValueError("cannot evaluate future Manila dates")
    all_rows = tuple(rows)
    if len({x.match_id for x in all_rows}) != len(all_rows):
        raise ValueError("duplicate original match")

    folds = []
    day = start
    while day <= end:
        cutoff = datetime.combine(day, time.min, tzinfo=MANILA)
        current = tuple(
            p for p in all_rows if p.day == day and p.picked_at <= evaluated_at
        )
        result = report_day(
            current, history=all_rows, cutoff=cutoff,
            evaluated_at=evaluated_at, quotes=quotes,
        )
        # Retain only aggregate counters; no ID, player, quote or odds line.
        result.pop("line_items", None)
        folds.append({"date": day.isoformat(), **result})
        day += timedelta(days=1)

    reconciliation = None
    if expected is not None:
        mock_folds = [{
            "date": x["date"],
            "policies": {"original": {
                "picks": x["original_picks"],
                "won": x["original_won"],
                "lost": x["original_lost"],
                "void": x["original_void"],
                "pending": x["original_pending"],
                "graded": x["original_won"] + x["original_lost"],
            }},
        } for x in folds]
        reconciliation = reconcile_original_days(
            {"folds": mock_folds}, expected,
        )

    return {
        "mode": "A3_A4_GUIDE_RESEARCH_ONLY",
        "model_version": MODEL_VERSION,
        "production_changed": False,
        "historical_result_changed": False,
        "guide_odds_change_winner_selection": False,
        "evaluated_at": evaluated_at.isoformat(),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "original_reconciliation": reconciliation,
        "summary": {
            "days": len(folds),
            "original_picks": sum(x["original_picks"] for x in folds),
            "A4_fitted_days": sum(x["a4_fit_available"] for x in folds),
            "guide_odds_available": sum(x["guide_odds_available"] for x in folds),
            "selected_sides_changed": 0,
            "a3_risks": dict(sorted(sum(
                (Counter(x["a3_risk_counts"]) for x in folds), Counter()
            ).items())),
        },
        "daily": folds,
        "limitations": (
            "A4 is a regularized reliability research model. Historical V2 "
            "populations may already be inspected and are not untouched. "
            "Outcomes are scored only when source-observed and ingested "
            "by evaluation instant. Market availability/consensus is not "
            "evidence of higher prediction accuracy. Pending and missing odds "
            "remain missing. Prospective independent validation required."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", required=True, type=Path)
    p.add_argument("--guide-quotes", type=Path)
    p.add_argument("--expected-original", type=Path)
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--evaluated-at", type=datetime.fromisoformat,
                   default=datetime.now(UTC))
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)
    if args.out is not None and args.out.exists():
        p.error("refusing to overwrite a report")
    try:
        rows = parse_export(
            json.loads(args.ledger.read_text(encoding="utf-8-sig"))
        )
        quotes = _load_guide_quotes(args.guide_quotes)
        expected = None
        if args.expected_original:
            payload = json.loads(
                args.expected_original.read_text(encoding="utf-8-sig")
            )
            expected = payload["days"]
        report = build_report(
            rows, start=args.start, end=args.end,
            evaluated_at=args.evaluated_at, quotes=quotes, expected=expected,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"A4_SHADOW_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    result = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(result, encoding="utf-8")
        print(f"A4_SHADOW_REPORT={args.out}")
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
