from __future__ import annotations

"""Durable OddsPapi event-identity cache.

Caches identity metadata only (never market prices) so the live TARGET odds path
can skip the discovery request when an OddsPapi event identity is already known
and still valid. Any identity that cannot be proven exactly stays uncached and
fail-closed; ambiguous matches are never written.

Invalidation rules (see ``identity_invalidation_reason``): the Sportradar event
id, scheduled start, tournament/provider id, player orientation, or verification
age must still agree, and any provider mismatch/no-event invalidates the entry.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

CACHE_TABLE = "oddspapi_event_identity_cache"
DEFAULT_MAX_IDENTITY_AGE = timedelta(hours=24)
DEFAULT_MAX_START_DRIFT = timedelta(minutes=30)

RESOLUTION_EXACT = "EXACT"
RESOLUTION_CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class OddspapiCachedIdentity:
    canonical_match_id: int
    sportradar_event_id: str | None
    oddspapi_event_id: str
    betradar_id: str | None
    oddspapi_tournament_id: int
    player_orientation: str
    resolution_status: str
    scheduled_start: datetime | None
    resolved_at: datetime
    last_verified_at: datetime


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def identity_invalidation_reason(
    cached: OddspapiCachedIdentity,
    *,
    sportradar_event_id: str | None,
    scheduled_start: datetime | None,
    now: datetime,
    max_identity_age: timedelta = DEFAULT_MAX_IDENTITY_AGE,
    max_start_drift: timedelta = DEFAULT_MAX_START_DRIFT,
) -> str | None:
    """Return a reason string when the cached identity must be revalidated."""
    if cached.resolution_status != RESOLUTION_EXACT:
        return "resolution_not_exact"
    if not cached.oddspapi_event_id:
        return "missing_provider_event_id"
    if sportradar_event_id and cached.sportradar_event_id and sportradar_event_id != cached.sportradar_event_id:
        return "sportradar_event_changed"
    verified_at = _as_utc(cached.last_verified_at)
    if verified_at is None or (now - verified_at) > max_identity_age:
        return "identity_verification_expired"
    cached_start = _as_utc(cached.scheduled_start)
    incoming_start = _as_utc(scheduled_start)
    if cached_start is not None and incoming_start is not None:
        if abs(cached_start - incoming_start) > max_start_drift:
            return "scheduled_start_changed"
    return None


def load_cached_identity(conn, *, match_id: int) -> OddspapiCachedIdentity | None:
    row = conn.execute(
        f"""
        SELECT canonical_match_id, sportradar_event_id, oddspapi_event_id, betradar_id,
               oddspapi_tournament_id, player_orientation, resolution_status,
               scheduled_start, resolved_at, last_verified_at
        FROM {CACHE_TABLE}
        WHERE canonical_match_id = %s
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        return None
    return OddspapiCachedIdentity(
        canonical_match_id=int(row[0]),
        sportradar_event_id=row[1],
        oddspapi_event_id=str(row[2]),
        betradar_id=row[3],
        oddspapi_tournament_id=int(row[4]),
        player_orientation=str(row[5]),
        resolution_status=str(row[6]),
        scheduled_start=row[7],
        resolved_at=row[8],
        last_verified_at=row[9],
    )


def upsert_cached_identity(conn, identity: OddspapiCachedIdentity) -> None:
    if identity.resolution_status != RESOLUTION_EXACT:
        raise ValueError("only EXACT identities may be cached")
    if not identity.oddspapi_event_id:
        raise ValueError("cached identity requires a provider event id")
    conn.execute(
        f"""
        INSERT INTO {CACHE_TABLE} (
            canonical_match_id,
            sportradar_event_id,
            oddspapi_event_id,
            betradar_id,
            oddspapi_tournament_id,
            player_orientation,
            resolution_status,
            scheduled_start,
            resolved_at,
            last_verified_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (canonical_match_id) DO UPDATE SET
            sportradar_event_id = EXCLUDED.sportradar_event_id,
            oddspapi_event_id = EXCLUDED.oddspapi_event_id,
            betradar_id = EXCLUDED.betradar_id,
            oddspapi_tournament_id = EXCLUDED.oddspapi_tournament_id,
            player_orientation = EXCLUDED.player_orientation,
            resolution_status = EXCLUDED.resolution_status,
            scheduled_start = EXCLUDED.scheduled_start,
            resolved_at = EXCLUDED.resolved_at,
            last_verified_at = EXCLUDED.last_verified_at
        """,
        (
            identity.canonical_match_id,
            identity.sportradar_event_id,
            identity.oddspapi_event_id,
            identity.betradar_id,
            identity.oddspapi_tournament_id,
            identity.player_orientation,
            identity.resolution_status,
            identity.scheduled_start,
            identity.resolved_at,
            identity.last_verified_at,
        ),
    )


def invalidate_cached_identity(conn, *, match_id: int) -> None:
    conn.execute(
        f"DELETE FROM {CACHE_TABLE} WHERE canonical_match_id = %s",
        (match_id,),
    )


def partition_cached_identities(
    conn,
    *,
    candidates: list[tuple[int, str | None, datetime | None]],
    now: datetime,
    max_identity_age: timedelta = DEFAULT_MAX_IDENTITY_AGE,
) -> tuple[dict[int, OddspapiCachedIdentity], list[int]]:
    """Return (warm_identity_by_match, cold_match_ids).

    ``candidates`` are ``(match_id, sportradar_event_id, scheduled_start)``.
    Warm means a cached identity passed every invalidation rule.
    """
    warm: dict[int, OddspapiCachedIdentity] = {}
    cold: list[int] = []
    for match_id, sportradar_event_id, scheduled_start in candidates:
        cached = load_cached_identity(conn, match_id=match_id)
        if cached is None:
            cold.append(match_id)
            continue
        reason = identity_invalidation_reason(
            cached,
            sportradar_event_id=sportradar_event_id,
            scheduled_start=scheduled_start,
            now=now,
            max_identity_age=max_identity_age,
        )
        if reason is not None:
            invalidate_cached_identity(conn, match_id=match_id)
            cold.append(match_id)
            continue
        warm[match_id] = cached
    return warm, cold


__all__ = [
    "CACHE_TABLE",
    "DEFAULT_MAX_IDENTITY_AGE",
    "DEFAULT_MAX_START_DRIFT",
    "RESOLUTION_CONFLICT",
    "RESOLUTION_EXACT",
    "OddspapiCachedIdentity",
    "identity_invalidation_reason",
    "invalidate_cached_identity",
    "load_cached_identity",
    "partition_cached_identities",
    "upsert_cached_identity",
]
