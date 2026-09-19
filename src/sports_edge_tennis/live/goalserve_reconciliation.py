from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Sequence

import psycopg

from sports_edge_tennis.live.goalserve import GoalserveTennisMatchEnvelope
from sports_edge_tennis.live.sportradar_benchmark import deterministic_name_keys


SOURCE_SYSTEM = "GOALSERVE_TENNIS"
DEFAULT_MAX_START_DELTA_MINUTES = 120

ResolutionStatus = Literal["EXACT", "UNMATCHED", "AMBIGUOUS", "EXISTING"]


@dataclass(frozen=True)
class GoalserveCanonicalCandidate:
    match_id: int
    match_version_id: int
    canonical_player_1_id: int
    canonical_player_2_id: int
    canonical_player_1_name: str
    canonical_player_2_name: str
    start_delta_minutes: int
    provider_orientation: str


@dataclass(frozen=True)
class GoalserveEventResolution:
    status: ResolutionStatus
    match_id: int | None
    candidate: GoalserveCanonicalCandidate | None
    candidate_count: int


class GoalserveReconciliationError(RuntimeError):
    pass


def reconcile_goalserve_event(
    conn: psycopg.Connection,
    *,
    envelope: GoalserveTennisMatchEnvelope,
    candidate_match_ids: Sequence[int],
    as_of: datetime,
    max_start_delta_minutes: int = DEFAULT_MAX_START_DELTA_MINUTES,
) -> GoalserveEventResolution:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if max_start_delta_minutes < 0:
        raise ValueError("max_start_delta_minutes must be non-negative")

    existing = conn.execute(
        """
        SELECT match_id
        FROM match_source_ids
        WHERE source_system = %s
          AND source_match_id = %s
        """,
        (SOURCE_SYSTEM, envelope.provider_event_id),
    ).fetchone()
    if existing is not None:
        match_id = int(existing[0])
        if candidate_match_ids and match_id not in set(int(v) for v in candidate_match_ids):
            raise GoalserveReconciliationError(
                "existing Goalserve event mapping is outside the requested candidate slate"
            )
        return GoalserveEventResolution("EXISTING", match_id, None, 1)

    unique_ids = tuple(dict.fromkeys(int(value) for value in candidate_match_ids))
    if not unique_ids:
        return GoalserveEventResolution("UNMATCHED", None, None, 0)

    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.match_version_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at
            FROM match_versions mv
            WHERE mv.match_id = ANY(%s)
              AND mv.source_observed_at <= %s
              AND mv.as_of <= %s
            ORDER BY
                mv.match_id,
                mv.as_of DESC,
                mv.source_observed_at DESC,
                mv.match_version_id DESC
        )
        SELECT
            latest.match_id,
            latest.match_version_id,
            latest.player_1_id,
            latest.player_2_id,
            latest.match_start_at,
            p1.canonical_name,
            p2.canonical_name
        FROM latest
        JOIN players p1 ON p1.player_id = latest.player_1_id
        JOIN players p2 ON p2.player_id = latest.player_2_id
        WHERE latest.match_start_at IS NOT NULL
        ORDER BY latest.match_id
        """,
        (list(unique_ids), as_of, as_of),
    ).fetchall()

    candidates: list[GoalserveCanonicalCandidate] = []
    for row in rows:
        candidate = _match_candidate(
            row,
            envelope=envelope,
            max_start_delta_minutes=max_start_delta_minutes,
        )
        if candidate is not None:
            candidates.append(candidate)

    if len(candidates) == 1:
        value = candidates[0]
        return GoalserveEventResolution("EXACT", value.match_id, value, 1)
    if len(candidates) > 1:
        return GoalserveEventResolution("AMBIGUOUS", None, None, len(candidates))
    return GoalserveEventResolution("UNMATCHED", None, None, 0)


def apply_goalserve_identity_bridge(
    conn: psycopg.Connection,
    *,
    envelope: GoalserveTennisMatchEnvelope,
    resolution: GoalserveEventResolution,
) -> str:
    """Attach Goalserve event/player IDs only after an exact canonical reconciliation."""
    if resolution.status == "EXISTING":
        return "EXISTING"
    if resolution.status != "EXACT" or resolution.candidate is None:
        raise GoalserveReconciliationError(
            "Goalserve identity bridge requires one exact canonical candidate"
        )

    candidate = resolution.candidate
    if candidate.provider_orientation == "DIRECT":
        bindings = (
            (envelope.player_1_id, candidate.canonical_player_1_id),
            (envelope.player_2_id, candidate.canonical_player_2_id),
        )
    elif candidate.provider_orientation == "REVERSED":
        bindings = (
            (envelope.player_1_id, candidate.canonical_player_2_id),
            (envelope.player_2_id, candidate.canonical_player_1_id),
        )
    else:
        raise GoalserveReconciliationError("unsupported provider orientation")

    with conn.transaction():
        for provider_player_id, canonical_player_id in bindings:
            existing = conn.execute(
                """
                SELECT player_id
                FROM player_source_ids
                WHERE source_system = %s
                  AND source_player_id = %s
                FOR UPDATE
                """,
                (SOURCE_SYSTEM, provider_player_id),
            ).fetchone()
            if existing is not None:
                if int(existing[0]) != int(canonical_player_id):
                    raise GoalserveReconciliationError(
                        "Goalserve player source ID conflicts with canonical player"
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO player_source_ids (
                        player_id,
                        source_system,
                        source_player_id
                    )
                    VALUES (%s, %s, %s)
                    """,
                    (canonical_player_id, SOURCE_SYSTEM, provider_player_id),
                )

        existing_event = conn.execute(
            """
            SELECT match_id
            FROM match_source_ids
            WHERE source_system = %s
              AND source_match_id = %s
            FOR UPDATE
            """,
            (SOURCE_SYSTEM, envelope.provider_event_id),
        ).fetchone()
        if existing_event is not None:
            if int(existing_event[0]) != candidate.match_id:
                raise GoalserveReconciliationError(
                    "Goalserve event source ID conflicts with canonical match"
                )
            return "EXISTING"

        conn.execute(
            """
            INSERT INTO match_source_ids (
                match_id,
                match_version_id,
                source_system,
                source_match_id
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                candidate.match_id,
                candidate.match_version_id,
                SOURCE_SYSTEM,
                envelope.provider_event_id,
            ),
        )
    return "INSERTED"


def _match_candidate(
    row,
    *,
    envelope: GoalserveTennisMatchEnvelope,
    max_start_delta_minutes: int,
) -> GoalserveCanonicalCandidate | None:
    (
        match_id,
        match_version_id,
        canonical_p1,
        canonical_p2,
        start_at,
        p1_name,
        p2_name,
    ) = row
    provider_p1 = deterministic_name_keys(envelope.player_1_name)
    provider_p2 = deterministic_name_keys(envelope.player_2_name)
    canonical_p1_keys = deterministic_name_keys(str(p1_name))
    canonical_p2_keys = deterministic_name_keys(str(p2_name))

    direct = bool(provider_p1 & canonical_p1_keys) and bool(
        provider_p2 & canonical_p2_keys
    )
    reversed_match = bool(provider_p1 & canonical_p2_keys) and bool(
        provider_p2 & canonical_p1_keys
    )
    if direct == reversed_match:
        return None

    delta = int(abs((start_at - envelope.match_start_at).total_seconds()) // 60)
    if delta > max_start_delta_minutes:
        return None

    return GoalserveCanonicalCandidate(
        match_id=int(match_id),
        match_version_id=int(match_version_id),
        canonical_player_1_id=int(canonical_p1),
        canonical_player_2_id=int(canonical_p2),
        canonical_player_1_name=str(p1_name),
        canonical_player_2_name=str(p2_name),
        start_delta_minutes=delta,
        provider_orientation="DIRECT" if direct else "REVERSED",
    )


__all__ = [
    "DEFAULT_MAX_START_DELTA_MINUTES",
    "GoalserveCanonicalCandidate",
    "GoalserveEventResolution",
    "GoalserveReconciliationError",
    "SOURCE_SYSTEM",
    "apply_goalserve_identity_bridge",
    "reconcile_goalserve_event",
]
