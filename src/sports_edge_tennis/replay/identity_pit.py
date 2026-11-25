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
2. an append-only ``model_identity_bridge_events`` row that was **inserted**,
   activated and evidence-stamped at or before ``T``, whose historical target also
   has a PIT-available non-live source identity;
3. a ``TENNIS_DATA`` alias whose linkage row and first observation were both
   available by ``T`` and which resolves to exactly one PIT-eligible historical
   player.

Mutable reference data is treated as a leakage channel, not an oracle. With
migration 0025 the resolver reads append-only ``player_name_versions`` and
``player_alias_versions`` rows whose ``valid_from`` is at or before ``T``. A rename
or alias linkage insertion after ``T`` therefore fails closed instead of rewriting
history. On a pre-0025 schema the legacy current-table gates are retained:
``players.canonical_name`` only when ``updated_at <= T`` (or never updated), and
``player_aliases`` on ``created_at``/``first_observed_at``.

Anything else fails closed as ``LIVE_UNRESOLVED`` so the engine can mark
``IDENTITY_NOT_AVAILABLE_PIT``. No fuzzy matching and no current-only lookup is
performed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg

from sports_edge_tennis.data.model_identity import (
    NAME_SOURCE_CANONICAL,
    NAME_SOURCE_UNAVAILABLE,
    identity_version_tables_available,
)
from sports_edge_tennis.data.model_identity_bridge import LIVE_FIXTURE_SOURCE_SYSTEMS
from sports_edge_tennis.data.normalize import (
    normalize_person_lookup,
    tennis_data_aliases,
)

IDENTITY_PIT_POLICY_VERSION = "identity_pit_v2"

STATUS_HISTORICAL = "HISTORICAL"
STATUS_BRIDGED = "BRIDGED"
STATUS_LIVE_UNRESOLVED = "LIVE_UNRESOLVED"

EVIDENCE_PLAYER_SOURCE_IDS = "player_source_ids"
EVIDENCE_BRIDGE_EVENTS = "model_identity_bridge_events"
EVIDENCE_TENNIS_DATA_ALIAS = "tennis_data_alias_pit"
EVIDENCE_NONE = "none"


@dataclass(frozen=True)
class IdentityResolution:
    live_player_id: int
    model_player_id: int
    status: str
    available: bool
    evidence: str
    policy_version: str = IDENTITY_PIT_POLICY_VERSION
    name_source: str = NAME_SOURCE_UNAVAILABLE
    observed_at: datetime | None = None

    @property
    def bridged(self) -> bool:
        return self.model_player_id != self.live_player_id


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
          AND e.created_at <= %s
        ORDER BY e.activated_at DESC, e.model_identity_bridge_event_id DESC
        LIMIT 1
        """,
        (live_player_id, as_of, as_of, as_of),
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
) -> tuple[int | None, str]:
    """Return ``(candidate_player_id, name_source)`` using PIT-visible names only."""
    if identity_version_tables_available(conn):
        row = conn.execute(
            """
            SELECT COALESCE(v.canonical_name, p.canonical_name)
            FROM players p
            LEFT JOIN LATERAL (
                SELECT vv.canonical_name
                FROM player_name_versions vv
                WHERE vv.player_id = p.player_id
                  AND vv.valid_from <= %s
                ORDER BY vv.valid_from DESC, vv.player_name_version_id DESC
                LIMIT 1
            ) v ON TRUE
            WHERE p.player_id = %s
              AND (v.canonical_name IS NOT NULL OR p.updated_at = p.created_at)
            """,
            (as_of, live_player_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT canonical_name
            FROM players
            WHERE player_id = %s
              AND (updated_at <= %s OR updated_at = created_at)
            """,
            (live_player_id, as_of),
        ).fetchone()
    if row is None:
        return None, NAME_SOURCE_UNAVAILABLE
    normalized_aliases = sorted(
        {
            normalize_person_lookup(alias)
            for alias in tennis_data_aliases(row[0])
            if alias and normalize_person_lookup(alias)
        }
    )
    if not normalized_aliases:
        return None, NAME_SOURCE_CANONICAL

    if identity_version_tables_available(conn):
        alias_sql = """
        SELECT DISTINCT pa.player_id
        FROM player_alias_versions pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND (pa.first_observed_at IS NULL OR pa.first_observed_at <= %s)
          AND pa.valid_from <= %s
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
        """
        params = (
            normalized_aliases,
            live_player_id,
            as_of,
            as_of,
            list(LIVE_FIXTURE_SOURCE_SYSTEMS),
            as_of,
            as_of,
            as_of,
        )
    else:
        alias_sql = """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND (pa.first_observed_at IS NULL OR pa.first_observed_at <= %s)
          AND pa.created_at <= %s
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
        """
        params = (
            normalized_aliases,
            live_player_id,
            as_of,
            as_of,
            list(LIVE_FIXTURE_SOURCE_SYSTEMS),
            as_of,
            as_of,
            as_of,
        )
    candidates = conn.execute(alias_sql, params).fetchall()
    if len(candidates) != 1:
        return None, NAME_SOURCE_CANONICAL
    return int(candidates[0][0]), NAME_SOURCE_CANONICAL


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
            evidence=EVIDENCE_PLAYER_SOURCE_IDS,
            observed_at=as_of,
        )

    bridged = _active_bridge_pit(conn, live_player_id=live_player_id, as_of=as_of)
    if bridged is not None:
        return IdentityResolution(
            live_player_id=live_player_id,
            model_player_id=bridged,
            status=STATUS_BRIDGED,
            available=True,
            evidence=EVIDENCE_BRIDGE_EVENTS,
            observed_at=as_of,
        )

    alias, name_source = _alias_candidate_pit(
        conn, live_player_id=live_player_id, as_of=as_of
    )
    if alias is not None:
        return IdentityResolution(
            live_player_id=live_player_id,
            model_player_id=alias,
            status=STATUS_BRIDGED,
            available=True,
            evidence=EVIDENCE_TENNIS_DATA_ALIAS,
            name_source=name_source,
            observed_at=as_of,
        )

    return IdentityResolution(
        live_player_id=live_player_id,
        model_player_id=live_player_id,
        status=STATUS_LIVE_UNRESOLVED,
        available=False,
        evidence=EVIDENCE_NONE,
        name_source=name_source,
        observed_at=as_of,
    )


__all__ = [
    "EVIDENCE_BRIDGE_EVENTS",
    "EVIDENCE_NONE",
    "EVIDENCE_PLAYER_SOURCE_IDS",
    "EVIDENCE_TENNIS_DATA_ALIAS",
    "IDENTITY_PIT_POLICY_VERSION",
    "STATUS_BRIDGED",
    "STATUS_HISTORICAL",
    "STATUS_LIVE_UNRESOLVED",
    "IdentityResolution",
    "resolve_model_player_id_pit",
]
