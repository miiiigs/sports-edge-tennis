from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sports_edge_tennis.agents import agent1_surface_elo
from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG,
    HistoricalMatchObservation,
    PlayerRatingState,
    PredictionRequest,
    SurfaceEloConfig,
    SurfaceEloValidationError,
    apply_historical_observation,
    dynamic_k,
    expected_score,
    predict,
    replay_history,
    surface_weight,
    update_rating_pair,
)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 30, hour, minute, tzinfo=UTC)


def _observation(
    *,
    match_id: int,
    player_1_id: int = 1,
    player_2_id: int = 2,
    surface: str | None = "HARD",
    tour_scope: str = "ATP_MAIN",
    competition_tier: str = "ATP_TOUR",
    best_of_sets: int = 3,
    qualifying_flag: bool = False,
    match_start_at: datetime | None = None,
    outcome_status: str = "COMPLETED",
    winner_side: int | None = 1,
    source_observed_at: datetime | None = None,
    as_of: datetime | None = None,
) -> HistoricalMatchObservation:
    source_time = source_observed_at or _utc(10)
    observation_time = as_of or source_time
    return HistoricalMatchObservation(
        match_id=match_id,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        surface=surface,
        tour_scope=tour_scope,
        competition_tier=competition_tier,
        best_of_sets=best_of_sets,
        qualifying_flag=qualifying_flag,
        match_start_at=match_start_at,
        outcome_status=outcome_status,
        winner_side=winner_side,
        source_observed_at=source_time,
        as_of=observation_time,
    )


def _request(
    *,
    player_1_id: int = 1,
    player_2_id: int = 2,
    surface: str | None = "HARD",
    tour_scope: str = "ATP_MAIN",
    competition_tier: str = "ATP_TOUR",
    best_of_sets: int = 3,
    qualifying_flag: bool = False,
    as_of: datetime | None = None,
) -> PredictionRequest:
    return PredictionRequest(
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        surface=surface,
        tour_scope=tour_scope,
        competition_tier=competition_tier,
        best_of_sets=best_of_sets,
        qualifying_flag=qualifying_flag,
        as_of=as_of or _utc(12),
    )


def test_default_config_payload_records_replay_relevant_v1_parameters():
    payload = DEFAULT_CONFIG.to_payload()

    assert payload["model_version"] == "agent1_surface_elo_v1"
    assert payload["feature_version"] == "agent1_surface_elo_inputs_v1"
    assert payload["config_version"] == "agent1_surface_elo_config_v1"
    assert payload["parameter_schema_version"] == "agent1_surface_elo_params_v1"
    assert payload["carpet_policy"] == "EXPLICIT_SURFACE_BOOK"
    assert payload["unknown_surface_policy"] == "OVERALL_ONLY"
    assert payload["retired_policy"] == "SKIP"
    assert payload["walkover_policy"] == "SKIP"
    assert payload["cancelled_policy"] == "SKIP"
    assert payload["postponed_policy"] == "SKIP"
    assert payload["qualifying_policy"] == "INCLUDE"
    assert payload["tour_pool_policy"] == "SHARED"
    assert payload["tie_selection_policy"] == "PLAYER_1"


def test_equal_ratings_produce_side_one_tie_with_exact_half_output():
    result = predict(_request(surface="HARD"))

    assert result.selection_side == 1
    assert result.win_probability == Decimal("0.500000")
    assert result.player_1_probability == Decimal("0.500000")
    assert result.player_2_probability == Decimal("0.500000")
    assert result.diagnostics.tie is True
    assert result.diagnostics.unknown_surface is False


def test_stronger_rating_produces_probability_above_half_and_within_bounds():
    history = [
        _observation(match_id=1, surface="HARD", winner_side=1),
        _observation(match_id=2, surface="HARD", winner_side=1, source_observed_at=_utc(10, 1)),
    ]

    result = predict(_request(surface="HARD", as_of=_utc(12)), history=history)

    assert result.selection_side == 1
    assert Decimal("0") <= result.player_1_probability <= Decimal("1")
    assert Decimal("0") <= result.player_2_probability <= Decimal("1")
    assert result.player_1_probability > Decimal("0.500000")
    assert result.player_2_probability < Decimal("0.500000")


def test_expected_score_is_symmetric():
    score_ab = expected_score(Decimal("1635.25"), Decimal("1512.75"))
    score_ba = expected_score(Decimal("1512.75"), Decimal("1635.25"))

    assert score_ab + score_ba == Decimal("1")


def test_dynamic_k_decreases_with_experience():
    assert dynamic_k(0) > dynamic_k(5) > dynamic_k(25)


def test_hand_calculated_1500_vs_1500_update_matches_decimal_policy():
    k = dynamic_k(0)
    new_a, new_b = update_rating_pair(
        Decimal("1500"),
        Decimal("1500"),
        0,
        0,
        1,
    )

    assert expected_score(Decimal("1500"), Decimal("1500")) == Decimal("0.5")
    assert k.quantize(Decimal("0.000001")) == Decimal("131.326390")
    assert new_a.quantize(Decimal("0.000001")) == Decimal("1565.663195")
    assert new_b.quantize(Decimal("0.000001")) == Decimal("1434.336805")


def test_rating_updates_use_pre_match_ratings_for_both_players():
    rating_a = Decimal("1600")
    rating_b = Decimal("1400")
    expected_a = expected_score(rating_a, rating_b)
    expected_b = Decimal("1") - expected_a
    k_a = dynamic_k(0)
    k_b = dynamic_k(10)

    new_a, new_b = update_rating_pair(rating_a, rating_b, 0, 10, 2)

    assert new_a == rating_a + k_a * (Decimal("0") - expected_a)
    assert new_b == rating_b + k_b * (Decimal("1") - expected_b)


def test_completed_known_surface_updates_overall_and_only_that_surface_book():
    updated = apply_historical_observation(
        {},
        _observation(match_id=1, surface="CLAY", winner_side=1),
    )

    player_1 = updated[1]
    player_2 = updated[2]
    assert player_1.overall_match_count == 1
    assert player_2.overall_match_count == 1
    assert player_1.surface_match_counts["CLAY"] == 1
    assert player_2.surface_match_counts["CLAY"] == 1
    assert "HARD" not in player_1.surface_ratings
    assert "GRASS" not in player_2.surface_ratings
    assert player_1.overall_rating > Decimal("1500")
    assert player_2.overall_rating < Decimal("1500")


def test_unknown_surface_updates_overall_only_and_marks_diagnostic():
    history = [_observation(match_id=1, surface=None, winner_side=1)]

    replay = replay_history(history, prediction_as_of=_utc(12))
    result = predict(_request(surface=None), history=history)

    assert replay.player_states[1].overall_match_count == 1
    assert replay.player_states[1].surface_match_counts == {}
    assert replay.player_states[1].surface_ratings == {}
    assert result.diagnostics.unknown_surface is True
    assert result.diagnostics.player_1_surface_matches == 0
    assert result.diagnostics.player_1_surface_weight == Decimal("0")


def test_surface_weight_shrinkage_profile_is_capped_at_half():
    assert surface_weight(0) == Decimal("0")
    assert surface_weight(5) == Decimal("0.25")
    assert surface_weight(10) == Decimal("0.5")
    assert surface_weight(25) == Decimal("0.5")


def test_player_specific_surface_histories_change_surface_probability():
    history = [
        _observation(match_id=1, surface="HARD", winner_side=1, source_observed_at=_utc(9, 0)),
        _observation(match_id=2, surface="HARD", winner_side=1, source_observed_at=_utc(9, 1)),
        _observation(match_id=3, surface="HARD", winner_side=1, source_observed_at=_utc(9, 2)),
        _observation(match_id=4, surface="HARD", winner_side=1, source_observed_at=_utc(9, 3)),
        _observation(match_id=5, surface="HARD", winner_side=1, source_observed_at=_utc(9, 4)),
        _observation(match_id=6, surface="CLAY", winner_side=2, source_observed_at=_utc(9, 5)),
        _observation(match_id=7, surface="CLAY", winner_side=2, source_observed_at=_utc(9, 6)),
        _observation(match_id=8, surface="CLAY", winner_side=2, source_observed_at=_utc(9, 7)),
        _observation(match_id=9, surface="CLAY", winner_side=2, source_observed_at=_utc(9, 8)),
        _observation(match_id=10, surface="CLAY", winner_side=2, source_observed_at=_utc(9, 9)),
    ]

    hard_result = predict(_request(surface="HARD"), history=history)
    clay_result = predict(_request(surface="CLAY"), history=history)
    carpet_result = predict(_request(surface="CARPET"), history=history)
    overall_only_result = predict(_request(surface=None), history=history)

    assert hard_result.player_1_probability > clay_result.player_1_probability
    assert carpet_result.player_1_probability == overall_only_result.player_1_probability
    assert carpet_result.diagnostics.player_1_surface_weight == Decimal("0")


def test_carpet_uses_normal_surface_book_mechanics_when_carpet_history_exists():
    history = [
        _observation(match_id=1, surface="CARPET", winner_side=1, source_observed_at=_utc(9, 0)),
        _observation(match_id=2, surface="CARPET", winner_side=1, source_observed_at=_utc(9, 1)),
        _observation(match_id=3, surface="HARD", winner_side=2, source_observed_at=_utc(9, 2)),
        _observation(match_id=4, surface="HARD", winner_side=2, source_observed_at=_utc(9, 3)),
    ]

    carpet_result = predict(_request(surface="CARPET"), history=history)
    overall_only_result = predict(_request(surface=None), history=history)

    assert carpet_result.player_1_probability > overall_only_result.player_1_probability
    assert carpet_result.diagnostics.player_1_surface_matches == 2
    assert carpet_result.diagnostics.player_1_surface_weight == Decimal("0.10")


def test_cold_start_and_surface_cold_start_diagnostics_are_explicit():
    history = [_observation(match_id=1, player_1_id=3, player_2_id=4, surface="CLAY", winner_side=1)]

    result = predict(_request(player_1_id=1, player_2_id=2, surface="HARD"), history=history)

    assert result.player_1_probability == Decimal("0.500000")
    assert result.player_2_probability == Decimal("0.500000")
    assert result.diagnostics.player_1_cold_start is True
    assert result.diagnostics.player_2_cold_start is True
    assert result.diagnostics.player_1_surface_cold_start is True
    assert result.diagnostics.player_2_surface_cold_start is True


def test_non_completed_statuses_do_not_change_state_and_are_counted_in_diagnostics():
    history = [
        _observation(match_id=1, outcome_status="RETIRED", winner_side=1, source_observed_at=_utc(9, 0)),
        _observation(match_id=2, outcome_status="WALKOVER", winner_side=1, source_observed_at=_utc(9, 1)),
        _observation(match_id=3, outcome_status="CANCELLED", winner_side=None, source_observed_at=_utc(9, 2)),
        _observation(match_id=4, outcome_status="POSTPONED", winner_side=None, source_observed_at=_utc(9, 3)),
    ]

    replay = replay_history(history, prediction_as_of=_utc(12))
    result = predict(_request(), history=history)

    assert replay.player_states == {}
    assert replay.eligible_history_match_count == 0
    assert result.diagnostics.skipped_retired_match_count == 1
    assert result.diagnostics.skipped_walkover_match_count == 1
    assert result.diagnostics.skipped_cancelled_match_count == 1
    assert result.diagnostics.skipped_postponed_match_count == 1


def test_future_observations_are_skipped_by_source_observed_at_and_as_of():
    history = [
        _observation(match_id=1, source_observed_at=_utc(9, 0), as_of=_utc(9, 0)),
        _observation(match_id=2, source_observed_at=_utc(13, 0), as_of=_utc(13, 0)),
        _observation(match_id=3, source_observed_at=_utc(9, 30), as_of=_utc(13, 1)),
    ]

    replay = replay_history(history, prediction_as_of=_utc(12))

    assert replay.eligible_history_match_count == 1
    assert replay.skipped_future_observation_count == 2
    assert replay.player_states[1].overall_match_count == 1


def test_history_replay_is_deterministic_across_input_order_and_tie_breaks():
    observations = [
        _observation(match_id=20, winner_side=1, source_observed_at=_utc(9, 1)),
        _observation(match_id=10, winner_side=2, source_observed_at=_utc(9, 1)),
        _observation(match_id=30, winner_side=1, source_observed_at=_utc(9, 2)),
    ]

    replay_a = replay_history(observations, prediction_as_of=_utc(12))
    replay_b = replay_history(list(reversed(observations)), prediction_as_of=_utc(12))
    prediction_a = predict(_request(), history=observations)
    prediction_b = predict(_request(), history=list(reversed(observations)))

    assert replay_a.player_states == replay_b.player_states
    assert prediction_a == prediction_b


def test_atp_and_challenger_share_one_pool_and_qualifying_is_included_without_weighting():
    history = [
        _observation(
            match_id=1,
            tour_scope="CHALLENGER",
            competition_tier="CHALLENGER",
            qualifying_flag=True,
            surface="HARD",
            winner_side=1,
        )
    ]

    result = predict(
        _request(
            tour_scope="ATP_MAIN",
            competition_tier="ATP_250",
            qualifying_flag=False,
            surface="HARD",
        ),
        history=history,
    )

    assert result.player_1_probability > Decimal("0.500000")
    assert result.diagnostics.player_1_overall_matches == 1
    assert result.diagnostics.player_1_surface_matches == 1


def test_best_of_sets_does_not_change_v1_probability():
    history = [_observation(match_id=1, surface="HARD", winner_side=1)]

    best_of_three = predict(_request(best_of_sets=3), history=history)
    best_of_five = predict(_request(best_of_sets=5), history=history)

    assert best_of_three.player_1_probability == best_of_five.player_1_probability
    assert best_of_three.win_probability == best_of_five.win_probability


def test_invalid_inputs_fail_closed():
    with pytest.raises(SurfaceEloValidationError):
        PredictionRequest(
            player_1_id=1,
            player_2_id=1,
            surface="HARD",
            tour_scope="ATP_MAIN",
            competition_tier="ATP_TOUR",
            best_of_sets=3,
            qualifying_flag=False,
            as_of=_utc(12),
        )
    with pytest.raises(SurfaceEloValidationError):
        HistoricalMatchObservation(
            match_id=1,
            player_1_id=1,
            player_2_id=2,
            surface="INDOOR",
            tour_scope="ATP_MAIN",
            competition_tier="ATP_TOUR",
            best_of_sets=3,
            qualifying_flag=False,
            match_start_at=None,
            outcome_status="COMPLETED",
            winner_side=1,
            source_observed_at=_utc(10),
            as_of=_utc(10),
        )
    with pytest.raises(SurfaceEloValidationError):
        PlayerRatingState(player_id=1, overall_rating=Decimal("1500"), overall_match_count=-1)
    with pytest.raises(SurfaceEloValidationError):
        SurfaceEloConfig(probability_scale=Decimal("0"))
    with pytest.raises(SurfaceEloValidationError):
        SurfaceEloConfig(k_scale=Decimal("0"))


def test_input_contracts_exclude_market_fields_and_module_imports_are_market_blind():
    history_fields = set(HistoricalMatchObservation.__dataclass_fields__)
    request_fields = set(PredictionRequest.__dataclass_fields__)
    forbidden_fields = {
        "odds",
        "price",
        "prices",
        "bookmaker",
        "market",
        "consensus",
        "clv",
        "veto",
    }

    assert history_fields.isdisjoint(forbidden_fields)
    assert request_fields.isdisjoint(forbidden_fields)

    module_source = inspect.getsource(agent1_surface_elo)
    imported_modules = {
        alias.name
        for node in ast.walk(ast.parse(module_source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    banned_import_fragments = ("market", "manager", "veto", "odds", "price")

    assert all(
        fragment not in imported_module
        for imported_module in imported_modules
        for fragment in banned_import_fragments
    )
