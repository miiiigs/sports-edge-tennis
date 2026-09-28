"""Fail-closed bridge from live-only provider players to historical model identity.

This does not rewrite append-only source identity. It is a read-time model bridge used
when historical observations from API-Tennis need to connect to the existing corpus.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import psycopg

from sports_edge_tennis.data.model_identity_bridge import (
    LIVE_FIXTURE_SOURCE_SYSTEMS,
    resolve_active_bridge,
)
from sports_edge_tennis.data.normalize import (
    normalize_person_lookup,
    tennis_data_aliases,
)


def has_historical_model_identity(conn: psycopg.Connection, player_id: int) -> bool:
    """Return True when the player carries a non-live historical source identity.

    Sportradar is a live fixture provider: its source IDs are attached to live
    canonical players and do not represent the deep historical corpus. A player
    that only carries live-provider identities must remain bridgeable.

    Player source ids and aliases are reference identity data and are not
    time-scoped here; the temporal boundary for replay is enforced by the dedicated
    ``replay.identity_pit`` resolver.
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


def resolve_model_player_ids(
    conn: psycopg.Connection,
    *,
    player_ids: Iterable[int],
    as_of: datetime,
) -> dict[int, int]:
    """Bulk, set-based equivalent of :func:`resolve_model_player_id`.

    Resolves every supplied player ID to its model identity using a bounded number of
    SQL statements rather than one query per player. Semantics are identical to calling
    :func:`resolve_model_player_id` individually: historical identities map to
    themselves, an active latest PIT-visible persisted bridge wins, a single unique
    ``TENNIS_DATA`` alias candidate wins, and zero or multiple candidates fail closed by
    retaining the supplied player ID. Nothing is written and no source mapping changes.
    """
    unique_ids = sorted({int(pid) for pid in player_ids})
    if not unique_ids:
        return {}
    live_sources = list(LIVE_FIXTURE_SOURCE_SYSTEMS)
    mapping = {player_id: player_id for player_id in unique_ids}

    # Stage B: IDs that already carry a non-live historical identity map to themselves.
    historical_rows = conn.execute(
        """
        SELECT DISTINCT player_id
        FROM player_source_ids
        WHERE player_id = ANY(%s)
          AND source_system <> ALL(%s)
        """,
        (unique_ids, live_sources),
    ).fetchall()
    historical_ids = {int(row[0]) for row in historical_rows}
    mapping.update({player_id: player_id for player_id in historical_ids})

    unresolved = [player_id for player_id in unique_ids if player_id not in historical_ids]
    if not unresolved:
        return mapping

    # Stage C: latest PIT-visible persisted bridge per live player, resolved in one query.
    bridge_rows = conn.execute(
        """
        SELECT DISTINCT ON (e.live_player_id)
            e.live_player_id,
            e.action,
            e.historical_player_id,
            EXISTS (
                SELECT 1
                FROM player_source_ids psi
                WHERE psi.player_id = e.historical_player_id
                  AND psi.source_system <> ALL(%s)
            ) AS target_has_historical_identity
        FROM model_identity_bridge_events e
        WHERE e.live_player_id = ANY(%s)
          AND e.activated_at <= %s
        ORDER BY
            e.live_player_id,
            e.activated_at DESC,
            e.model_identity_bridge_event_id DESC
        """,
        (live_sources, unresolved, as_of),
    ).fetchall()
    bridged_live_ids: set[int] = set()
    for live_player_id, action, historical_player_id, target_has_historical in bridge_rows:
        live_player_id = int(live_player_id)
        bridged_live_ids.add(live_player_id)
        if (
            str(action) == "BRIDGE"
            and historical_player_id is not None
            and bool(target_has_historical)
            and int(historical_player_id) != live_player_id
        ):
            mapping[live_player_id] = int(historical_player_id)

    unresolved = [
        player_id
        for player_id in unresolved
        if player_id not in bridged_live_ids or mapping[player_id] == player_id
    ]
    if not unresolved:
        return mapping

    # Stage D: canonical names for the remaining unresolved players, in one query.
    name_rows = conn.execute(
        "SELECT player_id, canonical_name FROM players WHERE player_id = ANY(%s)",
        (unresolved,),
    ).fetchall()
    canonical_names = {int(row[0]): str(row[1]) for row in name_rows}

    lookup_live_ids: list[int] = []
    lookup_aliases: list[str] = []
    for player_id in unresolved:
        canonical_name = canonical_names.get(player_id)
        if not canonical_name:
            continue
        aliases = sorted(
            {
                normalize_person_lookup(alias)
                for alias in tennis_data_aliases(canonical_name)
                if alias and normalize_person_lookup(alias)
            }
        )
        for alias in aliases:
            lookup_live_ids.append(player_id)
            lookup_aliases.append(alias)
    if not lookup_live_ids:
        return mapping

    # Stage E: unique historical alias candidates, resolved in one set-based query.
    candidate_rows = conn.execute(
        """
        WITH lookup AS (
            SELECT live_player_id, normalized_alias
            FROM unnest(%s::bigint[], %s::text[]) AS t(live_player_id, normalized_alias)
        )
        SELECT DISTINCT l.live_player_id, pa.player_id
        FROM lookup l
        JOIN player_aliases pa
          ON pa.normalized_alias = l.normalized_alias
         AND pa.source_system = 'TENNIS_DATA'
         AND pa.player_id <> l.live_player_id
        WHERE EXISTS (
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
        ORDER BY l.live_player_id, pa.player_id
        """,
        (lookup_live_ids, lookup_aliases, live_sources, as_of, as_of),
    ).fetchall()
    candidates_by_live: dict[int, set[int]] = {}
    for live_player_id, candidate_player_id in candidate_rows:
        candidates_by_live.setdefault(int(live_player_id), set()).add(int(candidate_player_id))
    for player_id in unresolved:
        unique_candidates = candidates_by_live.get(player_id, set())
        if len(unique_candidates) == 1:
            mapping[player_id] = next(iter(unique_candidates))
    return mapping
