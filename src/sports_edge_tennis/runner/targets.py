from __future__ import annotations

from datetime import datetime

import psycopg

from sports_edge_tennis.data.normalize import normalize_person_lookup, tennis_data_aliases
from sports_edge_tennis.runner.contracts import (
    ResolvedTargetV1,
    TargetResolutionError,
)


def _has_non_api_source_identity(conn: psycopg.Connection, player_id: int) -> bool:
    """Return True when this canonical player already belongs to historical identity truth."""
    return (
        conn.execute(
            """
            SELECT 1
            FROM player_source_ids
            WHERE player_id = %s
              AND source_system <> 'API_TENNIS'
            LIMIT 1
            """,
            (player_id,),
        ).fetchone()
        is not None
    )


def _resolve_model_player_id(
    conn: psycopg.Connection,
    *,
    player_id: int,
    as_of: datetime,
) -> int:
    """Resolve a live-only API-Tennis player to one unambiguous historical identity.

    API-Tennis stable player IDs are authoritative for live fixture identity, but the
    restored historical corpus uses its own source IDs. Earlier live ingestion created
    a new canonical player when an API-Tennis ID was first seen, which disconnected the
    frozen predictive agents from that player's historical observations.

    This bridge is model-input-only: it does not rewrite append-only source mappings or
    live match identity. It only substitutes a historical player ID when the normalized
    TENNIS_DATA aliases resolve to exactly one non-API player with PIT-visible completed
    history. Zero or multiple candidates fail closed by retaining the live player ID.
    """
    if _has_non_api_source_identity(conn, player_id):
        return player_id

    row = conn.execute(
        "SELECT canonical_name FROM players WHERE player_id = %s",
        (player_id,),
    ).fetchone()
    if row is None:
        return player_id

    normalized_aliases = sorted(
        {
            normalize_person_lookup(alias)
            for alias in tennis_data_aliases(row[0])
            if alias and normalize_person_lookup(alias)
        }
    )
    if not normalized_aliases:
        return player_id

    candidates = conn.execute(
        """
        SELECT DISTINCT pa.player_id
        FROM player_aliases pa
        WHERE pa.source_system = 'TENNIS_DATA'
          AND pa.normalized_alias = ANY(%s)
          AND pa.player_id <> %s
          AND EXISTS (
              SELECT 1
              FROM player_source_ids psi
              WHERE psi.player_id = pa.player_id
                AND psi.source_system <> 'API_TENNIS'
          )
          AND EXISTS (
              SELECT 1
              FROM match_versions hist_mv
              WHERE (hist_mv.player_1_id = pa.player_id OR hist_mv.player_2_id = pa.player_id)
                AND hist_mv.match_status = 'COMPLETED'
                AND hist_mv.match_start_at < %s
                AND hist_mv.source_observed_at <= %s
                AND hist_mv.as_of <= %s
          )
        ORDER BY pa.player_id
        """,
        (normalized_aliases, player_id, as_of, as_of, as_of),
    ).fetchall()

    if len(candidates) != 1:
        return player_id
    return int(candidates[0][0])


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

    model_player_1_id = _resolve_model_player_id(conn, player_id=player_1_id, as_of=as_of)
    model_player_2_id = _resolve_model_player_id(conn, player_id=player_2_id, as_of=as_of)
    if model_player_1_id == model_player_2_id:
        # Ambiguous/colliding bridges must never collapse a match to one player.
        model_player_1_id = player_1_id
        model_player_2_id = player_2_id

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
