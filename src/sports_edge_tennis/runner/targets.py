from __future__ import annotations

from datetime import datetime

import psycopg

from sports_edge_tennis.runner.contracts import (
    ResolvedTargetV1,
    TargetResolutionError,
)


def resolve_target(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> ResolvedTargetV1:
    """Resolve the single latest PIT-visible match version for one target.

    PIT rule: source_observed_at <= as_of AND as_of <= as_of. ingested_at is
    never used as a model cutoff.
    """
    row = conn.execute(
        """
        SELECT
            mv.match_id,
            mv.match_version_id,
            mv.player_1_id,
            mv.player_2_id,
            mv.surface::text,
            mv.tour_scope::text,
            mv.competition_tier::text,
            mv.round_code,
            mv.best_of_sets,
            mv.qualifying_flag,
            mv.match_status::text,
            t.canonical_name,
            te.season_year
        FROM match_versions mv
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE mv.match_id = %s
          AND mv.source_observed_at <= %s
          AND mv.as_of <= %s
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        LIMIT 1
        """,
        (match_id, as_of, as_of),
    ).fetchone()

    if row is None:
        raise TargetResolutionError(
            f"match {match_id} has no PIT-visible match_version at as_of={as_of}"
        )

    (
        match_id,
        match_version_id,
        player_1_id,
        player_2_id,
        surface,
        tour_scope,
        competition_tier,
        round_code,
        best_of_sets,
        qualifying_flag,
        match_status,
        canonical_name,
        season_year,
    ) = row

    if match_status != "SCHEDULED":
        raise TargetResolutionError(
            f"match {match_id} latest PIT-visible status is {match_status}, not SCHEDULED"
        )

    from sports_edge_tennis.data.agent2_history import normalize_tournament_alias

    return ResolvedTargetV1(
        match_id=match_id,
        match_version_id=match_version_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        surface=surface,
        tour_scope=tour_scope,
        competition_tier=competition_tier,
        round_code=round_code,
        best_of_sets=int(best_of_sets),
        qualifying_flag=bool(qualifying_flag),
        match_status=match_status,
        canonical_tournament_name=canonical_name,
        normalized_tournament_alias=normalize_tournament_alias(canonical_name),
        season_year=int(season_year),
    )


def resolve_targets(
    conn: psycopg.Connection,
    *,
    match_ids: tuple[int, ...],
    as_of: datetime,
) -> list[ResolvedTargetV1]:
    """Resolve every target in request order, preserving caller ordering."""
    return [resolve_target(conn, match_id=match_id, as_of=as_of) for match_id in match_ids]
