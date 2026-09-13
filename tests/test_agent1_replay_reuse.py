from datetime import datetime, timezone

from sports_edge_tennis.agents.agent1_surface_elo import (
    HistoricalMatchObservation,
    PredictionRequest,
    predict,
    predict_from_replay,
    replay_history,
)


def _utc(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, tzinfo=timezone.utc)


def _observation(
    *,
    match_id: int,
    player_1_id: int,
    player_2_id: int,
    winner_side: int,
    day: int,
    surface: str = "HARD",
) -> HistoricalMatchObservation:
    observed_at = _utc(day)
    return HistoricalMatchObservation(
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        surface=surface,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        best_of_sets=3,
        qualifying_flag=False,
        match_start_at=observed_at,
        outcome_status="COMPLETED",
        winner_side=winner_side,
        source_observed_at=observed_at,
        as_of=observed_at,
    )


def _request(player_1_id: int, player_2_id: int) -> PredictionRequest:
    return PredictionRequest(
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        surface="HARD",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        best_of_sets=3,
        qualifying_flag=False,
        as_of=_utc(10),
    )


def test_predict_from_replay_is_exactly_equivalent_to_regular_predict_for_shared_as_of() -> None:
    history = [
        _observation(match_id=1, player_1_id=10, player_2_id=20, winner_side=1, day=1),
        _observation(match_id=2, player_1_id=20, player_2_id=30, winner_side=2, day=2),
        _observation(match_id=3, player_1_id=10, player_2_id=30, winner_side=2, day=3),
        _observation(match_id=4, player_1_id=40, player_2_id=10, winner_side=1, day=4),
    ]
    replay = replay_history(history, _utc(10))

    for request in (_request(10, 20), _request(20, 30), _request(40, 10)):
        assert predict_from_replay(request, replay) == predict(request, history=history)


def test_replay_reuse_preserves_cold_start_and_history_diagnostics() -> None:
    history = [
        _observation(match_id=1, player_1_id=10, player_2_id=20, winner_side=1, day=1),
    ]
    request = _request(10, 999)
    replay = replay_history(history, request.as_of)

    result = predict_from_replay(request, replay)

    assert result.diagnostics.player_1_overall_matches == 1
    assert result.diagnostics.player_2_overall_matches == 0
    assert result.diagnostics.player_2_cold_start is True
    assert result.diagnostics.eligible_history_match_count == 1
