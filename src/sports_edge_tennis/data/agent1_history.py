from __future__ import annotations

from datetime import date, datetime

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import HistoricalMatchObservation


def load_agent1_history(
    conn: psycopg.Connection,
    *,
    cutoff: datetime,
    tour_scopes: tuple[str, ...] | list[str] | None = None,
    competition_tiers: tuple[str, ...] | list[str] | None = None,
    year_start: int | None = None,
    year_end: int | None = None,
    date_start: date | None = None,
    date_end: date | None = None,
) -> list[HistoricalMatchObservation]:
    result_filters = [
        "mro.source_observed_at <= %(cutoff)s",
        "mro.as_of <= %(cutoff)s",
    ]
    version_filters = [
        "mv.source_observed_at <= %(cutoff)s",
        "mv.as_of <= %(cutoff)s",
        "mv.source_observed_at <= selected_results.as_of",
        "mv.as_of <= selected_results.as_of",
    ]
    params: dict[str, object] = {"cutoff": cutoff}

    if tour_scopes:
        version_filters.append("mv.tour_scope::text = ANY(%(tour_scopes)s)")
        params["tour_scopes"] = list(tour_scopes)
    if competition_tiers:
        version_filters.append("mv.competition_tier::text = ANY(%(competition_tiers)s)")
        params["competition_tiers"] = list(competition_tiers)
    if year_start is not None:
        version_filters.append("te.season_year >= %(year_start)s")
        params["year_start"] = year_start
    if year_end is not None:
        version_filters.append("te.season_year <= %(year_end)s")
        params["year_end"] = year_end
    if date_start is not None:
        result_filters.append("mro.source_observed_at::date >= %(date_start)s")
        params["date_start"] = date_start
    if date_end is not None:
        result_filters.append("mro.source_observed_at::date <= %(date_end)s")
        params["date_end"] = date_end

    query = f"""
        WITH selected_results AS (
            SELECT
                mro.match_result_observation_id,
                mro.match_id,
                mro.outcome_status::text AS outcome_status,
                mro.winner_side,
                mro.source_observed_at,
                mro.as_of,
                ROW_NUMBER() OVER (
                    PARTITION BY mro.match_id
                    ORDER BY mro.as_of DESC, mro.source_observed_at DESC, mro.match_result_observation_id DESC
                ) AS result_rank
            FROM match_result_observations mro
            WHERE {" AND ".join(result_filters)}
        ),
        eligible_history AS (
            SELECT
                selected_results.match_result_observation_id,
                selected_results.match_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.surface::text AS surface,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                mv.best_of_sets,
                mv.qualifying_flag,
                mv.match_start_at,
                selected_results.outcome_status,
                selected_results.winner_side,
                selected_results.source_observed_at,
                selected_results.as_of,
                ROW_NUMBER() OVER (
                    PARTITION BY selected_results.match_id
                    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
                ) AS version_rank
            FROM selected_results
            JOIN matches m
                ON m.match_id = selected_results.match_id
            JOIN tournament_editions te
                ON te.tournament_edition_id = m.tournament_edition_id
            JOIN match_versions mv
                ON mv.match_id = selected_results.match_id
            WHERE selected_results.result_rank = 1
                AND {" AND ".join(version_filters)}
        )
        SELECT
            match_id,
            player_1_id,
            player_2_id,
            surface,
            tour_scope,
            competition_tier,
            best_of_sets,
            qualifying_flag,
            match_start_at,
            outcome_status,
            winner_side,
            source_observed_at,
            as_of
        FROM eligible_history
        WHERE version_rank = 1
        ORDER BY as_of, source_observed_at, match_id, player_1_id, player_2_id
    """

    rows = conn.execute(query, params).fetchall()
    return [
        HistoricalMatchObservation(
            match_id=row[0],
            player_1_id=row[1],
            player_2_id=row[2],
            surface=row[3],
            tour_scope=row[4],
            competition_tier=row[5],
            best_of_sets=row[6],
            qualifying_flag=row[7],
            match_start_at=row[8],
            outcome_status=row[9],
            winner_side=row[10],
            source_observed_at=row[11],
            as_of=row[12],
        )
        for row in rows
    ]
