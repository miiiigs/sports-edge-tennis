"""Fail-closed bridge from live-only provider players to historical model identity.

This does not rewrite append-only source identity. It is a read-time model bridge used
when historical observations from API-Tennis need to connect to the existing corpus.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import psycopg

from sports_edge_tennis.data.model_identity_bridge import (
    LIVE_FIXTURE_SOURCE_SYSTEMS,
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

    Player source ids and aliases are reference identity data. This helper is a
    current-state predicate; the PIT resolver below applies ``created_at`` gates.
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


@dataclass(frozen=True)
class IdentityResolution:
    """One player's fail-closed PIT identity resolution.

    ``method`` is one of ``HISTORICAL_ID``, ``PERSISTED_BRIDGE``, ``ALIAS``,
    ``AMBIGUOUS``, ``NO_CANDIDATE``, ``NO_HISTORICAL_CANDIDATE`` or ``NO_PLAYER``.
    ``matched_alias`` and ``bridge_event_id`` are audit provenance, never model
    inputs.
    """

    player_id: int
    resolved_player_id: int
    method: str
    candidate_count: int = 0
    matched_alias: str | None = None
    bridge_event_id: int | None = None
    visible_from: datetime | None = None


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
    return resolve_model_player_ids_with_provenance(
        conn, player_ids=[player_id], as_of=as_of
    )[player_id].resolved_player_id


def resolve_model_player_ids_with_provenance(
    conn: psycopg.Connection,
    *,
    player_ids: Iterable[int],
    as_of: datetime,
) -> dict[int, IdentityResolution]:
    """Bulk, set-based PIT identity resolution with audit provenance.

    Every source/alias/history join is bounded by the original cutoff:
    ``player_source_ids.created_at <= as_of``, ``player_aliases.created_at <= as_of``,
    ``player_aliases.first_observed_at <= as_of`` and
    ``match_result_observations.ingested_at <= as_of``. A backdated alias with a
    later ingestion timestamp therefore cannot leak into a historical replay.
    """
    unique_ids = sorted({int(pid) for pid in player_ids})
    if not unique_ids:
        return {}
    live_sources = list(LIVE_FIXTURE_SOURCE_SYSTEMS)
    resolutions = {
        player_id: IdentityResolution(
            player_id=player_id,
            resolved_player_id=player_id,
            method="NO_CANDIDATE",
        )
        for player_id in unique_ids
    }

    # Stage B: IDs that already carry a non-live historical identity map to themselves.
    historical_rows = conn.execute(
        """
        SELECT DISTINCT psi.player_id
        FROM player_source_ids psi
        LEFT JOIN ingest_runs ing ON ing.ingest_run_id = psi.ingest_run_id
        WHERE psi.player_id = ANY(%s)
          AND psi.source_system <> ALL(%s)
          AND COALESCE(ing.finished_at, ing.started_at, psi.created_at) <= %s
        """,
        (unique_ids, live_sources, as_of),
    ).fetchall()
    historical_ids = {int(row[0]) for row in historical_rows}
    for player_id in historical_ids:
        resolutions[player_id] = IdentityResolution(
            player_id=player_id,
            resolved_player_id=player_id,
            method="HISTORICAL_ID",
        )

    unresolved = [player_id for player_id in unique_ids if player_id not in historical_ids]
    if not unresolved:
        return resolutions

    # Stage C: latest PIT-visible persisted bridge per live player, resolved in one query.
    bridge_rows = conn.execute(
        """
        SELECT DISTINCT ON (e.live_player_id)
            e.live_player_id,
            e.action,
            e.historical_player_id,
            e.model_identity_bridge_event_id,
            e.activated_at,
            EXISTS (
                SELECT 1
                FROM player_source_ids psi
                LEFT JOIN ingest_runs ing ON ing.ingest_run_id = psi.ingest_run_id
                WHERE psi.player_id = e.historical_player_id
                  AND psi.source_system <> ALL(%s)
                  AND COALESCE(ing.finished_at, ing.started_at, psi.created_at) <= %s
            ) AS target_has_historical_identity
        FROM model_identity_bridge_events e
        WHERE e.live_player_id = ANY(%s)
          AND e.activated_at <= %s
        ORDER BY
            e.live_player_id,
            e.activated_at DESC,
            e.model_identity_bridge_event_id DESC
        """,
        (live_sources, as_of, unresolved, as_of),
    ).fetchall()
    bridged_live_ids: set[int] = set()
    for (
        live_player_id,
        action,
        historical_player_id,
        bridge_event_id,
        activated_at,
        target_has_historical,
    ) in bridge_rows:
        live_player_id = int(live_player_id)
        bridged_live_ids.add(live_player_id)
        if (
            str(action) == "BRIDGE"
            and historical_player_id is not None
            and bool(target_has_historical)
            and int(historical_player_id) != live_player_id
        ):
            resolutions[live_player_id] = IdentityResolution(
                player_id=live_player_id,
                resolved_player_id=int(historical_player_id),
                method="PERSISTED_BRIDGE",
                bridge_event_id=int(bridge_event_id),
                visible_from=activated_at,
            )

    unresolved = [
        player_id
        for player_id in unresolved
        if player_id not in bridged_live_ids
        or resolutions[player_id].resolved_player_id == player_id
    ]
    if not unresolved:
        return resolutions

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
            resolutions[player_id] = IdentityResolution(
                player_id=player_id,
                resolved_player_id=player_id,
                method="NO_PLAYER",
            )
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
        return resolutions

    # Stage E: unique historical alias candidates, resolved in one set-based query.
    candidate_rows = conn.execute(
        """
        WITH lookup AS (
            SELECT live_player_id, normalized_alias
            FROM unnest(%s::bigint[], %s::text[]) AS t(live_player_id, normalized_alias)
        )
        SELECT DISTINCT l.live_player_id, pa.player_id, l.normalized_alias,
               GREATEST(
                   COALESCE(pai.finished_at, pai.started_at, pa.created_at),
                   COALESCE(pa.first_observed_at, pa.created_at)
               ) AS visible_from
        FROM lookup l
        JOIN player_aliases pa
          ON pa.normalized_alias = l.normalized_alias
         AND pa.source_system = 'TENNIS_DATA'
         AND pa.player_id <> l.live_player_id
        LEFT JOIN ingest_runs pai ON pai.ingest_run_id = pa.ingest_run_id
        WHERE COALESCE(pai.finished_at, pai.started_at, pa.created_at) <= %s
          AND (pa.first_observed_at IS NULL OR pa.first_observed_at <= %s)
          AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              LEFT JOIN ingest_runs psi_ir ON psi_ir.ingest_run_id = psi.ingest_run_id
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> ALL(%s)
                AND COALESCE(psi_ir.finished_at, psi_ir.started_at, psi.created_at) <= %s
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
        ORDER BY l.live_player_id, pa.player_id, l.normalized_alias
        """,
        (
            lookup_live_ids,
            lookup_aliases,
            as_of,
            as_of,
            live_sources,
            as_of,
            as_of,
            as_of,
        ),
    ).fetchall()
    candidates_by_live: dict[int, dict[int, set[str]]] = {}
    visible_by_candidate: dict[tuple[int, int], object] = {}
    for live_player_id, candidate_player_id, normalized_alias, visible_from in candidate_rows:
        candidates_by_live.setdefault(int(live_player_id), {}).setdefault(
            int(candidate_player_id), set()
        ).add(str(normalized_alias))
        visible_by_candidate[(int(live_player_id), int(candidate_player_id))] = visible_from
    for player_id in unresolved:
        candidates = candidates_by_live.get(player_id, {})
        if len(candidates) == 1:
            candidate_id, aliases = next(iter(candidates.items()))
            resolutions[player_id] = IdentityResolution(
                player_id=player_id,
                resolved_player_id=candidate_id,
                method="ALIAS",
                candidate_count=1,
                matched_alias=min(aliases),
                visible_from=visible_by_candidate.get((player_id, candidate_id)),
            )
        elif len(candidates) > 1:
            resolutions[player_id] = IdentityResolution(
                player_id=player_id,
                resolved_player_id=player_id,
                method="AMBIGUOUS",
                candidate_count=len(candidates),
            )
        else:
            resolutions[player_id] = IdentityResolution(
                player_id=player_id,
                resolved_player_id=player_id,
                method="NO_HISTORICAL_CANDIDATE",
            )
    return resolutions


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
    resolutions = resolve_model_player_ids_with_provenance(
        conn, player_ids=player_ids, as_of=as_of
    )
    return {
        player_id: resolution.resolved_player_id
        for player_id, resolution in resolutions.items()
    }
