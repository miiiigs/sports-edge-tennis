from __future__ import annotations

from datetime import UTC, datetime

from sports_edge_tennis.data.agent2_history import (
    Agent2HistoricalMatch,
    Agent2MatchStatRow,
    build_history_snapshot as build_agent2_snapshot,
)
from sports_edge_tennis.data.agent3_history import (
    Agent3HistoricalMatch,
    build_history_snapshot as build_agent3_reference_snapshot,
)
from sports_edge_tennis.runner.agent2_live_snapshot import advance_agent2_history_snapshot
from sports_edge_tennis.runner.agent3_live_snapshot import (
    advance_live_agent3_history_snapshot,
    build_live_agent3_history_snapshot,
)
from sports_edge_tennis.runner.live_model_cache import (
    decode_agent2_snapshot,
    decode_agent3_snapshot,
    encode_agent2_snapshot,
    encode_agent3_snapshot,
)


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _agent3_match(
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    observed_at: datetime,
    *,
    surface: str | None = "HARD",
    winner_side: int = 1,
) -> Agent3HistoricalMatch:
    return Agent3HistoricalMatch(
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        match_start_at=observed_at,
        surface=surface,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        outcome_status="COMPLETED",
        winner_side=winner_side,
        result_source_observed_at=observed_at,
        result_as_of=observed_at,
        version_source_observed_at=observed_at,
        version_as_of=observed_at,
    )


def _agent2_match(
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    observed_at: datetime,
    *,
    surface: str = "HARD",
) -> Agent2HistoricalMatch:
    row_1 = Agent2MatchStatRow(
        match_stat_observation_id=match_id * 10 + 1,
        player_id=player_1_id,
        source_system="TEST",
        source_observed_at=observed_at,
        as_of=observed_at,
        first_serve_points_won=25,
        second_serve_points_won=15,
        service_points_played=65,
        return_points_won=25,
    )
    row_2 = Agent2MatchStatRow(
        match_stat_observation_id=match_id * 10 + 2,
        player_id=player_2_id,
        source_system="TEST",
        source_observed_at=observed_at,
        as_of=observed_at,
        first_serve_points_won=24,
        second_serve_points_won=14,
        service_points_played=64,
        return_points_won=25,
    )
    return Agent2HistoricalMatch(
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        match_start_at=observed_at,
        surface=surface,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        tournament_id=1,
        canonical_tournament_name="Test Open",
        normalized_tournament_alias="TEST OPEN",
        season_year=observed_at.year,
        outcome_status="COMPLETED",
        winner_side=1,
        result_source_observed_at=observed_at,
        result_as_of=observed_at,
        version_source_observed_at=observed_at,
        version_as_of=observed_at,
        stat_rows=(row_1, row_2),
    )


def test_agent3_checkpoint_round_trip_is_exact():
    observations = [
        _agent3_match(1, 1, 2, _utc(2025, 1, 1, 23)),
        _agent3_match(2, 1, 3, _utc(2025, 2, 1, 23), surface="CLAY", winner_side=2),
        _agent3_match(3, 2, 3, _utc(2026, 1, 1, 23)),
    ]
    snapshot = build_live_agent3_history_snapshot(observations, cutoff=_utc(2026, 9, 9, 10))
    assert decode_agent3_snapshot(encode_agent3_snapshot(snapshot)) == snapshot


def test_agent2_checkpoint_round_trip_is_exact():
    observations = [
        _agent2_match(1, 1, 2, _utc(2025, 1, 1, 23)),
        _agent2_match(2, 1, 3, _utc(2025, 2, 1, 23), surface="CLAY"),
    ]
    snapshot = build_agent2_snapshot(observations, cutoff=_utc(2026, 9, 9, 10))
    assert decode_agent2_snapshot(encode_agent2_snapshot(snapshot)) == snapshot


def test_agent3_checkpoint_plus_delta_matches_fresh_reference():
    checkpoint_cutoff = _utc(2026, 1, 1, 0)
    final_cutoff = _utc(2026, 9, 9, 10)
    before = [
        _agent3_match(1, 1, 2, _utc(2024, 1, 1, 23)),
        _agent3_match(2, 1, 3, _utc(2025, 1, 1, 23), surface="CLAY"),
    ]
    delta = [
        _agent3_match(3, 2, 3, _utc(2026, 2, 1, 23)),
        _agent3_match(4, 1, 4, _utc(2026, 3, 1, 23), winner_side=2),
    ]
    checkpoint = build_live_agent3_history_snapshot(before, cutoff=checkpoint_cutoff)
    advanced = advance_live_agent3_history_snapshot(
        checkpoint,
        delta,
        previous_cutoff=checkpoint_cutoff,
        cutoff=final_cutoff,
    )
    reference = build_agent3_reference_snapshot(before + delta, cutoff=final_cutoff)
    assert advanced == reference


def test_agent2_checkpoint_plus_delta_matches_fresh_snapshot():
    checkpoint_cutoff = _utc(2026, 1, 1, 0)
    final_cutoff = _utc(2026, 9, 9, 10)
    before = [
        _agent2_match(1, 1, 2, _utc(2025, 1, 1, 23)),
        _agent2_match(2, 1, 3, _utc(2025, 2, 1, 23), surface="CLAY"),
    ]
    delta = [
        _agent2_match(3, 2, 3, _utc(2026, 2, 1, 23)),
        _agent2_match(4, 1, 4, _utc(2026, 3, 1, 23), surface="CLAY"),
    ]
    checkpoint = build_agent2_snapshot(before, cutoff=checkpoint_cutoff)
    advanced = advance_agent2_history_snapshot(checkpoint, delta, cutoff=final_cutoff)
    reference = build_agent2_snapshot(before + delta, cutoff=final_cutoff)
    assert advanced == reference


def test_agent3_two_lookback_bootstrap_matches_full_reference_for_final_graph():
    cutoff = _utc(2026, 9, 9, 10)
    observations = [
        # Too old to affect any residual that can survive into the final 4-year graph.
        _agent3_match(1, 1, 2, _utc(2017, 1, 1, 23)),
        _agent3_match(2, 1, 3, _utc(2019, 1, 1, 23)),
        _agent3_match(3, 1, 4, _utc(2021, 1, 1, 23)),
        _agent3_match(4, 1, 5, _utc(2023, 1, 1, 23), surface="CLAY"),
        _agent3_match(5, 2, 5, _utc(2025, 1, 1, 23)),
    ]
    full = build_agent3_reference_snapshot(observations, cutoff=cutoff)
    bounded = [item for item in observations if item.visibility_cohort >= _utc(2018, 9, 7, 0)]
    optimized = build_live_agent3_history_snapshot(bounded, cutoff=cutoff)
    assert optimized == full
