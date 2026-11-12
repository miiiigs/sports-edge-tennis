"""One-source, three-window Results acceptance report for Brian.

Fixed Nov5-9, latest five completed Manila dates, and cumulative since
2026-10-27 are read in ONE PostgreSQL REPEATABLE READ READ ONLY
transaction. Never alter historical picks or settle a match here.

The complete private JSON can be compared row-by-row with the protected
dashboard API and November 9 selection-detail export.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sports_edge_tennis.dashboard.model_pick_history import (
    load_daily_model_pick_summaries,
)
from sports_edge_tennis.dashboard.qualified_history import (
    ComparisonDay, START_DAY, load_qualified_history,
)

from sports_edge_tennis.dashboard.three_window_evidence import report_windows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of-date", type=date.fromisoformat, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    if args.out.exists():
        p.error("will not overwrite a previously signed-off report")
    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        p.error("authorized read-only Railway PostgreSQL access required")
    import psycopg
    with psycopg.connect(dsn) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        refreshed_at = conn.execute(
            "SELECT transaction_timestamp()"
        ).fetchone()[0]
        shadow = load_qualified_history(conn, as_of_date=args.as_of_date)
        manager = load_daily_model_pick_summaries(
            conn, timezone_name="Asia/Manila"
        )
    result = report_windows(
        list(shadow), list(manager),
        as_of_date=args.as_of_date, source_refresh_at=refreshed_at,
    )
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"THREE_WINDOW_REPORT={args.out} SOURCE_AS_OF={refreshed_at.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
