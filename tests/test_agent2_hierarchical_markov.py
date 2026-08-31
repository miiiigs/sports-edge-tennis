from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sports_edge_tennis.agents import agent2_hierarchical_markov
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    AGENT_CODE,
    AbstainReason,
    Agent2Config,
    Agent2ValidationError,
    PopulationFallbackLevel,
    PlayerServeReturnState,
    PopulationPriorState,
    PredictionRequest,
    ScoringRuleVersion,
    deuce_probability,
    game_probability,
    match_probability,
    matchup_serve_probability,
    predict,
    resolve_population_prior,
    set_transition_distribution,
    shrink_player_state,
    tiebreak_probability,
    tiebreak_server_for_points_played,
)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 31, hour, minute, tzinfo=UTC)


def _player_state(
    *,
    player_id: int,
    prior_usable_matches: int = 8,
    overall_serve_points_won: int = 330,
    overall_serve_points_played: int = 500,
    overall_return_points_won: int = 170,
    overall_return_points_played: int = 500,
    surface_serve_points_won: int = 140,
    surface_serve_points_played: int = 210,
    surface_return_points_won: int = 70,
    surface_return_points_played: int = 210,
) -> PlayerServeReturnState:
    return PlayerServeReturnState(
        player_id=player_id,
        prior_usable_matches=prior_usable_matches,
        overall_serve_points_won=overall_serve_points_won,
        overall_serve_points_played=overall_serve_points_played,
        overall_return_points_won=overall_return_points_won,
        overall_return_points_played=overall_return_points_played,
        surface_serve_points_won=surface_serve_points_won,
        surface_serve_points_played=surface_serve_points_played,
        surface_return_points_won=surface_return_points_won,
        surface_return_points_played=surface_return_points_played,
    )


def _population_state(
    *,
    surface_tour: tuple[int, int] = (620, 1000),
    surface_total: tuple[int, int] = (990, 1600),
    global_total: tuple[int, int] = (1850, 3000),
) -> PopulationPriorState:
    return PopulationPriorState(
        surface_tour_service_points_won=surface_tour[0],
        surface_tour_service_points_played=surface_tour[1],
        surface_service_points_won=surface_total[0],
        surface_service_points_played=surface_total[1],
        global_service_points_won=global_total[0],
        global_service_points_played=global_total[1],
    )


def _request(
    *,
    player_1_state: PlayerServeReturnState | None = None,
    player_2_state: PlayerServeReturnState | None = None,
    population_prior_state: PopulationPriorState | None = None,
    surface: str | None = "HARD",
    tour_scope: str = "ATP_MAIN",
    competition_tier: str = "ATP_TOUR",
    best_of_sets: int = 3,
    scoring_rule_version: ScoringRuleVersion | None = ScoringRuleVersion.BO3_TB7_ALL_SETS,
    resolver_abstain_reason: AbstainReason | None = None,
) -> PredictionRequest:
    return PredictionRequest(
        player_1_id=1,
        player_2_id=2,
        surface=surface,
        tour_scope=tour_scope,
        competition_tier=competition_tier,
        best_of_sets=best_of_sets,
        scoring_rule_version=scoring_rule_version,
        player_1_state=player_1_state or _player_state(player_id=1),
        player_2_state=player_2_state or _player_state(
            player_id=2,
            overall_serve_points_won=320,
            overall_serve_points_played=510,
            overall_return_points_won=180,
            overall_return_points_played=510,
            surface_serve_points_won=132,
            surface_serve_points_played=215,
            surface_return_points_won=78,
            surface_return_points_played=215,
        ),
        population_prior_state=population_prior_state or _population_state(),
        as_of=_utc(12),
        resolver_abstain_reason=resolver_abstain_reason,
    )


def test_default_config_payload_records_locked_v1_identity_and_hyperparameters():
    payload = Agent2Config().to_payload()

    assert payload["k_serve_overall"] == "142.64921751742673"
    assert payload["k_serve_surface"] == "207.4613530571349"
    assert payload["k_return_overall"] == "348.87619155831686"
    assert payload["k_return_surface"] == "193.2713757201798"
    assert payload["minimum_prior_usable_matches"] == 5
    assert payload["minimum_prior_service_points"] == 250
    assert payload["minimum_prior_return_points"] == 250
    assert payload["model_version"] == "agent2_hierarchical_markov_v1"
    assert payload["feature_version"] == "agent2_serve_return_inputs_v1"
    assert payload["config_version"] == "agent2_hierarchical_markov_config_v1"
    assert payload["parameter_schema_version"] == "agent2_hierarchical_markov_params_v1"


def test_locked_config_rejects_parameter_tuning():
    with pytest.raises(Agent2ValidationError):
        Agent2Config(k_serve_overall=Decimal("143"))
    with pytest.raises(Agent2ValidationError):
        Agent2Config(minimum_prior_usable_matches=6)
    with pytest.raises(Agent2ValidationError):
        Agent2Config(probability_decimal_places=5)


def test_population_prior_uses_surface_tour_then_surface_then_global_fallback():
    surface_tour = resolve_population_prior(_population_state())
    surface_only = resolve_population_prior(
        _population_state(surface_tour=(0, 0), surface_total=(500, 800), global_total=(900, 1500))
    )
    global_only = resolve_population_prior(
        _population_state(surface_tour=(0, 0), surface_total=(0, 0), global_total=(900, 1500))
    )

    assert surface_tour == (
        Decimal("0.62"),
        Decimal("0.38"),
        PopulationFallbackLevel.SURFACE_TOUR,
    )
    assert surface_only == (
        Decimal("0.625"),
        Decimal("0.375"),
        PopulationFallbackLevel.SURFACE,
    )
    assert global_only == (
        Decimal("0.6"),
        Decimal("0.4"),
        PopulationFallbackLevel.GLOBAL,
    )
    assert resolve_population_prior(
        _population_state(surface_tour=(0, 0), surface_total=(0, 0), global_total=(0, 0))
    ) is None


def test_carpet_uses_same_population_fallback_chain():
    request = _request(surface="CARPET")
    result = predict(request)

    assert result.prediction_status == "PREDICTED"
    assert result.diagnostics.population_fallback_level == PopulationFallbackLevel.SURFACE_TOUR


def test_shrinkage_formulas_match_hand_calculated_decimal_values():
    player = _player_state(
        player_id=1,
        overall_serve_points_won=60,
        overall_serve_points_played=100,
        overall_return_points_won=35,
        overall_return_points_played=100,
        surface_serve_points_won=12,
        surface_serve_points_played=20,
        surface_return_points_won=8,
        surface_return_points_played=20,
    )
    posterior = shrink_player_state(
        player_state=player,
        mu_serve_context=Decimal("0.62"),
        mu_return_context=Decimal("0.38"),
    )

    assert posterior.serve_overall.quantize(Decimal("0.0000001")) == Decimal("0.6117576")
    assert posterior.serve_surface.quantize(Decimal("0.0000001")) == Decimal("0.6107238")
    assert posterior.return_overall.quantize(Decimal("0.0000001")) == Decimal("0.3733166")
    assert posterior.return_surface.quantize(Decimal("0.0000001")) == Decimal("0.3758189")
    assert Decimal("0") < posterior.serve_overall < Decimal("1")
    assert Decimal("0") < posterior.serve_surface < Decimal("1")
    assert Decimal("0") < posterior.return_overall < Decimal("1")
    assert Decimal("0") < posterior.return_surface < Decimal("1")


def test_zero_surface_counts_fall_back_toward_overall_without_epsilon_clipping():
    player = _player_state(
        player_id=1,
        overall_serve_points_won=0,
        overall_serve_points_played=0,
        overall_return_points_won=0,
        overall_return_points_played=0,
        surface_serve_points_won=0,
        surface_serve_points_played=0,
        surface_return_points_won=0,
        surface_return_points_played=0,
    )
    posterior = shrink_player_state(
        player_state=player,
        mu_serve_context=Decimal("0.61"),
        mu_return_context=Decimal("0.39"),
    )

    assert posterior.serve_overall == Decimal("0.61")
    assert posterior.serve_surface == Decimal("0.61")
    assert posterior.return_overall == Decimal("0.39")
    assert posterior.return_surface == Decimal("0.39")


def test_matchup_transform_obeys_neutral_identity():
    probability = matchup_serve_probability(
        server_serve_probability=Decimal("0.62"),
        returner_return_probability=Decimal("0.38"),
        context_serve_probability=Decimal("0.62"),
    )

    assert probability == Decimal("0.62")


def test_matchup_transform_is_player_swap_consistent():
    p_1_serve = matchup_serve_probability(
        server_serve_probability=Decimal("0.66"),
        returner_return_probability=Decimal("0.36"),
        context_serve_probability=Decimal("0.62"),
    )
    p_2_serve = matchup_serve_probability(
        server_serve_probability=Decimal("0.64"),
        returner_return_probability=Decimal("0.34"),
        context_serve_probability=Decimal("0.62"),
    )
    swapped = matchup_serve_probability(
        server_serve_probability=Decimal("0.64"),
        returner_return_probability=Decimal("0.34"),
        context_serve_probability=Decimal("0.62"),
    )

    assert p_2_serve == swapped
    assert Decimal("0") < p_1_serve < Decimal("1")
    assert Decimal("0") < p_2_serve < Decimal("1")


def test_game_formula_matches_boundary_identities_and_monotonicity():
    values = [
        game_probability(Decimal("0.20")),
        game_probability(Decimal("0.40")),
        game_probability(Decimal("0.60")),
        game_probability(Decimal("0.80")),
    ]

    assert game_probability(Decimal("0")) == Decimal("0")
    assert game_probability(Decimal("1")) == Decimal("1")
    assert game_probability(Decimal("0.5")) == Decimal("0.5")
    assert values == sorted(values)


def test_deuce_formula_matches_standard_closed_form():
    assert deuce_probability(Decimal("0")) == Decimal("0")
    assert deuce_probability(Decimal("1")) == Decimal("1")
    assert deuce_probability(Decimal("0.5")) == Decimal("0.5")
    assert deuce_probability(Decimal("0.6")).quantize(Decimal("0.000000")) == Decimal("0.692308")


@pytest.mark.parametrize(
    ("points_played", "expected_server"),
    [
        (0, 1),
        (1, 2),
        (2, 2),
        (3, 1),
        (4, 1),
        (5, 2),
        (6, 2),
        (7, 1),
    ],
)
def test_tiebreak_serve_sequence_uses_locked_one_two_two_pattern(
    points_played: int,
    expected_server: int,
):
    assert tiebreak_server_for_points_played(first_server=1, points_played=points_played) == expected_server


@pytest.mark.parametrize("point_target", [7, 10])
def test_tiebreak_probability_respects_symmetry_player_swap_and_first_server_swap(point_target: int):
    neutral = tiebreak_probability(
        player_1_serve_probability=Decimal("0.5"),
        player_2_serve_probability=Decimal("0.5"),
        point_target=point_target,
        first_server=1,
    )
    stronger = tiebreak_probability(
        player_1_serve_probability=Decimal("0.68"),
        player_2_serve_probability=Decimal("0.60"),
        point_target=point_target,
        first_server=1,
    )
    swapped = tiebreak_probability(
        player_1_serve_probability=Decimal("0.60"),
        player_2_serve_probability=Decimal("0.68"),
        point_target=point_target,
        first_server=2,
    )

    assert neutral == Decimal("0.5")
    assert stronger + swapped == Decimal("1")


@pytest.mark.parametrize("point_target", [7, 10])
def test_tiebreak_tail_absorption_is_exact_and_not_truncated(point_target: int):
    probability = tiebreak_probability(
        player_1_serve_probability=Decimal("0.999"),
        player_2_serve_probability=Decimal("0.001"),
        point_target=point_target,
        first_server=1,
    )

    assert probability > Decimal("0.999")
    assert probability < Decimal("1")


@pytest.mark.parametrize(
    "scoring_rule",
    [
        ScoringRuleVersion.BO3_TB7_ALL_SETS,
        ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6,
        ScoringRuleVersion.BO5_DECIDER_ADVANTAGE,
        ScoringRuleVersion.BO5_DECIDER_TB7_AT_6_6,
        ScoringRuleVersion.BO5_DECIDER_TB7_AT_12_12,
        ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6,
    ],
)
def test_set_engine_returns_joint_winner_next_server_distribution_for_all_supported_rules(
    scoring_rule: ScoringRuleVersion,
):
    distribution = set_transition_distribution(
        player_1_serve_probability=Decimal("0.64"),
        player_2_serve_probability=Decimal("0.62"),
        scoring_rule_version=scoring_rule,
        is_deciding_set=True,
        next_server=1,
    )

    assert sum(distribution.values(), Decimal("0")) == Decimal("1")
    assert set(distribution) <= {(1, 1), (1, 2), (2, 1), (2, 2)}


def test_set_engine_tiebreak_path_preserves_next_set_server_rule():
    distribution = set_transition_distribution(
        player_1_serve_probability=Decimal("0.999"),
        player_2_serve_probability=Decimal("0.999"),
        scoring_rule_version=ScoringRuleVersion.BO5_DECIDER_TB7_AT_12_12,
        is_deciding_set=True,
        next_server=1,
    )

    assert distribution[(1, 2)] + distribution[(2, 2)] > Decimal("0.999")


def test_match_engine_supports_bo3_bo5_and_first_server_marginalization():
    bo3_a_first = match_probability(
        player_1_serve_probability=Decimal("0.64"),
        player_2_serve_probability=Decimal("0.60"),
        scoring_rule_version=ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6,
        first_server=1,
    )
    bo3_b_first = match_probability(
        player_1_serve_probability=Decimal("0.64"),
        player_2_serve_probability=Decimal("0.60"),
        scoring_rule_version=ScoringRuleVersion.BO3_DECIDER_TB10_AT_6_6,
        first_server=2,
    )
    bo5 = match_probability(
        player_1_serve_probability=Decimal("0.64"),
        player_2_serve_probability=Decimal("0.60"),
        scoring_rule_version=ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6,
        first_server=1,
    )

    assert Decimal("0") < bo3_a_first < Decimal("1")
    assert Decimal("0") < bo3_b_first < Decimal("1")
    assert Decimal("0") < bo5 < Decimal("1")
    assert bo5 > bo3_b_first


def test_predict_returns_quantized_complement_and_side_one_on_exact_tie():
    state = _player_state(
        player_id=1,
        overall_serve_points_won=300,
        overall_serve_points_played=500,
        overall_return_points_won=200,
        overall_return_points_played=500,
        surface_serve_points_won=120,
        surface_serve_points_played=200,
        surface_return_points_won=80,
        surface_return_points_played=200,
    )
    result = predict(
        _request(
            player_1_state=state,
            player_2_state=_player_state(player_id=2, overall_serve_points_won=300, overall_serve_points_played=500, overall_return_points_won=200, overall_return_points_played=500, surface_serve_points_won=120, surface_serve_points_played=200, surface_return_points_won=80, surface_return_points_played=200),
        )
    )

    assert result.prediction_status == "PREDICTED"
    assert result.selection_side == 1
    assert result.player_1_probability == Decimal("0.500000")
    assert result.player_2_probability == Decimal("0.500000")
    assert result.win_probability == Decimal("0.500000")


def test_near_half_probability_is_still_predicted():
    result = predict(
        _request(
            player_1_state=_player_state(player_id=1, overall_serve_points_won=301),
            player_2_state=_player_state(player_id=2, overall_serve_points_won=300),
        )
    )

    assert result.prediction_status == "PREDICTED"
    assert result.win_probability is not None
    assert result.diagnostics.abstain_reason is None


@pytest.mark.parametrize(
    ("prediction_request", "expected_reason"),
    [
        (_request(surface=None), AbstainReason.UNKNOWN_SURFACE),
        (_request(scoring_rule_version=None), AbstainReason.UNSUPPORTED_SCORING),
        (
            _request(
                scoring_rule_version=None,
                resolver_abstain_reason=AbstainReason.UNSUPPORTED_OR_UNKNOWN,
            ),
            AbstainReason.UNSUPPORTED_OR_UNKNOWN,
        ),
        (
            _request(player_1_state=_player_state(player_id=1, prior_usable_matches=4)),
            AbstainReason.INSUFFICIENT_HISTORY,
        ),
        (
            _request(
                player_1_state=_player_state(
                    player_id=1,
                    overall_serve_points_won=160,
                    overall_serve_points_played=249,
                )
            ),
            AbstainReason.INSUFFICIENT_POINT_HISTORY,
        ),
        (
            _request(
                population_prior_state=_population_state(
                    surface_tour=(0, 0), surface_total=(0, 0), global_total=(0, 0)
                )
            ),
            AbstainReason.PIT_STATE_UNRESOLVED,
        ),
    ],
)
def test_predict_abstains_only_for_locked_input_or_rule_insufficiency(
    prediction_request: PredictionRequest,
    expected_reason: AbstainReason,
):
    result = predict(prediction_request)

    assert result.prediction_status == "ABSTAINED"
    assert result.selection_side is None
    assert result.win_probability is None
    assert result.player_1_probability is None
    assert result.player_2_probability is None
    assert result.diagnostics.abstain_reason == expected_reason


def test_prediction_output_carries_locked_identity_and_diagnostics():
    result = predict(_request())

    assert result.agent_code == AGENT_CODE
    assert result.model_version == "agent2_hierarchical_markov_v1"
    assert result.feature_version == "agent2_serve_return_inputs_v1"
    assert result.config_version == "agent2_hierarchical_markov_config_v1"
    assert result.parameter_schema_version == "agent2_hierarchical_markov_params_v1"
    assert result.diagnostics.scoring_rule_version == ScoringRuleVersion.BO3_TB7_ALL_SETS
    assert result.diagnostics.best_of_sets == 3
    assert result.diagnostics.first_server_marginalized is True
    assert result.player_1_probability + result.player_2_probability == Decimal("1.000000")


def test_compute_module_remains_blind_to_other_agents_and_market_inputs():
    request_fields = set(PredictionRequest.__dataclass_fields__)
    forbidden_fields = {
        "agent1",
        "agent3",
        "manager",
        "odds",
        "bookmaker",
        "market",
        "price",
        "edge",
        "clv",
        "settlement",
    }
    assert request_fields.isdisjoint(forbidden_fields)

    module_source = inspect.getsource(agent2_hierarchical_markov)
    imported_modules = {
        alias.name
        for node in ast.walk(ast.parse(module_source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    banned_fragments = ("agent1", "agent3", "manager", "market", "odds", "bookmaker")
    assert all(
        fragment not in imported_module
        for imported_module in imported_modules
        for fragment in banned_fragments
    )


def test_invalid_player_state_fails_closed():
    with pytest.raises(Agent2ValidationError):
        _player_state(
            player_id=1,
            overall_serve_points_won=501,
            overall_serve_points_played=500,
        )
