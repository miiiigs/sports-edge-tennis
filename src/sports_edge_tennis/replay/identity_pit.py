"""Point-in-time identity availability for replay.

Live production may resolve a live player to a historical corpus identity using
mappings that were created at any time up to *now*. Historical replay must not
treat a mapping created **after** the replay cutoff ``T`` as if it had existed at
``T``.

This module resolves model identity using only evidence provably available at
``T``:

1. the live player already carries a non-live-fixture ``player_source_ids`` row
   that was created at or before ``T`` (only ``created_at``, the earliest time our
   system could have known the mapping, is used);
2. an append-only ``model_identity_bridge_events`` row with
   ``activated_at <= T`` **and** evidence captured at or before ``T``, whose
   historical target also has a PIT-available non-live source identity;
3. a ``TENNIS_DATA`` alias whose linkage row and observation were both available
   by ``T`` and which resolves to exactly one PIT-eligible historical player.

Anything else fails closed as ``LIVE_UNRESOLVED`` so the engine can mark
``IDENTITY_NOT_AVAILABLE_PIT``. No fuzzy matching and no current-only lookup is
performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg

from sports_edge_tennis.data.model_identity_bridge import LIVE_FIXTURE_SOURCE_SYSTEMS
from sports_edge_tennis.data.normalize import normalize_person_lookup, tennis_data_aliases

IDENTITY_PIT_POLICY_VERSION = "identity_pit_v1"

STATUS_HISTORICAL = "HISTORICAL"
STATUS_BRIDGED = "BRIDGED"
STATUS_LIVE_UNRESOLVED = "LIVE_UNRESOLVED"


@dataclass(frozen=True)
class IdentityResolution:
    live_player_id: int
    model_player_id: int
    status: str
    available: bool
    evidence: str
    policy_version: str = IDENTITY_PIT_POLICY_VERSION


def _has_historical_identity_pit(
    conn: psycopg.Connection,
    *,
    player_id: int,
    as_of: datetime,
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM player_source_ids
            WHERE player_id = %s
              AND source_system <> ALL(%s)
              AND created_at <= %s
            LIMIT 1
            """,
            (player_id, list(LIVE_FIXTURE_SOURCE_SYSTEMS), as_of),
        ).fetchone()
        is not None
    )


def _active_bridge_pit(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
    as_of: datetime,
) -> int | None:
    row = conn.execute(
        """
        SELECT e.action, e.historical_player_id
        FROM model_identity_bridge_events e
        WHERE e.live_player_id = %s
          AND e.activated_at <= %s
          AND e.evidence_as_of <= %s
        ORDER BY e.activated_at DESC, e.model_identity_bridge_event_id DESC
        LIMIT 1
        """,
        (live_player_id, as_of, as_of),
    ).fetchone()
    if row is None or str(row[0]) != "BRIDGE" or row[1] is None:
        return None
    historical_player_id = int(row[1])
    if historical_player_id == live_player_id:
        return None
    if not _has_historical_identity_pit(
        conn, player_id=historical_player_id, as_of=as_of
    ):
        return None
    return historical_player_id


def _alias_candidate_pit(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
    as_of: datetime,
) -> int | None:
    row = conn.execute(
        "SELECT canonical_name FROM players WHERE player_id = %s",
        (live_player_id,),
    ).fetchone()
    if row is None:
        return None
    normalized_aliases = sorted(
        {
            normalize_person_lookup(alias)
            for alias in tennis_data_aliases(row[0])
            if alias and normalize_person_lookup(alias)
        }
    )
    if not normalized_aliases:
        return None

    candidates = conn.execute(
        """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND (
                (pa.first_observed_at IS NOT NULL AND pa.first_observed_at <= %s)
             OR (pa.first_observed_at IS NULL AND pa.created_at <= %s)
          )
          AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> ALL(%s)
                AND psi.created_at <= %s
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
        (
            normalized_aliases,
            live_player_id,
            as_of,
            as_of,
            list(LIVE_FIXTURE_SOURCE_SYSTEMS),
            as_of,
            as_of,
            as_of,
        ),  # first_observed_at OR created_at, then candidate source-id cutoff, then history cutoffs
    ).fetchall()
    if len(candidates) != 1:
        return None
    return int(candidates[0][0])


def resolve_model_player_id_pit(
    conn: psycopg.Connection,
    *,
    live_player_id: int,
    as_of: datetime,
) -> IdentityResolution:
    if _has_historical_identity_pit(conn, player_id=live_player_id, as_of=as_of):
        return IdentityResolution(
            live_player_id=live_player_id,
            model_player_id=live_player_id,
            status=STATUS_HISTORICAL,
            available=True,
            evidence="player_source_ids",
        )

    bridged = _active_bridge_pit(conn, live_player_id=live_player_id, as_of=as_of)
    if bridged is not None:
        return IdentityResolution(
            live_player_id=live_player_id,
            model_player_id=bridged,
            status=STATUS_BRIDGED,
            available=True,
            evidence="model_identity_bridge_events",
        )

    alias = _alias_candidate_pit(conn, live_player_id=live_player_id, as_of=as_of)
    if alias is not None:
        return IdentityResolution(
            live_player_id=live_player_id,
            model_player_id=alias,
            status=STATUS_BRIDGED,
            available=True,
            evidence="tennis_data_alias_pit",
        )

    return IdentityResolution(
        live_player_id=live_player_id,
        model_player_id=live_player_id,
        status=STATUS_LIVE_UNRESOLVED,
        available=False,
        evidence="none",
    )


__all__ = [
    "IDENTITY_PIT_POLICY_VERSION",
    "STATUS_HISTORICAL",
    "STATUS_BRIDGED",
    "STATUS_LIVE_UNRESOLVED",
    "IdentityResolution",
    "resolve_model_player_id_pit",
]
