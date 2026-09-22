from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar_fixtures import SportradarFixtureError, parse_summary
from sports_edge_tennis.live.sportradar_history_refresh import (
    _parse_final_stats,
    _parse_terminal_result,
    _sportradar_player_map,
    _validate_sportradar_player_map,
)
from sports_edge_tennis.live.target_fixtures import fetch_sportradar_fixture_rows


TARGET_DATE = date(2026, 9, 21)
TERMINAL = {"COMPLETED", "RETIRED", "WALKOVER", "CANCELLED"}


def _canonical_match(conn: psycopg.Connection, event_id: str) -> tuple[int, int, int] | None:
    row = conn.execute(
        """
        SELECT msi.match_id, mv.player_1_id, mv.player_2_id
        FROM match_source_ids msi
        JOIN LATERAL (
            SELECT player_1_id, player_2_id
            FROM match_versions
            WHERE match_id = msi.match_id
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        WHERE msi.source_system = 'SPORTRADAR'
          AND msi.source_match_id = %s
        ORDER BY msi.match_source_id
        LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row[0]), int(row[1]), int(row[2])


def main() -> int:
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not api_key:
        raise RuntimeError("SPORTRADAR_TENNIS_API_KEY unavailable")

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")

    # Exactly one provider request: one daily-summary request for TARGET_DATE.
    received_at, summaries = fetch_sportradar_fixture_rows(api_key, TARGET_DATE)

    counts = Counter()
    reasons = Counter()
    linked_examples: list[dict[str, object]] = []

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("transaction_read_only is not on")

        for summary in summaries:
            counts["summaries_seen"] += 1
            try:
                fixture = parse_summary(summary, received_at=received_at)
            except SportradarFixtureError:
                counts["fixture_parse_rejected"] += 1
                continue

            if fixture.match_status not in TERMINAL:
                counts["nonterminal"] += 1
                continue

            counts["terminal"] += 1
            canonical = _canonical_match(conn, fixture.source_match_id)
            if canonical is None:
                counts["terminal_unlinked"] += 1
                continue

            match_id, player_1_id, player_2_id = canonical
            counts["terminal_linked"] += 1

            try:
                _parse_terminal_result(summary, fixture=fixture)
                counts["terminal_result_parse_ok"] += 1
            except ValueError as exc:
                counts["terminal_result_parse_rejected"] += 1
                reasons[str(exc)] += 1
                continue

            if fixture.match_status != "COMPLETED":
                counts[f"linked_{fixture.match_status.lower()}"] += 1
                continue

            counts["linked_completed"] += 1
            try:
                stats = _parse_final_stats(summary, fixture=fixture)
                counts["final_stats_parse_ok"] += 1
            except ValueError as exc:
                counts["final_stats_parse_rejected"] += 1
                reasons[str(exc)] += 1
                continue

            try:
                mapped = _validate_sportradar_player_map(
                    _sportradar_player_map(
                        conn,
                        provider_ids=tuple(item.provider_player_id for item in stats),
                    ),
                    expected_player_ids=(player_1_id, player_2_id),
                )
            except ValueError as exc:
                counts["player_bridge_rejected"] += 1
                reasons[str(exc)] += 1
                continue

            counts["player_bridge_ok"] += 1
            counts["would_be_stat_rows"] += 2
            if len(linked_examples) < 8:
                linked_examples.append(
                    {
                        "match_id": match_id,
                        "event_id": fixture.source_match_id,
                        "status": fixture.match_status,
                        "mapped_players": sorted(mapped.values()),
                        "stat_rows": 2,
                    }
                )

        conn.rollback()

    result = {
        "status": "OK",
        "target_date": TARGET_DATE.isoformat(),
        "provider_requests": 1,
        "transaction_read_only": "on",
        "writes": 0,
        "counts": dict(sorted(counts.items())),
        "rejection_reasons": dict(sorted(reasons.items())),
        "linked_examples": linked_examples,
    }
    print("SPORTRADAR_FINAL_STATS_SHADOW " + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
