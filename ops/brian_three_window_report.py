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

FIXED_START = date(2026, 11, 5)
FIXED_END = date(2026, 11, 9)


def _aggregate(rows: list[tuple[int, int, int, int, int]]) -> dict:
    picks = won = lost = void = pending = 0
    for p, w, l, v, n in rows:
        if min(p, w, l, v, n) < 0 or p != w+l+v+n:
            raise ValueError("inconsistent original or research W/L/VOID/PENDING")
        picks += p
        won += w
        lost += l
        void += v
        pending += n
    graded = won+lost
    return {
        "picks": picks, "won": won, "lost": lost,
        "void": void, "pending": pending, "graded": graded,
        "settled_accuracy": won/graded if graded else None,
    }


def report_windows(
    days: list[ComparisonDay], manager: list,
    *,
    as_of_date: date,
    source_refresh_at: datetime,
) -> dict:
    """All three reporting windows share ONE as-of and ONE classification."""
    if as_of_date < date(2026, 11, 10):
        raise ValueError("fixed November 5-9 cannot be current before November 10")
    if source_refresh_at.tzinfo is None or source_refresh_at.utcoffset() is None:
        raise ValueError("source timestamp must be timezone aware")
    by_day = {x.day: x for x in days}
    if len(by_day) != len(days):
        raise ValueError("duplicate research daily record")
    man = {x.decision_date: x for x in manager}
    if len(man) != len(manager):
        raise ValueError("duplicate Manager daily record")
    end = as_of_date - timedelta(days=1)
    periods = {
        "fixed_nov05_nov09": (FIXED_START, FIXED_END),
        "rolling_five_complete_dates": (end-timedelta(days=4), end),
        "cumulative_since_2026_10_27": (START_DAY, end),
    }
    output = {}
    for name, (start, last) in periods.items():
        wanted = [start+timedelta(days=i) for i in range((last-start).days+1)]
        if any(d not in by_day for d in wanted):
            raise ValueError("missing daily original/qualified evidence in "+name)
        original = _aggregate([
            (by_day[d].original, by_day[d].original_won, by_day[d].original_lost,
             by_day[d].original_void, by_day[d].original_pending)
            for d in wanted
        ])
        selected = _aggregate([
            (by_day[d].qualified, by_day[d].qualified_won, by_day[d].qualified_lost,
             by_day[d].qualified_void, by_day[d].qualified_pending)
            for d in wanted
        ])
        for k in ("picks", "won", "lost", "void", "pending"):
            if selected[k] > original[k]:
                raise ValueError("filtered picks not subset of Original: "+name)
        selected["coverage"] = (
            selected["picks"] / original["picks"]
            if original["picks"] else None
        )
        manager_rows = [man[d] for d in wanted if d in man]
        separate_manager = (
            _aggregate([
                (x.picks, x.wins, x.losses, x.voids, x.pending)
                for x in manager_rows
            ])
            if len(manager_rows) == len(wanted) else None
        )
        output[name] = {
            "start": start.isoformat(), "end": last.isoformat(),
            "status": (
                "PROVISIONAL" if original["pending"] or selected["pending"]
                or (separate_manager or {}).get("pending") else "SETTLED"
            ),
            "original": original, "existing_qualified_ge70": selected,
            "manager_only_separate_population": separate_manager,
            "excluded_winners": original["won"]-selected["won"],
            "excluded_losses": original["lost"]-selected["lost"],
            "days": [
                {
                    "day": d.isoformat(),
                    "original": {
                        "picks": by_day[d].original,
                        "won": by_day[d].original_won,
                        "lost": by_day[d].original_lost,
                        "void": by_day[d].original_void,
                        "pending": by_day[d].original_pending,
                    },
                    "qualified": {
                        "picks": by_day[d].qualified,
                        "won": by_day[d].qualified_won,
                        "lost": by_day[d].qualified_lost,
                        "void": by_day[d].qualified_void,
                        "pending": by_day[d].qualified_pending,
                    },
                }
                for d in wanted
            ],
        }
    return {
        "source": "RAILWAY_PRODUCTION_POSTGRES_READ_ONLY_SINGLE_TRANSACTION",
        "data_refreshed_at": source_refresh_at.isoformat(),
        "timezone": "Asia/Manila",
        "original_population": "FIRST_ISSUED_ALIGNED_MODEL_PICKS",
        "qualified_policy": "PIT_CONFIDENCE_GE70_V1_RESEARCH_ONLY",
        "manager_population": "INDEPENDENT_FIRST_PRODUCTION_MANAGER_ONLY",
        "accuracy_denominator": "WON+LOST (void/pending excluded)",
        "a3_a4_forward_evidence": "NOT_AVAILABLE",
        "periods": output,
    }


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
