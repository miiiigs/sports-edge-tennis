from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import psycopg

from sports_edge_tennis.live.sportradar_benchmark import deterministic_name_keys
from sports_edge_tennis.live.sportradar_fixtures import SportradarFixtureCandidate

TERMINAL_MATCH_STATUSES = ("COMPLETED", "RETIRED", "WALKOVER", "CANCELLED")

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
    "TERMINAL_TRUTH_CONFLICT",
    "TOURNAMENT_EVIDENCE_CONFLICT",
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
    match_status: str | None = None
    existing_source_match_ids: tuple[str, ...] = ()
    terminal_outcome_status: str | None = None
    terminal_winner_side: int | None = None
    tournament_id: int | None = None
    tournament_source_ids: tuple[str, ...] = ()
    season_year: int | None = None
    edition_surface: str | None = None


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
    if fixture.match_status in TERMINAL_MATCH_STATUSES:
        terminal_resolution = _resolve_terminal_overlap(
            conn,
            fixture=fixture,
            p1=p1,
            p2=p2,
            window=window,
        )
        if terminal_resolution is not None:
            return terminal_resolution

    return SportradarEventResolution(
        "NEW_CANONICAL_EVENT",
        None,
        p1,
        p2,
    )


TERMINAL_CANDIDATE_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (mv.match_id)
        mv.match_id,
        mv.player_1_id,
        mv.player_2_id,
        mv.match_start_at,
        mv.match_status::text AS match_status,
        mv.source_observed_at,
        mv.as_of
    FROM match_versions mv
    WHERE mv.source_observed_at <= %(received_at)s
      AND mv.as_of <= %(received_at)s
    ORDER BY
        mv.match_id,
        mv.as_of DESC,
        mv.source_observed_at DESC,
        mv.match_version_id DESC
),
candidate AS (
    SELECT
        latest.match_id,
        latest.player_1_id,
        latest.player_2_id,
        latest.match_start_at,
        latest.match_status
    FROM latest
    WHERE latest.match_start_at IS NOT NULL
      AND latest.match_start_at BETWEEN %(window_start)s AND %(window_end)s
      AND latest.match_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
      AND (
          (latest.player_1_id = %(player_1_id)s AND latest.player_2_id = %(player_2_id)s)
          OR
          (latest.player_1_id = %(player_2_id)s AND latest.player_2_id = %(player_1_id)s)
      )
),
source_ids AS (
    SELECT
        msi.match_id,
        array_agg(msi.source_match_id ORDER BY msi.source_match_id) AS event_ids
    FROM match_source_ids msi
    WHERE msi.source_system = 'SPORTRADAR'
      AND msi.source_match_id IS NOT NULL
    GROUP BY msi.match_id
),
result AS (
    SELECT DISTINCT ON (mro.match_id)
        mro.match_id,
        mro.outcome_status::text AS outcome_status,
        mro.winner_side
    FROM match_result_observations mro
    WHERE mro.source_observed_at <= %(received_at)s
      AND mro.as_of <= %(received_at)s
    ORDER BY
        mro.match_id,
        mro.as_of DESC,
        mro.source_observed_at DESC,
        mro.match_result_observation_id DESC
),
tournament AS (
    SELECT
        m.match_id,
        te.tournament_id,
        te.season_year,
        te.surface::text AS edition_surface,
        array_agg(tsr.source_tournament_id ORDER BY tsr.source_tournament_id)
            FILTER (WHERE tsr.source_tournament_id IS NOT NULL)
            AS tournament_source_ids
    FROM matches m
    JOIN tournament_editions te
      ON te.tournament_edition_id = m.tournament_edition_id
    LEFT JOIN tournament_source_ids tsr
      ON tsr.tournament_id = te.tournament_id
     AND tsr.source_system = 'SPORTRADAR'
    GROUP BY m.match_id, te.tournament_id, te.season_year, te.surface
)
SELECT
    candidate.match_id,
    candidate.player_1_id,
    candidate.player_2_id,
    candidate.match_start_at,
    candidate.match_status,
    COALESCE(source_ids.event_ids, ARRAY[]::text[]),
    result.outcome_status,
    result.winner_side,
    tournament.tournament_id,
    COALESCE(tournament.tournament_source_ids, ARRAY[]::text[]),
    tournament.season_year,
    tournament.edition_surface
FROM candidate
LEFT JOIN source_ids
  ON source_ids.match_id = candidate.match_id
LEFT JOIN result
  ON result.match_id = candidate.match_id
LEFT JOIN tournament
  ON tournament.match_id = candidate.match_id
ORDER BY candidate.match_start_at, candidate.match_id
"""


def _resolve_terminal_overlap(
    conn: psycopg.Connection,
    *,
    fixture: SportradarFixtureCandidate,
    p1: ExistingPlayerResolution,
    p2: ExistingPlayerResolution,
    window: timedelta,
) -> SportradarEventResolution | None:
    """Link a terminal provider event to an existing terminal canonical match.

    Returns ``None`` when there is no deterministic terminal candidate, letting
    the caller fall back to ``NEW_CANONICAL_EVENT``. Any non-unique or
    contradictory candidate set fails closed instead of creating a duplicate.
    """
    rows = conn.execute(
        TERMINAL_CANDIDATE_SQL,
        {
            "received_at": fixture.received_at,
            "window_start": fixture.match_start_at - window,
            "window_end": fixture.match_start_at + window,
            "player_1_id": p1.player_id,
            "player_2_id": p2.player_id,
        },
    ).fetchall()
    if not rows:
        return None

    candidates = tuple(
        _terminal_candidate(
            row,
            fixture=fixture,
            p1_id=p1.player_id,
            p2_id=p2.player_id,
        )
        for row in rows
    )
    if len(candidates) > 1:
        return SportradarEventResolution(
            "AMBIGUOUS_EXISTING_MATCH",
            None,
            p1,
            p2,
            candidates,
        )

    candidate = candidates[0]
    if candidate.existing_source_match_ids:
        return SportradarEventResolution(
            "SOURCE_ID_CONFLICT",
            None,
            p1,
            p2,
            candidates,
        )
    if _tournament_evidence_conflict(candidate, fixture) is not None:
        return SportradarEventResolution(
            "TOURNAMENT_EVIDENCE_CONFLICT",
            None,
            p1,
            p2,
            candidates,
        )
    if _terminal_truth_conflict(candidate, fixture) is not None:
        return SportradarEventResolution(
            "TERMINAL_TRUTH_CONFLICT",
            None,
            p1,
            p2,
            candidates,
        )
    return SportradarEventResolution(
        "EXACT_EXISTING_MATCH",
        candidate.match_id,
        p1,
        p2,
        candidates,
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


def _terminal_candidate(
    row,
    *,
    fixture: SportradarFixtureCandidate,
    p1_id: int,
    p2_id: int,
) -> ExistingMatchCandidate:
    (
        match_id,
        canonical_p1,
        canonical_p2,
        start_at,
        match_status,
        event_ids,
        outcome_status,
        winner_side,
        tournament_id,
        tournament_source_ids,
        season_year,
        edition_surface,
    ) = row
    direct = int(canonical_p1) == p1_id and int(canonical_p2) == p2_id
    reverse = int(canonical_p1) == p2_id and int(canonical_p2) == p1_id
    if direct == reverse:
        raise RuntimeError("terminal candidate player orientation is invalid")
    delta = int(abs((start_at - fixture.match_start_at).total_seconds()) // 60)
    return ExistingMatchCandidate(
        match_id=int(match_id),
        player_1_id=int(canonical_p1),
        player_2_id=int(canonical_p2),
        match_start_at=start_at,
        start_delta_minutes=delta,
        orientation="DIRECT" if direct else "REVERSED",
        match_status=str(match_status) if match_status is not None else None,
        existing_source_match_ids=tuple(
            str(value) for value in (event_ids or ())
        ),
        terminal_outcome_status=(
            str(outcome_status) if outcome_status is not None else None
        ),
        terminal_winner_side=(
            int(winner_side) if winner_side is not None else None
        ),
        tournament_id=int(tournament_id) if tournament_id is not None else None,
        tournament_source_ids=tuple(
            str(value) for value in (tournament_source_ids or ())
        ),
        season_year=int(season_year) if season_year is not None else None,
        edition_surface=(
            str(edition_surface) if edition_surface is not None else None
        ),
    )


def _tournament_evidence_conflict(
    candidate: ExistingMatchCandidate,
    fixture: SportradarFixtureCandidate,
) -> str | None:
    source_ids = candidate.tournament_source_ids
    if len(source_ids) > 1:
        return "tournament_source_id_ambiguous"
    if source_ids and source_ids[0] != fixture.competition_source_id:
        return "tournament_source_id_mismatch"
    if (
        candidate.season_year is not None
        and candidate.season_year != fixture.match_start_at.year
    ):
        return "edition_season_year_mismatch"
    if (
        candidate.edition_surface is not None
        and fixture.surface is not None
        and candidate.edition_surface != fixture.surface
    ):
        return "edition_surface_mismatch"
    return None


def _terminal_truth_conflict(
    candidate: ExistingMatchCandidate,
    fixture: SportradarFixtureCandidate,
) -> str | None:
    if candidate.terminal_outcome_status is None:
        return None
    if candidate.terminal_outcome_status != fixture.match_status:
        return (
            "terminal_outcome_conflict:"
            f"{candidate.terminal_outcome_status}:{fixture.match_status}"
        )
    incoming_winner = _incoming_winner_canonical_side(candidate, fixture)
    if (
        fixture.match_status in {"COMPLETED", "RETIRED", "WALKOVER"}
        and incoming_winner is None
    ):
        return "terminal_winner_unresolved"
    if candidate.terminal_winner_side != incoming_winner:
        return (
            "terminal_winner_conflict:"
            f"{candidate.terminal_winner_side}:{incoming_winner}"
        )
    return None


def _incoming_winner_canonical_side(
    candidate: ExistingMatchCandidate,
    fixture: SportradarFixtureCandidate,
) -> int | None:
    winner_source_id = _terminal_winner_source_id(fixture)
    if winner_source_id is None:
        return None
    if winner_source_id == fixture.player_1_source_id:
        side = 1
    elif winner_source_id == fixture.player_2_source_id:
        side = 2
    else:
        return None
    if candidate.orientation == "DIRECT":
        return side
    return 2 if side == 1 else 1


def _terminal_winner_source_id(
    fixture: SportradarFixtureCandidate,
) -> str | None:
    summary = fixture.raw_summary
    if not isinstance(summary, Mapping):
        return None
    status = summary.get("sport_event_status")
    if not isinstance(status, Mapping):
        return None
    winner = str(status.get("winner_id") or "").strip()
    return winner or None


__all__ = [
    "TERMINAL_MATCH_STATUSES",
    "ExistingMatchCandidate",
    "ExistingPlayerResolution",
    "SportradarEventResolution",
    "resolve_existing_player",
    "resolve_sportradar_event_overlap",
]
