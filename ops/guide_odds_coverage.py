"""Read-only Guide Odds coverage audit for an approved production/operator session.

This script makes NO data-provider requests and NO writes. It shows whether
Brian's already-issued Model Picks actually have source-verified, side-matched
market prices, without confusing a completed prediction batch with priced
coverage. Output contains counts only (no player identities or credentials).

Run from the Tennis repository with the existing DATABASE_URL/TENNIS_DATABASE_URL:

    python ops/guide_odds_coverage.py --date 2026-10-19 --timezone Asia/Manila

This audit is separate from any proposed LIVE publication-time odds snapshot
ledger, which requires design, migration and rollout approval.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import load_aligned_day_picks
from sports_edge_tennis.dashboard.guide_odds import get_issued_guide_odds


def audit_guide_odds(
    conn: psycopg.Connection, *, day: date, timezone_name: str
) -> dict:
    picks = load_aligned_day_picks(
        conn, decision_date=day, timezone_name=timezone_name
    )
    quotes = get_issued_guide_odds(
        conn, decision_date=day, timezone_name=timezone_name
    )
    ids = {p.match_id for p in picks}
    quotes = {mid: q for mid, q in quotes.items() if mid in ids}
    booked = Counter(q.bookmaker_code for q in quotes.values())
    post_issue = sum(q.issued_before_quote for q in quotes.values())
    return {
        "date": day.isoformat(),
        "timezone": timezone_name,
        "population": "first-qualified aligned production Model Picks, one per match",
        "total_model_picks": len(ids),
        "guide_odds_available": len(quotes),
        "guide_odds_missing": len(ids) - len(quotes),
        "guide_odds_coverage_pct": (
            round(100 * len(quotes) / len(ids), 2) if ids else None
        ),
        "originally_available_by_issuance": len(quotes) - post_issue,
        "first_available_after_issuance": post_issue,
        "offered_bookmakers": dict(sorted(booked.items())),
        "quote_evidence_mutable": True,
        "notes": (
            "Guide odds are observed non-betting display evidence only; "
            "historical read-model snapshots are not an immutable issued-price ledger. "
            "Missing quotes stay missing; no model-implied odds substituted."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--timezone", default="UTC")
    args = parser.parse_args(argv)

    try:
        ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        parser.error("Unknown timezone")

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL or DATABASE_URL must be set")

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        try:
            report = audit_guide_odds(
                conn, day=args.date, timezone_name=args.timezone
            )
        finally:
            conn.rollback()
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
