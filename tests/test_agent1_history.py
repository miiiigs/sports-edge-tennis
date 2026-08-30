from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.data.agent1_history import load_agent1_history


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def _insert_player(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO players (canonical_name, status) VALUES (%s, 'ACTIVE') RETURNING player_id",
        (name,),
    ).fetchone()[0]


def _insert_match_shell(
    conn: psycopg.Connection,
    *,
    tournament_name: str,
    season_year: int,
    tour_scope: str,
    competition_tier: str,
    surface: str | None,
    start_date: str,
    end_date: str,
) -> tuple[int, int]:
    tournament_id = conn.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, %s, %s)
        RETURNING tournament_id
        """,
        (tournament_name, tour_scope, competition_tier),
    ).fetchone()[0]
    edition_id = conn.execute(
        """
        INSERT INTO tournament_editions (
            tournament_id,
            season_year,
            edition_name,
            surface,
            start_date,
            end_date
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, season_year, f"{tournament_name} {season_year}", surface, start_date, end_date),
    ).fetchone()[0]
    match_id = conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    return match_id, edition_id


def _insert_match_version(
    conn: psycopg.Connection,
    *,
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    tour_scope: str,
    competition_tier: str,
    surface: str | None,
    best_of_sets: int,
    qualifying_flag: bool,
    match_status: str,
    match_start_at: datetime | None,
    source_observed_at: datetime,
    as_of: datetime,
) -> int:
    return conn.execute(
        """
        INSERT INTO match_versions (
            match_id,
            player_1_id,
            player_2_id,
            match_start_at,
            tour_scope,
            competition_tier,
            surface,
            round_code,
            best_of_sets,
            qualifying_flag,
            match_status,
            source_observed_at,
            as_of
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'R32', %s, %s, %s, %s, %s)
        RETURNING match_version_id
        """,
        (
            match_id,
            player_1_id,
            player_2_id,
            match_start_at,
            tour_scope,
            competition_tier,
            surface,
            best_of_sets,
            qualifying_flag,
            match_status,
            source_observed_at,
            as_of,
        ),
    ).fetchone()[0]


def _insert_result(
    conn: psycopg.Connection,
    *,
    match_id: int,
    outcome_status: str,
    winner_side: int | None,
    source_observed_at: datetime,
    as_of: datetime,
) -> int:
    return conn.execute(
        """
        INSERT INTO match_result_observations (
            match_id,
            outcome_status,
            winner_side,
            retired_side,
            sets_won_player_1,
            sets_won_player_2,
            games_won_player_1,
            games_won_player_2,
            scoreline_text,
            source_observed_at,
            as_of
        )
        VALUES (%s, %s, %s, NULL, NULL, NULL, NULL, NULL, NULL, %s, %s)
        RETURNING match_result_observation_id
        """,
        (match_id, outcome_status, winner_side, source_observed_at, as_of),
    ).fetchone()[0]


def test_load_agent1_history_returns_completed_retired_and_walkover_rows(db: psycopg.Connection):
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    player_3 = _insert_player(db, "Player Three")
    player_4 = _insert_player(db, "Player Four")
    player_5 = _insert_player(db, "Player Five")
    player_6 = _insert_player(db, "Player Six")

    completed_match_id, _ = _insert_match_shell(
        db,
        tournament_name="Completed Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        surface="HARD",
        start_date="2025-01-01",
        end_date="2025-01-07",
    )
    retired_match_id, _ = _insert_match_shell(
        db,
        tournament_name="Retired Open",
        season_year=2025,
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        surface=None,
        start_date="2025-02-01",
        end_date="2025-02-07",
    )
    walkover_match_id, _ = _insert_match_shell(
        db,
        tournament_name="Walkover Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="CLAY",
        start_date="2025-03-01",
        end_date="2025-03-07",
    )

    _insert_match_version(
        db,
        match_id=completed_match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        surface="HARD",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=None,
        source_observed_at=_utc(2025, 1, 5, 0, 0),
        as_of=_utc(2025, 1, 5, 0, 0),
    )
    _insert_result(
        db,
        match_id=completed_match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 1, 5, 23, 59, 59),
        as_of=_utc(2025, 1, 5, 23, 59, 59),
    )
    _insert_match_version(
        db,
        match_id=retired_match_id,
        player_1_id=player_3,
        player_2_id=player_4,
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        surface=None,
        best_of_sets=3,
        qualifying_flag=True,
        match_status="RETIRED",
        match_start_at=None,
        source_observed_at=_utc(2025, 2, 4, 0, 0),
        as_of=_utc(2025, 2, 4, 0, 0),
    )
    _insert_result(
        db,
        match_id=retired_match_id,
        outcome_status="RETIRED",
        winner_side=2,
        source_observed_at=_utc(2025, 2, 4, 23, 59, 59),
        as_of=_utc(2025, 2, 4, 23, 59, 59),
    )
    _insert_match_version(
        db,
        match_id=walkover_match_id,
        player_1_id=player_5,
        player_2_id=player_6,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="CLAY",
        best_of_sets=5,
        qualifying_flag=False,
        match_status="WALKOVER",
        match_start_at=None,
        source_observed_at=_utc(2025, 3, 2, 0, 0),
        as_of=_utc(2025, 3, 2, 0, 0),
    )
    _insert_result(
        db,
        match_id=walkover_match_id,
        outcome_status="WALKOVER",
        winner_side=1,
        source_observed_at=_utc(2025, 3, 2, 23, 59, 59),
        as_of=_utc(2025, 3, 2, 23, 59, 59),
    )

    history = load_agent1_history(db, cutoff=_utc(2025, 12, 31, 23, 59, 59))

    assert [row.match_id for row in history] == [completed_match_id, retired_match_id, walkover_match_id]
    assert history[0].surface == "HARD"
    assert history[1].surface is None
    assert history[1].tour_scope == "CHALLENGER"
    assert history[1].competition_tier == "CHALLENGER"
    assert history[1].qualifying_flag is True
    assert history[1].outcome_status == "RETIRED"
    assert history[2].outcome_status == "WALKOVER"


def test_load_agent1_history_applies_cutoff_and_chooses_latest_eligible_match_version(
    db: psycopg.Connection,
):
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Versioned Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        surface="HARD",
        start_date="2025-04-01",
        end_date="2025-04-07",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        surface="HARD",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="SCHEDULED",
        match_start_at=None,
        source_observed_at=_utc(2025, 4, 3, 0, 0),
        as_of=_utc(2025, 4, 3, 0, 0),
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_500",
        surface="CLAY",
        best_of_sets=5,
        qualifying_flag=True,
        match_status="COMPLETED",
        match_start_at=None,
        source_observed_at=_utc(2025, 4, 4, 12, 0),
        as_of=_utc(2025, 4, 4, 12, 0),
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="GRASS",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=None,
        source_observed_at=_utc(2025, 4, 6, 12, 0),
        as_of=_utc(2025, 4, 6, 12, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 4, 5, 23, 59, 59),
        as_of=_utc(2025, 4, 5, 23, 59, 59),
    )

    before_result = load_agent1_history(db, cutoff=_utc(2025, 4, 5, 12, 0))
    after_result = load_agent1_history(db, cutoff=_utc(2025, 4, 5, 23, 59, 59))

    assert before_result == []
    assert len(after_result) == 1
    observation = after_result[0]
    assert observation.competition_tier == "ATP_500"
    assert observation.surface == "CLAY"
    assert observation.best_of_sets == 5
    assert observation.qualifying_flag is True


def test_load_agent1_history_filters_and_ordering_are_deterministic(db: psycopg.Connection):
    player_ids = [_insert_player(db, f"Player {idx}") for idx in range(1, 5)]
    match_a_id, _ = _insert_match_shell(
        db,
        tournament_name="Alpha Open",
        season_year=2024,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2024-05-01",
        end_date="2024-05-07",
    )
    match_b_id, _ = _insert_match_shell(
        db,
        tournament_name="Beta Challenger",
        season_year=2025,
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        surface="CLAY",
        start_date="2025-06-01",
        end_date="2025-06-07",
    )
    _insert_match_version(
        db,
        match_id=match_b_id,
        player_1_id=player_ids[2],
        player_2_id=player_ids[3],
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        surface="CLAY",
        best_of_sets=3,
        qualifying_flag=True,
        match_status="COMPLETED",
        match_start_at=None,
        source_observed_at=_utc(2025, 6, 4, 0, 0),
        as_of=_utc(2025, 6, 4, 0, 0),
    )
    _insert_result(
        db,
        match_id=match_b_id,
        outcome_status="COMPLETED",
        winner_side=2,
        source_observed_at=_utc(2025, 6, 4, 23, 59, 59),
        as_of=_utc(2025, 6, 4, 23, 59, 59),
    )
    _insert_match_version(
        db,
        match_id=match_a_id,
        player_1_id=player_ids[0],
        player_2_id=player_ids[1],
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        match_start_at=None,
        source_observed_at=_utc(2024, 5, 3, 0, 0),
        as_of=_utc(2024, 5, 3, 0, 0),
    )
    _insert_result(
        db,
        match_id=match_a_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2024, 5, 3, 23, 59, 59),
        as_of=_utc(2024, 5, 3, 23, 59, 59),
    )

    before_counts = (
        db.execute("SELECT count(*) FROM match_versions").fetchone()[0],
        db.execute("SELECT count(*) FROM match_result_observations").fetchone()[0],
    )
    filtered = load_agent1_history(
        db,
        cutoff=_utc(2025, 12, 31, 23, 59, 59),
        tour_scopes=("CHALLENGER",),
        competition_tiers=("CHALLENGER",),
        year_start=2025,
        year_end=2025,
    )
    full_history = load_agent1_history(db, cutoff=_utc(2025, 12, 31, 23, 59, 59))
    after_counts = (
        db.execute("SELECT count(*) FROM match_versions").fetchone()[0],
        db.execute("SELECT count(*) FROM match_result_observations").fetchone()[0],
    )

    assert [row.match_id for row in filtered] == [match_b_id]
    assert [row.match_id for row in full_history] == [match_a_id, match_b_id]
    assert after_counts == before_counts
