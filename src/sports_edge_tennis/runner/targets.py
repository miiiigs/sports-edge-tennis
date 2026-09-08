from __future__ import annotations

from datetime import datetime

import psycopg

from sports_edge_tennis.data.normalize import normalize_person_lookup
from sports_edge_tennis.runner.contracts import (
    ResolvedTargetV1,
    TargetResolutionError,
)


def _has_historical_result_evidence(
    conn: psycopg.Connection,
    *,
    player_id: int,
    as_of: datetime,
) -> bool:
    return bool(
        conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM match_versions mv
                JOIN match_result_observations mro ON mro.match_id = mv.match_id
                WHERE (mv.player_1_id = %s OR mv.player_2_id = %s)
                  AND mv.source_observed_at <= %s
                  AND mv.as_of <= %s
                  AND mro.source_observed_at <= %s
                  AND mro.as_of <= %s
            )
            """,
            (player_id, player_id, as_of, as_of, as_of, as_of),
        ).fetchone()[0]
    )


def _resolve_model_player_id(
    conn: psycopg.Connection,
    *,
    player_id: int,
    as_of: datetime,
) -> int:
    """Bridge a live-only player row to one unique historical identity.

    API-Tennis has stable provider IDs, while the historical corpus predates
    those IDs. Early live ingestion therefore created separate player rows for
    names already present in historical Tennis data. The canonical match/source
    lineage stays untouched here: this function only resolves the player ID used
    as model input.

    Resolution is deliberately fail-closed. Keep the original player ID when it
    already has PIT-visible history, when no exact normalized historical alias
    exists, or when more than one historical candidate exists.
    """
    if _has_historical_result_evidence(conn, player_id=player_id, as_of=as_of):
        return player_id

    row = conn.execute(
        "SELECT canonical_name FROM players WHERE player_id = %s",
        (player_id,),
    ).fetchone()
    if row is None:
        raise TargetResolutionError(f"player {player_id} does not exist")

    canonical_name = row[0]
    normalized_name = normalize_person_lookup(canonical_name)
    candidates = conn.execute(
        """
        SELECT DISTINCT p.player_id
        FROM players p
        LEFT JOIN player_aliases pa
          ON pa.player_id = p.player_id
         AND pa.source_system = 'TENNIS_DATA'
        WHERE p.player_id <> %s
          AND (
              pa.normalized_alias = %s
              OR lower(btrim(p.canonical_name)) = lower(btrim(%s))
          )
          AND EXISTS (
              SELECT 1
              FROM match_versions mv
              JOIN match_result_observations mro ON mro.match_id = mv.match_id
              WHERE (mv.player_1_id = p.player_id OR mv.player_2_id = p.player_id)
                AND mv.source_observed_at <= %s
                AND mv.as_of <= %s
                AND mro.source_observed_at <= %s
                AND mro.as_of <= %s
          )
        ORDER BY p.player_id
        """,
        (
            player_id,
            normalized_name,
            canonical_name,
            as_of,
            as_of,
            as_of,
            as_of,
        ),
    ).fetchall()

    if len(candidates) == 1:
        return int(candidates[0][0])
    return player_id


def resolve_target(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> ResolvedTargetV1:
    """Resolve the single latest PIT-visible match version for one target.

    PIT rule: source_observed_at <= prediction as_of and version as_of <=
    prediction as_of. ingested_at is never used as a model cutoff.
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

    model_player_1_id = _resolve_model_player_id(conn, player_id=player_1_id, as_of=as_of)
    model_player_2_id = _resolve_model_player_id(conn, player_id=player_2_id, as_of=as_of)
    if model_player_1_id == model_player_2_id:
        raise TargetResolutionError(
            f"match {match_id} historical identity resolution collapsed both sides"
        )

    from sports_edge_tennis.data.agent2_history import normalize_tournament_alias

    return ResolvedTargetV1(
        match_id=match_id,
        match_version_id=match_version_id,
        player_1_id=model_player_1_id,
        player_2_id=model_player_2_id,
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
