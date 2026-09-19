from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import psycopg

from sports_edge_tennis.live.sportradar_benchmark import deterministic_name_keys
from sports_edge_tennis.live.sportradar_fixtures import SportradarFixtureCandidate


PlayerResolutionStatus = Literal[
    "SOURCE_ID_RESOLVED",
    "UNIQUE_NAME_RESOLVED",
    "NEW_PLAYER",
    "AMBIGUOUS_PLAYER",
    "SOURCE_ID_CONFLICT",
]
EventResolutionStatus = Literal[
    "SOURCE_ID_RESOLVED",
    "EXACT_EXISTING_MATCH",
    "NEW_CANONICAL_EVENT",
    "AMBIGUOUS_EXISTING_MATCH",
    "PLAYER_IDENTITY_UNRESOLVED",
    "SOURCE_ID_CONFLICT",
]


@dataclass(frozen=True)
class ExistingPlayerResolution:
    status: PlayerResolutionStatus
    player_id: int | None
    candidate_player_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ExistingMatchCandidate:
    match_id: int
    player_1_id: int
    player_2_id: int
    match_start_at: object
    start_delta_minutes: int
    orientation: str


@dataclass(frozen=True)
class SportradarEventResolution:
    status: EventResolutionStatus
    match_id: int | None
    player_1: ExistingPlayerResolution
    player_2: ExistingPlayerResolution
    candidates: tuple[ExistingMatchCandidate, ...] = ()


def resolve_existing_player(
    conn: psycopg.Connection,
    *,
    provider_player_id: str,
    provider_name: str,
) -> ExistingPlayerResolution:
    source_rows = conn.execute(
        """
        SELECT player_id
        FROM player_source_ids
        WHERE source_system = 'SPORTRADAR'
          AND source_player_id = %s
        ORDER BY player_source_id
        LIMIT 2
        """,
        (provider_player_id,),
    ).fetchall()
    source_ids = tuple(int(row[0]) for row in source_rows)
    if len(source_ids) == 1:
        return ExistingPlayerResolution("SOURCE_ID_RESOLVED", source_ids[0], source_ids)
    if len(source_ids) > 1:
        return ExistingPlayerResolution("SOURCE_ID_CONFLICT", None, source_ids)

    keys = sorted(deterministic_name_keys(provider_name))
    if not keys:
        return ExistingPlayerResolution("NEW_PLAYER", None, ())
    rows = conn.execute(
        """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.normalized_alias = ANY(%s)
        ORDER BY pa.player_id
        """,
        (keys,),
    ).fetchall()
    ids = tuple(int(row[0]) for row in rows)
    if len(ids) == 1:
        return ExistingPlayerResolution("UNIQUE_NAME_RESOLVED", ids[0], ids)
    if len(ids) > 1:
        return ExistingPlayerResolution("AMBIGUOUS_PLAYER", None, ids)
    return ExistingPlayerResolution("NEW_PLAYER", None, ())


def resolve_sportradar_event_overlap(
    conn: psycopg.Connection,
    *,
    fixture: SportradarFixtureCandidate,
    max_start_delta_minutes: int = 180,
) -> SportradarEventResolution:
    if max_start_delta_minutes < 0:
        raise ValueError("max_start_delta_minutes must be non-negative")

    event_rows = conn.execute(
        """
        SELECT match_id
        FROM match_source_ids
        WHERE source_system = 'SPORTRADAR'
          AND source_match_id = %s
        ORDER BY match_source_id
        LIMIT 2
        """,
        (fixture.source_match_id,),
    ).fetchall()
    event_ids = tuple(int(row[0]) for row in event_rows)

    p1 = resolve_existing_player(
        conn,
        provider_player_id=fixture.player_1_source_id,
        provider_name=fixture.player_1_name,
    )
    p2 = resolve_existing_player(
        conn,
        provider_player_id=fixture.player_2_source_id,
        provider_name=fixture.player_2_name,
    )

    if len(event_ids) == 1:
        return SportradarEventResolution(
            "SOURCE_ID_RESOLVED",
            event_ids[0],
            p1,
            p2,
        )
    if len(event_ids) > 1:
        return SportradarEventResolution(
            "SOURCE_ID_CONFLICT",
            None,
            p1,
            p2,
        )

    if p1.player_id is None or p2.player_id is None:
        if p1.status == "NEW_PLAYER" or p2.status == "NEW_PLAYER":
            return SportradarEventResolution(
                "NEW_CANONICAL_EVENT",
                None,
                p1,
                p2,
            )
        return SportradarEventResolution(
            "PLAYER_IDENTITY_UNRESOLVED",
            None,
            p1,
            p2,
        )

    if p1.player_id == p2.player_id:
        return SportradarEventResolution(
            "PLAYER_IDENTITY_UNRESOLVED",
            None,
            p1,
            p2,
        )

    window = timedelta(minutes=max_start_delta_minutes)
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at,
                mv.match_status::text,
                mv.source_observed_at,
                mv.as_of
            FROM match_versions mv
            WHERE mv.source_observed_at <= %s
              AND mv.as_of <= %s
            ORDER BY
                mv.match_id,
                mv.as_of DESC,
                mv.source_observed_at DESC,
                mv.match_version_id DESC
        )
        SELECT
            match_id,
            player_1_id,
            player_2_id,
            match_start_at
        FROM latest
        WHERE match_start_at IS NOT NULL
          AND match_start_at BETWEEN %s AND %s
          AND match_status IN ('SCHEDULED', 'POSTPONED')
          AND (
              (player_1_id = %s AND player_2_id = %s)
              OR
              (player_1_id = %s AND player_2_id = %s)
          )
        ORDER BY match_start_at, match_id
        """,
        (
            fixture.received_at,
            fixture.received_at,
            fixture.match_start_at - window,
            fixture.match_start_at + window,
            p1.player_id,
            p2.player_id,
            p2.player_id,
            p1.player_id,
        ),
    ).fetchall()

    candidates = tuple(
        _candidate(
            row,
            fixture=fixture,
            p1_id=p1.player_id,
            p2_id=p2.player_id,
        )
        for row in rows
    )
    if len(candidates) == 1:
        return SportradarEventResolution(
            "EXACT_EXISTING_MATCH",
            candidates[0].match_id,
            p1,
            p2,
            candidates,
        )
    if len(candidates) > 1:
        return SportradarEventResolution(
            "AMBIGUOUS_EXISTING_MATCH",
            None,
            p1,
            p2,
            candidates,
        )
    return SportradarEventResolution(
        "NEW_CANONICAL_EVENT",
        None,
        p1,
        p2,
    )


def _candidate(
    row,
    *,
    fixture: SportradarFixtureCandidate,
    p1_id: int,
    p2_id: int,
) -> ExistingMatchCandidate:
    match_id, canonical_p1, canonical_p2, start_at = row
    direct = int(canonical_p1) == p1_id and int(canonical_p2) == p2_id
    reverse = int(canonical_p1) == p2_id and int(canonical_p2) == p1_id
    if direct == reverse:
        raise RuntimeError("candidate player orientation is invalid")
    delta = int(abs((start_at - fixture.match_start_at).total_seconds()) // 60)
    return ExistingMatchCandidate(
        match_id=int(match_id),
        player_1_id=int(canonical_p1),
        player_2_id=int(canonical_p2),
        match_start_at=start_at,
        start_delta_minutes=delta,
        orientation="DIRECT" if direct else "REVERSED",
    )


__all__ = [
    "ExistingMatchCandidate",
    "ExistingPlayerResolution",
    "SportradarEventResolution",
    "resolve_existing_player",
    "resolve_sportradar_event_overlap",
]
