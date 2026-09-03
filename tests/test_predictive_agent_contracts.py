from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sports_edge_tennis.agents.adapters import (
    adapt_agent1_result,
    adapt_agent2_result,
    adapt_agent3_result,
)
from sports_edge_tennis.agents.agent1_surface_elo import (
    PredictionRequest as Agent1PredictionRequest,
    predict as predict_agent1,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    AbstainReason as Agent2AbstainReason,
    PlayerServeReturnState,
    PopulationPriorState,
    PredictionRequest as Agent2PredictionRequest,
    ScoringRuleVersion,
    predict as predict_agent2,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    Agent3HistorySnapshot,
    PairHistorySnapshot,
    PredictionRequest as Agent3PredictionRequest,
    ResidualObservation,
    predict as predict_agent3,
)
from sports_edge_tennis.agents.contracts import (
    AGENT1_RAW_PROBABILITY_V1,
    AGENT2_RAW_PROBABILITY_V1,
    AGENT3_RAW_PROBABILITY_V1,
    PredictiveAgentContractError,
    PredictiveAgentResultV1,
    probability_to_db_string,
)


AS_OF = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _agent1_request() -> Agent1PredictionRequest:
    return Agent1PredictionRequest(
        player_1_id=1,
        player_2_id=2,
        surface="HARD",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        best_of_sets=3,
        qualifying_flag=False,
        as_of=AS_OF,
    )


def _agent2_state(*, player_id: int, serve_won: int) -> PlayerServeReturnState:
    return PlayerServeReturnState(
        player_id=player_id,
        prior_usable_matches=8,
        overall_serve_points_won=serve_won,
        overall_serve_points_played=500,
        overall_return_points_won=180,
        overall_return_points_played=500,
        surface_serve_points_won=serve_won - 180,
        surface_serve_points_played=220,
        surface_return_points_won=80,
        surface_return_points_played=220,
    )


def _agent2_request(*, surface: str | None = "HARD") -> Agent2PredictionRequest:
    return Agent2PredictionRequest(
        player_1_id=1,
        player_2_id=2,
        surface=surface,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        best_of_sets=3,
        scoring_rule_version=ScoringRuleVersion.BO3_TB7_ALL_SETS,
        player_1_state=_agent2_state(player_id=1, serve_won=335),
        player_2_state=_agent2_state(player_id=2, serve_won=310),
        population_prior_state=PopulationPriorState(
            surface_tour_service_points_won=620,
            surface_tour_service_points_played=1000,
            surface_service_points_won=990,
            surface_service_points_played=1600,
            global_service_points_won=1850,
            global_service_points_played=3000,
        ),
        as_of=AS_OF,
    )


def _agent3_prediction_request() -> Agent3PredictionRequest:
    residuals = tuple(
        ResidualObservation(observed_at=AS_OF, residual=Decimal("0.60"))
        for _ in range(3)
    )
    snapshot = Agent3HistorySnapshot(
        pairs={
            (1, 2): PairHistorySnapshot(
                player_low_id=1,
                player_high_id=2,
                all_observations=residuals,
            )
        },
        adjacency={1: (2,), 2: (1,)},
    )
    return Agent3PredictionRequest(
        player_1_id=1,
        player_2_id=2,
        surface="HARD",
        history_snapshot=snapshot,
        as_of=AS_OF,
    )


def test_agent1_predicted_adapter_preserves_frozen_six_decimal_probability():
    raw = predict_agent1(_agent1_request())
    result = adapt_agent1_result(raw)

    assert result.prediction_status == "PREDICTED"
    assert result.selected_side == raw.selection_side
    assert result.selected_probability == raw.win_probability
    assert result.probability_representation == AGENT1_RAW_PROBABILITY_V1
    assert result.config_version == "agent1_surface_elo_config_v1"
    assert result.parameter_schema_version == "agent1_surface_elo_params_v1"


def test_agent2_predicted_and_abstained_adapters_use_public_contract_only():
    predicted = adapt_agent2_result(predict_agent2(_agent2_request()))
    abstained = adapt_agent2_result(predict_agent2(_agent2_request(surface=None)))

    assert predicted.prediction_status == "PREDICTED"
    assert predicted.probability_representation == AGENT2_RAW_PROBABILITY_V1
    assert predicted.selected_probability is not None
    assert -predicted.selected_probability.as_tuple().exponent == 6

    assert abstained.prediction_status == "ABSTAINED"
    assert abstained.selected_side is None
    assert abstained.selected_probability is None
    assert abstained.probability_representation is None
    assert abstained.abstain_reason == Agent2AbstainReason.UNKNOWN_SURFACE.value


def test_agent3_adapter_uses_high_precision_selected_probability_not_rounded_player_value():
    raw = predict_agent3(_agent3_prediction_request())
    result = adapt_agent3_result(raw)

    assert result.prediction_status == "PREDICTED"
    assert result.selected_probability == raw.selected_probability
    assert result.probability_representation == AGENT3_RAW_PROBABILITY_V1
    assert raw.selected_probability != raw.player_1_probability
    assert raw.selected_probability != raw.player_2_probability


def test_agent3_abstention_adapter_has_no_hidden_manager_probability():
    raw = predict_agent3(
        Agent3PredictionRequest(
            player_1_id=1,
            player_2_id=2,
            surface=None,
            history_snapshot=Agent3HistorySnapshot(),
            as_of=AS_OF,
        )
    )
    result = adapt_agent3_result(raw)

    assert result.prediction_status == "ABSTAINED"
    assert result.selected_side is None
    assert result.selected_probability is None
    assert result.probability_representation is None
    assert result.abstain_reason == "UNKNOWN_SURFACE"


def test_common_contract_rejects_invalid_probability_state_and_rejected_calibration():
    with pytest.raises(PredictiveAgentContractError):
        PredictiveAgentResultV1(
            agent_code="AGENT_3_INTRANSITIVITY",
            prediction_status="PREDICTED",
            selected_side=1,
            selected_probability=Decimal("0.55"),
            probability_representation="agent3_symmetric_temperature_v1",  # type: ignore[arg-type]
            abstain_reason=None,
            model_version="m",
            feature_version="f",
            config_version="c",
            parameter_schema_version="p",
            as_of=AS_OF,
        )

    with pytest.raises(PredictiveAgentContractError):
        probability_to_db_string(Decimal("0." + ("1" * 51)))


def test_decimal_db_serialization_never_uses_float_or_silent_quantization():
    value = Decimal("0.53000000000000000000000000000000000000000000000001")

    assert probability_to_db_string(value) == (
        "0.53000000000000000000000000000000000000000000000001"
    )
