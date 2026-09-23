from __future__ import annotations

"""Read-only OddsPapi live odds readiness probe.

Loads the current Sportradar production targets, fetches OddsPapi odds for the
smallest covering window, classifies target<->event matching, and verifies
match-winner price normalization for sharp and offered books. Writes nothing.
"""

import argparse
import json
import os
import time
from collections import Counter
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
from sports_edge_tennis.live.sportradar_benchmark import deterministic_name_keys
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    extract_oddspapi_match_winner_prices,
)

RUN_IDS = (481, 482, 483)
MAX_START_DELTA_MINUTES = 120


def _load_targets(conn) -> list[dict]:
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, mv.player_1_id, mv.player_2_id, mv.match_start_at,
                mv.tour_scope::text AS tour_scope, mv.competition_tier::text AS competition_tier,
                mv.match_status::text AS match_status
            FROM match_versions mv
            JOIN prediction_run_targets prt ON prt.match_id = mv.match_id
            WHERE prt.prediction_run_id = ANY(%s)
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        )
        SELECT latest.match_id, latest.match_start_at, latest.tour_scope, latest.competition_tier,
               latest.match_status, p1.canonical_name, p2.canonical_name, t.canonical_name,
               src.source_match_id
        FROM latest
        JOIN players p1 ON p1.player_id = latest.player_1_id
        JOIN players p2 ON p2.player_id = latest.player_2_id
        JOIN matches m ON m.match_id = latest.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        LEFT JOIN LATERAL (
            SELECT source_match_id FROM match_source_ids
            WHERE match_id = latest.match_id AND source_system = 'SPORTRADAR'
            ORDER BY match_source_id DESC LIMIT 1
        ) src ON TRUE
        ORDER BY latest.match_start_at, latest.match_id
        """,
        (list(RUN_IDS),),
    ).fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "match_id": int(row[0]),
                "start": row[1],
                "tour_scope": row[2],
                "competition_tier": row[3],
                "match_status": row[4],
                "p1_name": str(row[5]),
                "p2_name": str(row[6]),
                "tournament": str(row[7]),
                "sportradar_event_id": row[8],
            }
        )
    return out


def _betradar_key(source_match_id: str | None) -> str | None:
    if not source_match_id:
        return None
    text = str(source_match_id)
    return text[len("sr:sport_event:") :] if text.startswith("sr:sport_event:") else text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-budget", type=int, default=2)
    args = parser.parse_args()

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    api_key = load_secret("ODDSPAPI_API_KEY")
    if not dsn or not api_key:
        print("ODDSPAPI_PROBE_BLOCKED missing_dsn_or_key")
        return 2

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        if read_only != "on":
            raise RuntimeError("transaction_read_only is not on")
        targets = _load_targets(conn)
        conn.rollback()

    requests_used = 0
    if not targets:
        print("ODDSPAPI_PROBE no_targets")
        return 0

    start = min(t["start"] for t in targets)
    end = max(t["start"] for t in targets)
    window_start = datetime.combine(start.date(), datetime.min.time(), tzinfo=UTC)
    window_end = datetime.combine(end.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    fixture_received_at, fixtures = fetch_oddspapi_tennis_fixtures(
        api_key,
        from_at=window_start,
        to_at=window_end,
        bookmakers=ODDSPAPI_PLAN_BOOKMAKERS,
    )
    requests_used += 1

    tournament_ids: list[int] = []
    for payload in fixtures:
        try:
            envelope = parse_oddspapi_tennis_fixture(payload)
        except ValueError:
            continue
        tournament_ids.append(envelope.tournament_id)

    unique_tournaments = tuple(dict.fromkeys(tournament_ids))
    board_rows: list[dict] = []
    odds_received_at = fixture_received_at
    merged: list[dict] = []
    if unique_tournaments:
        time.sleep(1.1)
        odds_received_at, board_rows, used = fetch_oddspapi_tennis_odds_for_books(
            api_key,
            tournament_ids=unique_tournaments,
            bookmakers=ODDSPAPI_PLAN_BOOKMAKERS,
        )
        requests_used += used
        merged = merge_oddspapi_odds_books(fixtures, board_rows)

    by_betradar: dict[str, dict] = {}
    envelopes = []
    malformed = 0
    for payload in merged:
        try:
            envelope = parse_oddspapi_tennis_fixture(payload)
        except ValueError:
            malformed += 1
            continue
        envelopes.append((envelope, payload))
        if envelope.betradar_id:
            by_betradar.setdefault(_betradar_key(envelope.betradar_id) or envelope.betradar_id, payload)

    classifications: Counter[str] = Counter()
    sharp_available = offered_available = 0
    offered_books_seen: set[str] = set()
    stale_markets = suspended_markets = malformed_prices = 0
    normalized_markets = 0
    examples: dict[str, list] = {}

    for target in targets:
        event_key = _betradar_key(target["sportradar_event_id"])
        payload = by_betradar.get(event_key) if event_key else None
        orientation = "DIRECT"
        status = None
        if payload is not None:
            status = "EXACT_MATCH"
        else:
            # name/time fallback
            provider_p1 = set(target["p1_name"].split())
            t_keys = deterministic_name_keys(target["p1_name"])
            t2_keys = deterministic_name_keys(target["p2_name"])
            candidates = []
            time_near = []
            for envelope, candidate_payload in envelopes:
                delta = abs((envelope.match_start_at - target["start"]).total_seconds()) // 60
                if delta <= MAX_START_DELTA_MINUTES:
                    time_near.append(candidate_payload)
                p1_keys = deterministic_name_keys(envelope.participant_1_name)
                p2_keys = deterministic_name_keys(envelope.participant_2_name)
                direct = bool(p1_keys & t_keys) and bool(p2_keys & t2_keys)
                reverse = bool(p1_keys & t2_keys) and bool(p2_keys & t_keys)
                if direct != reverse:
                    candidates.append((candidate_payload, "DIRECT" if direct else "REVERSED"))
            if len(candidates) > 1:
                status = "AMBIGUOUS_EVENT"
            elif len(candidates) == 1:
                payload, orientation = candidates[0]
                status = "SAFE_ORIENTATION_REMAP" if orientation == "REVERSED" else "TIME_OR_NAME_MATCH"
            elif time_near:
                status = "PLAYER_MISMATCH"
            else:
                status = "NO_ODDS_EVENT"
        classifications[status] += 1

        if payload is None:
            continue
        book_prices = {}
        for book in ODDSPAPI_PLAN_BOOKMAKERS:
            prices = extract_oddspapi_match_winner_prices(payload, bookmaker=book)
            if prices is None:
                continue
            offered_books_seen.add(book)
            if orientation == "REVERSED":
                prices = (prices[1], prices[0])
            book_prices[book] = [str(prices[0]), str(prices[1])]
            normalized_markets += 1
            if book == ODDSPAPI_SHARP_BOOKMAKER:
                sharp_available += 1
            else:
                offered_available += 1
        if book_prices and len(examples.get("matched", [])) < 6:
            examples.setdefault("matched", []).append(
                {"match_id": target["match_id"], "event": event_key, "orientation": orientation, "prices": book_prices}
            )

    print(
        "ODDSPAPI_LIVE_PROBE "
        + json.dumps(
            {
                "requests_used": requests_used,
                "session_budget": args.session_budget,
                "transaction_read_only": read_only,
                "writes": 0,
                "targets_checked": len(targets),
                "odds_fixture_rows": len(fixtures),
                "odds_board_rows": len(board_rows),
                "merged_rows": len(merged),
                "malformed_rows": malformed,
                "bookmakers_seen": sorted(offered_books_seen),
                "classifications": dict(sorted(classifications.items())),
                "sharp_available": sharp_available,
                "offered_available": offered_available,
                "normalized_markets": normalized_markets,
                "stale_markets": stale_markets,
                "suspended_markets": suspended_markets,
                "malformed_prices": malformed_prices,
                "examples": examples,
            },
            sort_keys=True,
        )[:12000]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
