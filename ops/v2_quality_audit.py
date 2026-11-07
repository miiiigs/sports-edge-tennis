"""Run a read-only V2 quality research audit on an authorized JSON export.

Example:
  python ops/v2_quality_audit.py --ledger /secure/aligned.json \
    --start 2026-10-27 --end 2026-11-06 --as-of 2026-11-07

The export is produced by ops/calibration_dataset.sql (including both result
observation timestamps). This script never connects to production, changes
predictions, or commits any match-level data.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sports_edge_tennis.performance.v2_quality_research import (  # noqa: E402
    MANILA, audit, parse_export,
)
from sports_edge_tennis.performance.v2_walkforward import (  # noqa: E402
    walk_forward, reconcile_original_days,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("fixed", "walk-forward"), default="fixed",
        help="fixed: freeze one training cutoff; walk-forward: refit prior-only each day",
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--evaluated-at", type=datetime.fromisoformat, default=None,
        help="Offset-aware evaluation instant, e.g. 2026-11-07T09:00:00+08:00",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--expected-original", type=Path, default=None,
        help="Independent JSON daily original scorecard to hard-fail cohort drift",
    )
    args = parser.parse_args(argv)

    if not args.ledger.is_file():
        parser.error("ledger must be an existing authorized export file")
    if args.start > args.end:
        parser.error("start date must be before or equal to end")
    if args.out is not None and args.out.exists():
        parser.error("refusing to overwrite an existing report")
    if args.expected_original is not None and args.mode != "walk-forward":
        parser.error("--expected-original is supported only for walk-forward")

    try:
        raw = json.loads(args.ledger.read_text(encoding="utf-8-sig"))
        rows = parse_export(raw)
        if args.mode == "walk-forward":
            if args.evaluated_at is not None and args.as_of is not None:
                parser.error("pass either --evaluated-at or --as-of, not both")
            if args.evaluated_at is not None:
                evaluated_at = args.evaluated_at
            elif args.as_of is not None:
                # Date-only cutoff means through the END of that Manila day.
                evaluated_at = (
                    datetime.combine(args.as_of + timedelta(days=1), time.min, tzinfo=MANILA)
                    - timedelta(microseconds=1)
                )
            else:
                evaluated_at = datetime.now(UTC)
            report = walk_forward(
                rows, start=args.start, end=args.end, evaluated_at=evaluated_at,
            )
            if args.expected_original is not None:
                expected_payload = json.loads(
                    args.expected_original.read_text(encoding="utf-8-sig")
                )
                if not isinstance(expected_payload, dict):
                    raise ValueError("expected-original must be a JSON daily mapping")
                expected = expected_payload.get("days")
                if not isinstance(expected, dict):
                    raise ValueError("expected-original requires a days mapping")
                report["original_reconciliation"] = reconcile_original_days(
                    report, expected
                )
        else:
            if args.evaluated_at is not None:
                parser.error("--evaluated-at is only valid in walk-forward mode")
            report = audit(rows, start=args.start, end=args.end, as_of=args.as_of)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"V2_QUALITY_AUDIT_BLOCKED reason={type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    serialized = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        # Report contains only cohort aggregates, never player names or IDs.
        args.out.write_text(serialized + "\n", encoding="utf-8")
        print(f"V2_QUALITY_AUDIT_REPORT={args.out}")
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
