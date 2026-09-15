from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sports_edge_tennis.data.agent2_history import (
    Agent2HistoricalMatch,
    Agent2MatchStatRow,
    replay_historical_predictions,
)
from sports_edge_tennis.validation.agent2_recency import (
    replay_historical_predictions_windowed,
    score_agent2_replay,
)


def _match(match_id: int, day: int) -> Agent2HistoricalMatch:
    start = datetime(2025, 1, 1, 12, tzinfo=UTC) + timedelta(days=day)
    observed = start + timedelta(hours=3)
    p1_stat = Agent2MatchStatRow(
        match_stat_observation_id=match_id * 10 + 1,
        player_id=1,
        source_system="TEST",
        source_observed_at=observed,
        as_of=observed,
        first_serve_points_won=40,
        second_serve_points_won=15,
        service_points_played=80,
        return_points_won=30,
    )
    p2_stat = Agent2MatchStatRow(
        match_stat_observation_id=match_id * 10 + 2,
        player_id=2,
        source_system="TEST",
        source_observed_at=observed,
        as_of=observed,
        first_serve_points_won=34,
        second_serve_points_won=14,
        service_points_played=80,
        return_points_won=25,
    )
    return Agent2HistoricalMatch(
        match_id=match_id,
        player_1_id=1,
        player_2_id=2,
        match_start_at=start,
        surface="HARD",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        tournament_id=1,
        canonical_tournament_name="Test Challenger",
        normalized_tournament_alias="TEST CHALLENGER",
        season_year=2025,
        outcome_status="COMPLETED",
        winner_side=1,
        result_source_observed_at=observed,
        result_as_of=observed,
        version_source_observed_at=observed,
        version_as_of=observed,
        stat_rows=(p1_stat, p2_stat),
    )


def test_long_window_matches_cumulative_replay_when_nothing_expires():
    observations = [_match(index + 1, index) for index in range(7)]
    baseline = replay_historical_predictions(observations)
    windowed = replay_historical_predictions_windowed(observations, window_days=365)

    assert len(windowed.records) == len(baseline.records)
    for expected, actual in zip(baseline.records, windowed.records, strict=True):
        assert actual.result.prediction_status == expected.result.prediction_status
        assert actual.result.selection_side == expected.result.selection_side
        assert actual.result.win_probability == expected.result.win_probability


def test_short_window_can_expose_recency_coverage_cost_without_changing_production():
    observations = [_match(index + 1, index) for index in range(7)]
    baseline = replay_historical_predictions(observations)
    short = replay_historical_predictions_windowed(observations, window_days=2)

    assert baseline.records[-1].result.prediction_status == "PREDICTED"
    assert short.records[-1].result.prediction_status == "ABSTAINED"

    metrics = score_agent2_replay(short, window_days=2, evaluation_year_start=2025)
    assert metrics.sample == 7
    assert metrics.window_days == 2
    assert metrics.abstained > 0
