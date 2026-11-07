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
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sports_edge_tennis.performance.v2_quality_research import (  # noqa: E402
    MANILA, audit, parse_export,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.ledger.is_file():
        parser.error("ledger must be an existing authorized export file")
    if args.start > args.end:
        parser.error("start date must be before or equal to end")
    if args.out is not None and args.out.exists():
        parser.error("refusing to overwrite an existing report")

    try:
        raw = json.loads(args.ledger.read_text(encoding="utf-8-sig"))
        rows = parse_export(raw)
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
