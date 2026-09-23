from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.oddspapi import (
    ODDSPAPI_PLAN_BOOKMAKERS,
    ODDSPAPI_SHARP_BOOKMAKER,
    fetch_oddspapi_tennis_fixtures,
    fetch_oddspapi_tennis_odds_for_books,
    merge_oddspapi_odds_books,
    parse_oddspapi_tennis_fixture,
)
from sports_edge_tennis.live.oddspapi_reconciliation import reconcile_oddspapi_event
from sports_edge_tennis.live.price_slate import load_price_scanner_candidate_match_ids
from sports_edge_tennis.odds.providers.oddspapi_tennis import extract_oddspapi_match_winner_prices


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _today_utc() -> date:
    return datetime.now(UTC).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only OddsPapi shadow comparison")
    parser.add_argument("--start-date", type=date.fromisoformat, default=_today_utc())
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--dsn", default=_db_dsn())
    args = parser.parse_args(argv)
    if args.days < 1 or args.days > 7:
        print("ODDSPAPI_SHADOW_BLOCKED invalid_days", flush=True)
        return 2
    if not args.dsn:
        print("ODDSPAPI_SHADOW_BLOCKED missing_database", flush=True)
        return 2
    api_key = load_secret("ODDSPAPI_API_KEY")
    if not api_key:
        print("ODDSPAPI_SHADOW_BLOCKED missing_api_key", flush=True)
        return 2

    window_start = datetime.combine(args.start_date, datetime.min.time(), tzinfo=UTC)
    window_end = window_start + timedelta(days=args.days)
    requests_used = 0
    fixture_received_at, fixtures = fetch_oddspapi_tennis_fixtures(
        api_key,
        from_at=window_start,
        to_at=window_end,
        bookmakers=(ODDSPAPI_SHARP_BOOKMAKER,),
    )
    requests_used += 1

    fixture_ids: set[str] = set()
    tournament_ids: list[int] = []
    valid_fixture_count = 0
    for payload in fixtures:
        try:
            envelope = parse_oddspapi_tennis_fixture(payload)
        except ValueError:
            continue
        fixture_ids.add(envelope.fixture_id)
        tournament_ids.append(envelope.tournament_id)
        valid_fixture_count += 1

    odds_rows: list[dict] = []
    odds_received_at = fixture_received_at
    if tournament_ids:
        odds_received_at, board_rows, odds_requests = fetch_oddspapi_tennis_odds_for_books(
            api_key,
            tournament_ids=tournament_ids,
            bookmakers=ODDSPAPI_PLAN_BOOKMAKERS,
        )
        requests_used += odds_requests
        odds_rows = merge_oddspapi_odds_books(
            fixtures,
            board_rows,
        )

    summary = {
        "requests_used": requests_used,
        "fixture_rows": len(fixtures),
        "priced_fixture_rows": valid_fixture_count,
        "odds_rows": len(odds_rows),
        "candidate_matches": 0,
        "exact": 0,
        "existing": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "conflict": 0,
        "pinnacle_complete": 0,
        "pinnacle_comparable": 0,
        "mean_abs_price_delta": None,
        "max_abs_price_delta": None,
    }
    deltas: list[Decimal] = []

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        candidate_ids = load_price_scanner_candidate_match_ids(
            conn,
            horizon_start=args.start_date,
            horizon_days=args.days,
            as_of=odds_received_at,
        )
        summary["candidate_matches"] = len(candidate_ids)
        for payload in odds_rows:
            try:
                envelope = parse_oddspapi_tennis_fixture(payload)
            except ValueError:
                continue
            resolution = reconcile_oddspapi_event(
                conn,
                envelope=envelope,
                candidate_match_ids=candidate_ids,
                as_of=odds_received_at,
                persist=False,
            )
            key = resolution.status.casefold()
            if key in summary:
                summary[key] += 1
            if resolution.status not in {"EXACT", "EXISTING"} or resolution.candidate is None:
                continue
            prices = extract_oddspapi_match_winner_prices(
                payload,
                bookmaker=ODDSPAPI_SHARP_BOOKMAKER,
            )
            if prices is None:
                continue
            summary["pinnacle_complete"] += 1
            existing = _latest_pinnacle_prices(
                conn,
                match_id=int(resolution.match_id),
                as_of=odds_received_at,
            )
            if existing is None:
                continue
            provider_p1, provider_p2 = prices
            if resolution.candidate.provider_orientation == "DIRECT":
                odds_p1, odds_p2 = provider_p1, provider_p2
            else:
                odds_p1, odds_p2 = provider_p2, provider_p1
            summary["pinnacle_comparable"] += 1
            deltas.extend(
                (abs(odds_p1 - existing[0]), abs(odds_p2 - existing[1]))
            )

    if deltas:
        summary["mean_abs_price_delta"] = str(
            sum(deltas, Decimal("0")) / Decimal(len(deltas))
        )
        summary["max_abs_price_delta"] = str(max(deltas))
    print("ODDSPAPI_SHADOW " + json.dumps(summary, sort_keys=True), flush=True)
    return 0


def _latest_pinnacle_prices(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> tuple[Decimal, Decimal] | None:
    rows = conn.execute(
        """
        SELECT DISTINCT ON (selection_type)
            selection_type::text, decimal_price
        FROM odds_snapshots
        WHERE match_id = %s
          AND source_system = 'SHARP'
          AND bookmaker_code = 'PINNACLE'
          AND market_type = 'MATCH_WINNER'
          AND market_status = 'OPEN'
          AND source_observed_at <= %s
        ORDER BY selection_type, source_observed_at DESC, odds_snapshot_id DESC
        """,
        (match_id, as_of),
    ).fetchall()
    values = {str(row[0]): Decimal(row[1]) for row in rows}
    if "PLAYER_1" not in values or "PLAYER_2" not in values:
        return None
    return values["PLAYER_1"], values["PLAYER_2"]


if __name__ == "__main__":
    sys.exit(main())
