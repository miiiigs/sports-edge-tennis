from __future__ import annotations

import json
import os
from datetime import date

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar_fixtures import SportradarFixtureError, parse_summary
from sports_edge_tennis.live.sportradar_history_refresh import _parse_terminal_result
from sports_edge_tennis.live.target_fixtures import fetch_sportradar_fixture_rows


TARGET_DATE = date(2026, 9, 21)
TARGET_EVENTS = {
    "sr:sport_event:74861362": 130673,
    "sr:sport_event:74880058": 130705,
    "sr:sport_event:74888652": 130724,
    "sr:sport_event:74891186": 130736,
    "sr:sport_event:74933664": 130746,
}


def latest_canonical_result(conn: psycopg.Connection, match_id: int) -> dict[str, object] | None:
    row = conn.execute(
        """
        SELECT outcome_status::text, winner_side, retired_side
        FROM match_result_observations
        WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC,
                 match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "outcome_status": str(row[0]),
        "winner_side": row[1],
        "retired_side": row[2],
    }


def main() -> int:
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not api_key:
        raise RuntimeError("SPORTRADAR_TENNIS_API_KEY unavailable")
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")

    received_at, summaries = fetch_sportradar_fixture_rows(api_key, TARGET_DATE)
    by_id = {}
    for summary in summaries:
        event = summary.get("sport_event")
        if isinstance(event, dict):
            event_id = str(event.get("id") or "").strip()
            if event_id in TARGET_EVENTS:
                by_id[event_id] = summary

    rows = []
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("transaction_read_only is not on")

        for event_id, match_id in TARGET_EVENTS.items():
            canonical = latest_canonical_result(conn, match_id)
            summary = by_id.get(event_id)
            if summary is None:
                rows.append({
                    "event_id": event_id,
                    "match_id": match_id,
                    "provider_present": False,
                    "canonical": canonical,
                })
                continue

            status = summary.get("sport_event_status")
            status = status if isinstance(status, dict) else {}
            raw = {
                "status": str(status.get("status") or ""),
                "winning_reason": str(status.get("winning_reason") or ""),
                "winner_id": str(status.get("winner_id") or ""),
                "home_score": status.get("home_score"),
                "away_score": status.get("away_score"),
            }
            try:
                fixture = parse_summary(summary, received_at=received_at)
                parsed = {
                    "fixture_status": fixture.match_status,
                }
                try:
                    terminal = _parse_terminal_result(summary, fixture=fixture)
                    parsed.update({
                        "outcome_status": terminal.outcome_status,
                        "winner_side": terminal.winner_side,
                        "retired_side": terminal.retired_side,
                    })
                except ValueError as exc:
                    parsed["terminal_error"] = str(exc)
            except SportradarFixtureError as exc:
                parsed = {"fixture_error": str(exc)}

            rows.append({
                "event_id": event_id,
                "match_id": match_id,
                "provider_present": True,
                "raw": raw,
                "parsed": parsed,
                "canonical": canonical,
            })

        conn.rollback()

    result = {
        "status": "OK",
        "target_date": TARGET_DATE.isoformat(),
        "provider_requests": 1,
        "transaction_read_only": "on",
        "writes": 0,
        "target_events": len(TARGET_EVENTS),
        "provider_events_found": len(by_id),
        "rows": rows,
    }
    print("SPORTRADAR_RETIREMENT_LIVE_SAMPLE " + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
