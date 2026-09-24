"""Fail-closed bridge from live-only provider players to historical model identity.

This does not rewrite append-only source identity. It is a read-time model bridge used
when historical observations from API-Tennis need to connect to the existing corpus.
"""

from __future__ import annotations

from datetime import datetime

import psycopg

from sports_edge_tennis.data.model_identity_bridge import (
    LIVE_FIXTURE_SOURCE_SYSTEMS,
    resolve_active_bridge,
)
from sports_edge_tennis.data.normalize import normalize_person_lookup, tennis_data_aliases


def has_historical_model_identity(conn: psycopg.Connection, player_id: int) -> bool:
    """Return True when the player carries a non-live historical source identity.

    Sportradar is a live fixture provider: its source IDs are attached to live
    canonical players and do not represent the deep historical corpus. A player
    that only carries live-provider identities must remain bridgeable.
    """
    return (
        conn.execute(
            """
            SELECT 1
            FROM player_source_ids
            WHERE player_id = %s
              AND source_system <> ALL(%s)
            LIMIT 1
            """,
            (player_id, list(LIVE_FIXTURE_SOURCE_SYSTEMS)),
        ).fetchone()
        is not None
    )


# Retained name for the guarded Sportradar identity-bridge apply tool. The predicate
# now excludes every live fixture provider, including Sportradar.
has_non_api_source_identity = has_historical_model_identity


def resolve_model_player_id(
    conn: psycopg.Connection,
    *,
    player_id: int,
    as_of: datetime,
) -> int:
    """Map one live-only player to one unambiguous PIT-visible historical identity.

    Zero or multiple candidates retain the supplied player ID. No source mapping is
    mutated, and a player that already has a non-live historical source identity is
    returned as-is.
    """
    if has_historical_model_identity(conn, player_id):
        return player_id

    persisted_bridge = resolve_active_bridge(
        conn,
        live_player_id=player_id,
        as_of=as_of,
    )
    if persisted_bridge is not None:
        return persisted_bridge

    row = conn.execute(
        "SELECT canonical_name FROM players WHERE player_id = %s",
        (player_id,),
    ).fetchone()
    if row is None:
        return player_id

    aliases = sorted(
        {
            normalize_person_lookup(alias)
            for alias in tennis_data_aliases(row[0])
            if alias and normalize_person_lookup(alias)
        }
    )
    if not aliases:
        return player_id

    candidates = conn.execute(
        """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> ALL(%s)
          )
          AND EXISTS (
              SELECT 1
              FROM match_result_observations hist_mro
              JOIN match_versions hist_mv
                ON hist_mv.match_id = hist_mro.match_id
               AND hist_mv.source_observed_at <= hist_mro.source_observed_at
               AND hist_mv.as_of <= hist_mro.as_of
              WHERE (hist_mv.player_1_id = pa.player_id OR hist_mv.player_2_id = pa.player_id)
                AND hist_mro.outcome_status = 'COMPLETED'
                AND hist_mro.source_observed_at <= %s
                AND hist_mro.as_of <= %s
          )
        ORDER BY pa.player_id
        """,
        (aliases, player_id, list(LIVE_FIXTURE_SOURCE_SYSTEMS), as_of, as_of),
    ).fetchall()
    if len(candidates) != 1:
        return player_id
    return int(candidates[0][0])
