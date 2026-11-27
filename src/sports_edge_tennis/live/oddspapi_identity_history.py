"""Reproducible point-in-time reads over OddsPapi identity history.

``oddspapi_event_identity_cache`` is mutable (upsert overwrites, invalidation
deletes). ``oddspapi_event_identity_history`` (migration 0024) is append-only
and records the identity values in force at each event. This module reads only
that immutable history, so a "was this fixture warm at cutoff T" question can be
answered from evidence instead of the current cache row.

Semantics:
  * Only rows with ``recorded_at <= T`` are visible. ``recorded_at`` is the
    database insert time and can never be backdated by the writer.
  * The latest visible event wins. ``UPSERTED`` means warm at ``T``;
    ``INVALIDATED`` means cold at ``T``.
  * No event at or before ``T`` means ``NEVER_RECORDED``, which is *not* proof
    that the provider had no identity - only that this system did not record
    one by then.

The policy version is part of every result so downstream research can pin the
interpretation it used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sports_edge_tennis.live.oddspapi_identity_cache import (
    HISTORY_TABLE,
    OddspapiCachedIdentity,
)

HISTORY_PIT_POLICY_VERSION = "oddspapi_identity_history_pit_v1"

STATUS_WARM = "WARM"
STATUS_INVALIDATED = "INVALIDATED"
STATUS_NEVER_RECORDED = "NEVER_RECORDED"


class IdentityHistoryUnavailable(RuntimeError):
    """Raised when the append-only history table has not been migrated yet."""


@dataclass(frozen=True)
class IdentityHistoryRecord:
    oddspapi_event_identity_history_id: int
    canonical_match_id: int
    event_type: str
    oddspapi_event_id: str
    oddspapi_tournament_id: int
    player_orientation: str
    resolution_status: str
    scheduled_start: datetime | None
    resolved_at: datetime
    last_verified_at: datetime
    recorded_at: datetime
    sportradar_event_id: str | None = None
    betradar_id: str | None = None

    def as_cached_identity(self) -> OddspapiCachedIdentity:
        return OddspapiCachedIdentity(
            canonical_match_id=self.canonical_match_id,
            sportradar_event_id=self.sportradar_event_id,
            oddspapi_event_id=self.oddspapi_event_id,
            betradar_id=self.betradar_id,
            oddspapi_tournament_id=self.oddspapi_tournament_id,
            player_orientation=self.player_orientation,
            resolution_status=self.resolution_status,
            scheduled_start=self.scheduled_start,
            resolved_at=self.resolved_at,
            last_verified_at=self.last_verified_at,
        )


@dataclass(frozen=True)
class IdentityAtCutoff:
    canonical_match_id: int
    status: str
    identity: OddspapiCachedIdentity | None
    recorded_at: datetime | None
    policy_version: str = HISTORY_PIT_POLICY_VERSION

    @property
    def warm(self) -> bool:
        return self.status == STATUS_WARM


def _require_history_table(conn) -> None:
    row = conn.execute(
        f"SELECT to_regclass('public.{HISTORY_TABLE}') IS NOT NULL"
    ).fetchone()
    if not (row and row[0]):
        raise IdentityHistoryUnavailable(
            "migration 0024_oddspapi_identity_history is not applied"
        )


def load_identity_history(
    conn,
    *,
    match_id: int,
    as_of: datetime | None = None,
    limit: int | None = None,
) -> list[IdentityHistoryRecord]:
    """Every recorded event for a match, oldest first, optionally up to ``as_of``."""
    _require_history_table(conn)
    params: list[object] = [match_id]
    cutoff_sql = ""
    if as_of is not None:
        cutoff_sql = "AND recorded_at <= %s"
        params.append(as_of)
    limit_sql = ""
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        limit_sql = "LIMIT %s"
        params.append(limit)
    rows = conn.execute(
        f"""
        SELECT oddspapi_event_identity_history_id, canonical_match_id, event_type,
               oddspapi_event_id, oddspapi_tournament_id, player_orientation,
               resolution_status, scheduled_start, resolved_at, last_verified_at,
               recorded_at, sportradar_event_id, betradar_id
        FROM {HISTORY_TABLE}
        WHERE canonical_match_id = %s {cutoff_sql}
        ORDER BY recorded_at, oddspapi_event_identity_history_id
        {limit_sql}
        """,
        tuple(params),
    ).fetchall()
    return [IdentityHistoryRecord(*row) for row in rows]


def identity_at_cutoff(
    conn,
    *,
    match_id: int,
    as_of: datetime,
) -> IdentityAtCutoff:
    """Reconstruct the identity state that was in force at ``as_of``."""
    _require_history_table(conn)
    row = conn.execute(
        f"""
        SELECT event_type, oddspapi_event_id, oddspapi_tournament_id,
               player_orientation, resolution_status, scheduled_start,
               resolved_at, last_verified_at, recorded_at,
               sportradar_event_id, betradar_id
        FROM {HISTORY_TABLE}
        WHERE canonical_match_id = %s AND recorded_at <= %s
        ORDER BY recorded_at DESC, oddspapi_event_identity_history_id DESC
        LIMIT 1
        """,
        (match_id, as_of),
    ).fetchone()
    if row is None:
        return IdentityAtCutoff(
            canonical_match_id=match_id,
            status=STATUS_NEVER_RECORDED,
            identity=None,
            recorded_at=None,
        )
    event_type = str(row[0])
    if event_type == "INVALIDATED":
        return IdentityAtCutoff(
            canonical_match_id=match_id,
            status=STATUS_INVALIDATED,
            identity=None,
            recorded_at=row[8],
        )
    if event_type != "UPSERTED":
        raise IdentityHistoryUnavailable(
            f"unexpected history event_type {event_type!r}"
        )
    identity = OddspapiCachedIdentity(
        canonical_match_id=match_id,
        sportradar_event_id=row[9],
        oddspapi_event_id=str(row[1]),
        betradar_id=row[10],
        oddspapi_tournament_id=int(row[2]),
        player_orientation=str(row[3]),
        resolution_status=str(row[4]),
        scheduled_start=row[5],
        resolved_at=row[6],
        last_verified_at=row[7],
    )
    return IdentityAtCutoff(
        canonical_match_id=match_id,
        status=STATUS_WARM,
        identity=identity,
        recorded_at=row[8],
    )


def identity_availability_at_cutoff(
    conn,
    *,
    match_ids: list[int],
    as_of: datetime,
) -> dict[int, IdentityAtCutoff]:
    """Bulk ``identity_at_cutoff`` for an evidence census."""
    return {
        match_id: identity_at_cutoff(conn, match_id=match_id, as_of=as_of)
        for match_id in match_ids
    }


def summarize_identity_availability(
    conn,
    *,
    match_ids: list[int],
    as_of: datetime,
) -> dict[str, int]:
    """Counts by status plus the evaluated denominator.

    ``measured_new_warm`` is deliberately absent: a PIT read reports the state
    that was recorded, not a promise that any fixture became recoverable.
    """
    states = identity_availability_at_cutoff(conn, match_ids=match_ids, as_of=as_of)
    summary = {
        "fixtures": len(match_ids),
        STATUS_WARM: 0,
        STATUS_INVALIDATED: 0,
        STATUS_NEVER_RECORDED: 0,
    }
    for state in states.values():
        summary[state.status] += 1
    return summary


__all__ = [
    "HISTORY_PIT_POLICY_VERSION",
    "STATUS_INVALIDATED",
    "STATUS_NEVER_RECORDED",
    "STATUS_WARM",
    "IdentityAtCutoff",
    "IdentityHistoryRecord",
    "IdentityHistoryUnavailable",
    "identity_at_cutoff",
    "identity_availability_at_cutoff",
    "load_identity_history",
    "summarize_identity_availability",
]
