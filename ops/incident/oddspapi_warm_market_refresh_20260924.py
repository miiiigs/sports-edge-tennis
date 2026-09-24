from __future__ import annotations

"""Validation-only OddsPapi warm-cache market refresh.

Uses the already-cached event identity (no discovery request), runs the real
qualification/merge/normalize/persist path with discovery_rows=[] and the cached
identity as the trusted base, and proves the board rows survive and a new,
fresher snapshot is created. No Manager/consensus/prediction/publication work.
Sanitized output only.
"""

import json
import os
from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.oddspapi import (
    ODDSPAPI_PLAN_BOOKMAKERS,
    merge_oddspapi_odds_books,
)
from sports_edge_tennis.live.oddspapi_budget import OddspapiBudgetPolicy
from sports_edge_tennis.live.oddspapi_odds import ingest_oddspapi_tennis_odds
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.market.policy import is_fresh
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    build_oddspapi_tennis_observation,
)

TTL_MINUTES = 15


def emit(label: str, payload: dict) -> None:
    print(f"{label} {json.dumps(payload, default=str, sort_keys=True)}", flush=True)


def _fetch_for_books(api_key, tournament_ids, fallback_budget, stats):
    from sports_edge_tennis.live.oddspapi import fetch_oddspapi_tennis_odds_for_books

    return fetch_oddspapi_tennis_odds_for_books(
        api_key,
        tournament_ids=tournament_ids,
        bookmakers=ODDSPAPI_PLAN_BOOKMAKERS,
        fallback_budget=fallback_budget,
        fallback_stats=stats,
    )


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    api_key = load_secret("ODDSPAPI_API_KEY")
    if not dsn or not api_key:
        raise SystemExit("WARM_REFRESH_BLOCKED missing_dsn_or_key")

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = off")
        cached = conn.execute(
            """
            SELECT canonical_match_id, sportradar_event_id, oddspapi_event_id,
                   betradar_id, oddspapi_tournament_id, resolution_status, player_orientation
            FROM oddspapi_event_identity_cache
            ORDER BY last_verified_at DESC
            """
        ).fetchall()
        if not cached:
            emit("ODDSPAPI_WARM_CACHE", {"warm_identity_found": False, "rows": 0})
            emit("ODDSPAPI_WARM_REFRESH_DONE", {"writes": 0})
            return 0

        match_id, sr_event, oddspapi_event, betradar, tournament_id, status, orientation = cached[0]
        mapping = conn.execute(
            """
            SELECT match_id FROM match_source_ids
            WHERE source_system = 'ODDSPAPI' AND source_match_id = %s
            """,
            (str(oddspapi_event),),
        ).fetchone()
        emit(
            "ODDSPAPI_WARM_CACHE",
            {
                "warm_identity_found": True,
                "rows": len(cached),
                "canonical_match_id": int(match_id),
                "oddspapi_event_id": str(oddspapi_event),
                "betradar_id": betradar,
                "oddspapi_tournament_id": int(tournament_id),
                "resolution_status": status,
                "player_orientation": orientation,
                "canonical_mapping_present": mapping is not None,
                "discovery_requests": 0,
            },
        )

        run_started = datetime.now(UTC)
        policy = OddspapiBudgetPolicy()
        stats: dict[str, int] = {}
        try:
            received_at, board_rows, requests_used = _fetch_for_books(
                api_key, (int(tournament_id),), 4, stats
            )
        except TransportError as exc:
            emit("ODDSPAPI_WARM_QUALIFICATION", {"status": "ERROR", "kind": exc.kind})
            emit("ODDSPAPI_WARM_REFRESH_DONE", {"writes": 0})
            return 0

        emit(
            "ODDSPAPI_WARM_QUALIFICATION",
            {
                "status": "OK",
                "qualification_requests": requests_used,
                "planned_requests": policy.per_cycle_limit,
                "board_rows": len(board_rows),
                "fallback_stats": stats,
                "fallback_invoked": bool(stats.get("fallback_requests")),
            },
        )

        merged = merge_oddspapi_odds_books(
            [], board_rows, trusted_fixture_ids=(str(oddspapi_event),)
        )
        retained = len(merged)
        emit(
            "ODDSPAPI_WARM_MERGE",
            {
                "discovery_rows": 0,
                "board_rows": len(board_rows),
                "merged_rows": retained,
                "board_rows_survived": retained > 0,
            },
        )
        if retained == 0:
            emit("ODDSPAPI_WARM_REFRESH_DONE", {"writes": 0, "reason": "board_rows_discarded"})
            return 0

        outcome = ingest_oddspapi_tennis_odds(
            conn,
            payload_rows=merged,
            received_at=received_at,
            candidate_match_ids=(int(match_id),),
        )
        emit(
            "ODDSPAPI_WARM_INGEST",
            {
                "provider_events_seen": outcome.provider_events_seen,
                "exact_events": outcome.exact_events,
                "existing_events": outcome.existing_events,
                "unmatched_events": outcome.unmatched_events,
                "conflict_events": outcome.conflict_events,
                "normalized_quotes": outcome.normalized_quotes,
            },
        )

        # Freshness of the polled observation itself.
        sample = build_oddspapi_tennis_observation(
            event_payload=merged[0],
            received_at=received_at,
            response_id="warm-refresh-sample",
        )
        age_seconds = int((received_at - sample.observation_time).total_seconds())
        emit(
            "ODDSPAPI_WARM_FRESHNESS",
            {
                "observation_time_basis": sample.observation_time_basis,
                "source_observed_at": sample.observation_time,
                "received_at": received_at,
                "market_age_seconds": age_seconds,
                "fresh": is_fresh(sample.observation_time, received_at, TTL_MINUTES),
            },
        )

        # Snapshot advancement for the canonical match.
        snapshots = conn.execute(
            """
            SELECT odds_snapshot_id, source_system, bookmaker_code, source_observed_at, ingested_at
            FROM odds_snapshots
            WHERE match_id = %s AND source_system IN ('SHARP', 'OFFERED')
            ORDER BY ingested_at DESC, odds_snapshot_id DESC
            LIMIT 12
            """,
            (int(match_id),),
        ).fetchall()
        new_snaps = [r for r in snapshots if r[4] >= run_started]
        prior_snaps = [r for r in snapshots if r[4] < run_started]
        emit(
            "ODDSPAPI_WARM_SNAPSHOTS",
            {
                "new_snapshots": len(new_snaps),
                "prior_snapshot_latest": (
                    {
                        "odds_snapshot_id": int(prior_snaps[0][0]),
                        "bookmaker_code": prior_snaps[0][2],
                        "source_observed_at": prior_snaps[0][3],
                        "ingested_at": prior_snaps[0][4],
                    }
                    if prior_snaps
                    else None
                ),
                "new_snapshot_latest": (
                    {
                        "odds_snapshot_id": int(new_snaps[0][0]),
                        "bookmaker_code": new_snaps[0][2],
                        "source_observed_at": new_snaps[0][3],
                        "ingested_at": new_snaps[0][4],
                    }
                    if new_snaps
                    else None
                ),
                "advances": bool(
                    new_snaps
                    and prior_snaps
                    and new_snaps[0][3] > prior_snaps[0][3]
                ),
            },
        )
    emit("ODDSPAPI_WARM_REFRESH_DONE", {"writes": 1})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
