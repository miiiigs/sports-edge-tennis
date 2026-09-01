from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.data.agent3_history import (
    Agent3HistoricalMatch,
    build_history_snapshot,
    build_prediction_request_from_snapshot,
    historical_match_is_eligible_for_update,
    load_agent3_history,
    prepare_historical_match_update,
    replay_historical_predictions,
)


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
    return match_id, tournament_id


def _insert_match_version(
    conn: psycopg.Connection,
    *,
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    tour_scope: str,
    competition_tier: str,
    surface: str | None,
    match_status: str,
    match_start_at: datetime | None,
    source_observed_at: datetime,
    as_of: datetime,
    ingested_at: datetime | None = None,
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
            as_of,
            ingested_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'R32', 3, FALSE, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP))
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
            match_status,
            source_observed_at,
            as_of,
            ingested_at,
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
    ingested_at: datetime | None = None,
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
            as_of,
            ingested_at
        )
        VALUES (%s, %s, %s, NULL, NULL, NULL, NULL, NULL, NULL, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP))
        RETURNING match_result_observation_id
        """,
        (match_id, outcome_status, winner_side, source_observed_at, as_of, ingested_at),
    ).fetchone()[0]


def _historical_match(
    *,
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    surface: str | None = "HARD",
    outcome_status: str = "COMPLETED",
    winner_side: int | None = 1,
    result_source_observed_at: datetime | None = None,
    result_as_of: datetime | None = None,
    version_source_observed_at: datetime | None = None,
    version_as_of: datetime | None = None,
) -> Agent3HistoricalMatch:
    result_source_observed_at = result_source_observed_at or _utc(2025, 1, 10, 23, 0)
    result_as_of = result_as_of or result_source_observed_at
    version_source_observed_at = version_source_observed_at or _utc(2025, 1, 10, 8, 0)
    version_as_of = version_as_of or version_source_observed_at
    return Agent3HistoricalMatch(
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        match_start_at=_utc(2025, 1, 10, 9, 0),
        surface=surface,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        outcome_status=outcome_status,
        winner_side=winner_side,
        result_source_observed_at=result_source_observed_at,
        result_as_of=result_as_of,
        version_source_observed_at=version_source_observed_at,
        version_as_of=version_as_of,
    )


def test_load_agent3_history_applies_cutoff_and_chooses_latest_eligible_version(db: psycopg.Connection):
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
        match_status="SCHEDULED",
        match_start_at=_utc(2025, 4, 3, 10, 0),
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
        match_status="COMPLETED",
        match_start_at=_utc(2025, 4, 4, 10, 0),
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
        match_status="COMPLETED",
        match_start_at=_utc(2025, 4, 6, 10, 0),
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

    before_result = load_agent3_history(db, cutoff=_utc(2025, 4, 5, 12, 0))
    after_result = load_agent3_history(db, cutoff=_utc(2025, 4, 5, 23, 59, 59))

    assert before_result == []
    assert len(after_result) == 1
    observation = after_result[0]
    assert observation.competition_tier == "ATP_500"
    assert observation.surface == "CLAY"
    assert observation.match_start_at == _utc(2025, 4, 4, 10, 0)


def test_load_agent3_history_uses_source_observed_at_and_as_of_but_not_ingested_at(
    db: psycopg.Connection,
):
    player_1 = _insert_player(db, "Player One")
    player_2 = _insert_player(db, "Player Two")
    match_id, _ = _insert_match_shell(
        db,
        tournament_name="Visibility Open",
        season_year=2025,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        start_date="2025-01-01",
        end_date="2025-01-07",
    )
    _insert_match_version(
        db,
        match_id=match_id,
        player_1_id=player_1,
        player_2_id=player_2,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        match_status="COMPLETED",
        match_start_at=_utc(2025, 1, 5, 10, 0),
        source_observed_at=_utc(2025, 1, 5, 8, 0),
        as_of=_utc(2025, 1, 5, 8, 0),
        ingested_at=_utc(2026, 8, 31, 12, 0),
    )
    _insert_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        source_observed_at=_utc(2025, 1, 5, 23, 0),
        as_of=_utc(2025, 1, 6, 9, 0),
        ingested_at=_utc(2026, 8, 31, 12, 0),
    )

    before_source = load_agent3_history(db, cutoff=_utc(2025, 1, 5, 22, 59))
    before_as_of = load_agent3_history(db, cutoff=_utc(2025, 1, 6, 8, 59))
    visible = load_agent3_history(db, cutoff=_utc(2025, 1, 6, 9, 0))

    assert before_source == []
    assert before_as_of == []
    assert len(visible) == 1


def test_load_agent3_history_filters_and_ordering_are_deterministic(db: psycopg.Connection):
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
        match_status="COMPLETED",
        match_start_at=_utc(2025, 6, 4, 10, 0),
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
        match_status="COMPLETED",
        match_start_at=_utc(2024, 5, 3, 10, 0),
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

    filtered = load_agent3_history(
        db,
        cutoff=_utc(2025, 12, 31, 23, 59, 59),
        tour_scopes=("CHALLENGER",),
        competition_tiers=("CHALLENGER",),
        year_start=2025,
        year_end=2025,
    )
    full_history = load_agent3_history(db, cutoff=_utc(2025, 12, 31, 23, 59, 59))

    assert [row.match_id for row in filtered] == [match_b_id]
    assert [row.match_id for row in full_history] == [match_a_id, match_b_id]


def test_completed_status_prepares_update_and_other_statuses_do_not():
    completed = _historical_match(match_id=1, player_1_id=1, player_2_id=2, outcome_status="COMPLETED")
    snapshot = build_history_snapshot([], cutoff=_utc(2026, 1, 1))

    assert historical_match_is_eligible_for_update(completed) is True
    assert prepare_historical_match_update(completed, snapshot) is not None

    for status, winner_side in (
        ("RETIRED", 1),
        ("WALKOVER", 1),
        ("CANCELLED", None),
        ("POSTPONED", None),
    ):
        observation = _historical_match(
            match_id=10,
            player_1_id=1,
            player_2_id=2,
            outcome_status=status,
            winner_side=winner_side,
        )
        assert historical_match_is_eligible_for_update(observation) is False
        assert prepare_historical_match_update(observation, snapshot) is None


def test_build_prediction_request_from_snapshot_uses_visibility_cohort():
    observation = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=2,
        result_source_observed_at=_utc(2025, 1, 5, 23, 0),
        result_as_of=_utc(2025, 1, 6, 9, 0),
    )

    request = build_prediction_request_from_snapshot(
        observation,
        build_history_snapshot([], cutoff=_utc(2026, 1, 1)),
    )

    assert request.as_of == _utc(2025, 1, 6, 9, 0)


def test_same_cohort_predictions_use_frozen_pre_cohort_state():
    same_cohort = _utc(2025, 1, 10, 23, 0)
    observations = [
        _historical_match(
            match_id=1,
            player_1_id=1,
            player_2_id=3,
            winner_side=1,
            result_source_observed_at=same_cohort,
            result_as_of=same_cohort,
        ),
        _historical_match(
            match_id=2,
            player_1_id=1,
            player_2_id=2,
            winner_side=1,
            result_source_observed_at=same_cohort,
            result_as_of=same_cohort,
        ),
    ]

    replay = replay_historical_predictions(observations)

    assert replay.records[0].request.history_snapshot.players == {}
    assert replay.records[1].request.history_snapshot.players == {}
    assert replay.records[0].result.abstain_reason == replay.records[1].result.abstain_reason


def test_target_winner_label_does_not_affect_its_own_prediction():
    baseline = _historical_match(
        match_id=1,
        player_1_id=3,
        player_2_id=4,
        winner_side=1,
        result_source_observed_at=_utc(2025, 1, 1, 23, 0),
        result_as_of=_utc(2025, 1, 1, 23, 0),
    )
    target_a = _historical_match(
        match_id=2,
        player_1_id=1,
        player_2_id=2,
        winner_side=1,
        result_source_observed_at=_utc(2025, 2, 1, 23, 0),
        result_as_of=_utc(2025, 2, 1, 23, 0),
    )
    target_b = _historical_match(
        match_id=2,
        player_1_id=1,
        player_2_id=2,
        winner_side=2,
        result_source_observed_at=_utc(2025, 2, 1, 23, 0),
        result_as_of=_utc(2025, 2, 1, 23, 0),
    )

    replay_a = replay_historical_predictions([baseline, target_a])
    replay_b = replay_historical_predictions([baseline, target_b])

    assert replay_a.records[1].request == replay_b.records[1].request
    assert replay_a.records[1].result == replay_b.records[1].result


def test_future_match_does_not_affect_prior_prediction():
    early = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=2,
        result_source_observed_at=_utc(2025, 1, 1, 23, 0),
        result_as_of=_utc(2025, 1, 1, 23, 0),
    )
    late = _historical_match(
        match_id=2,
        player_1_id=1,
        player_2_id=3,
        result_source_observed_at=_utc(2025, 2, 1, 23, 0),
        result_as_of=_utc(2025, 2, 1, 23, 0),
    )

    first_only = replay_historical_predictions([early])
    with_future = replay_historical_predictions([early, late])

    assert first_only.records[0].result == with_future.records[0].result


def test_later_cohort_observes_previous_completed_update():
    early = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=3,
        winner_side=1,
        result_source_observed_at=_utc(2025, 1, 1, 23, 0),
        result_as_of=_utc(2025, 1, 1, 23, 0),
    )
    late = _historical_match(
        match_id=2,
        player_1_id=1,
        player_2_id=2,
        winner_side=1,
        result_source_observed_at=_utc(2025, 2, 1, 23, 0),
        result_as_of=_utc(2025, 2, 1, 23, 0),
    )

    replay = replay_historical_predictions([early, late])

    assert 1 in replay.records[1].request.history_snapshot.players
    assert replay.records[1].request.history_snapshot.players[1].all_observations


def test_build_history_snapshot_respects_source_observed_at_and_as_of_cutoff():
    source_future = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=2,
        result_source_observed_at=_utc(2025, 1, 6, 12, 0),
        result_as_of=_utc(2025, 1, 6, 12, 0),
    )
    as_of_future = _historical_match(
        match_id=2,
        player_1_id=1,
        player_2_id=3,
        result_source_observed_at=_utc(2025, 1, 5, 12, 0),
        result_as_of=_utc(2025, 1, 7, 12, 0),
    )

    snapshot = build_history_snapshot(
        [source_future, as_of_future],
        cutoff=_utc(2025, 1, 6, 11, 59),
    )

    assert snapshot.players == {}
    assert snapshot.pairs == {}


def test_build_history_snapshot_keeps_completed_matches_with_unknown_surface_in_all_bucket():
    snapshot = build_history_snapshot(
        [
            _historical_match(
                match_id=1,
                player_1_id=1,
                player_2_id=2,
                surface=None,
                winner_side=1,
                result_source_observed_at=_utc(2025, 1, 1, 23, 0),
                result_as_of=_utc(2025, 1, 1, 23, 0),
            )
        ],
        cutoff=_utc(2025, 2, 1, 0, 0),
    )

    assert 1 in snapshot.players
    assert snapshot.players[1].surface_observations == {}
    assert snapshot.pairs[(1, 2)].surface_observations == {}


def test_build_history_snapshot_expires_observations_outside_lookback():
    old_match = _historical_match(
        match_id=1,
        player_1_id=1,
        player_2_id=2,
        result_source_observed_at=_utc(2021, 1, 1, 23, 0),
        result_as_of=_utc(2021, 1, 1, 23, 0),
    )
    recent_match = _historical_match(
        match_id=2,
        player_1_id=1,
        player_2_id=3,
        result_source_observed_at=_utc(2025, 12, 31, 23, 0),
        result_as_of=_utc(2025, 12, 31, 23, 0),
    )

    snapshot = build_history_snapshot([old_match, recent_match], cutoff=_utc(2026, 1, 1, 0, 0))

    assert (1, 2) not in snapshot.pairs
    assert (1, 3) in snapshot.pairs


def test_replay_and_snapshot_are_deterministic_across_input_order():
    observations = [
        _historical_match(
            match_id=1,
            player_1_id=1,
            player_2_id=3,
            result_source_observed_at=_utc(2025, 1, 1, 23, 0),
            result_as_of=_utc(2025, 1, 1, 23, 0),
        ),
        _historical_match(
            match_id=2,
            player_1_id=1,
            player_2_id=2,
            result_source_observed_at=_utc(2025, 2, 1, 23, 0),
            result_as_of=_utc(2025, 2, 1, 23, 0),
        ),
    ]

    replay_a = replay_historical_predictions(observations)
    replay_b = replay_historical_predictions(list(reversed(observations)))
    snapshot_a = build_history_snapshot(observations, cutoff=_utc(2026, 1, 1))
    snapshot_b = build_history_snapshot(list(reversed(observations)), cutoff=_utc(2026, 1, 1))

    assert replay_a == replay_b
    assert snapshot_a == snapshot_b
