from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, getcontext

import pytest

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.data.prediction_persistence import PersistedPredictiveAgentPrediction
from sports_edge_tennis.manager.contracts import (
    ManagerConsensusError,
    ManagerPolicyV1,
    consensus_mean,
    exact_spread,
)
from sports_edge_tennis.manager.consensus import evaluate_consensus


AS_OF = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"


def _policy() -> ManagerPolicyV1:
    return ManagerPolicyV1(
        manager_policy_version_id=1,
        policy_key="tennis_manager_consensus",
        policy_version="v1",
        three_agent_max_probability_spread=Decimal("0.080000"),
        agent3_abstention_max_probability_spread=Decimal("0.060000"),
        min_active_agent_probability=Decimal("0.500000"),
    )


def _pred(
    agent_code: str,
    *,
    status: str = "PREDICTED",
    side: int | None = 1,
    prob: str = "0.60",
) -> PersistedPredictiveAgentPrediction:
    if status == "PREDICTED":
        return PersistedPredictiveAgentPrediction(
            prediction_id=0,
            prediction_run_id=1,
            match_id=1,
            agent_code=agent_code,
            prediction_status="PREDICTED",
            selection_side=side,
            win_probability=Decimal(prob),
            probability_representation=AGENT_PROBABILITY_REPRESENTATIONS[agent_code],
            model_version="m1",
            feature_version="f1",
            config_version="c1",
            parameter_schema_version="p1",
            agent_config_version_id=0,
            as_of=AS_OF,
        )
    return PersistedPredictiveAgentPrediction(
        prediction_id=0,
        prediction_run_id=1,
        match_id=1,
        agent_code=agent_code,
        prediction_status="ABSTAINED",
        selection_side=None,
        win_probability=None,
        probability_representation=None,
        model_version="m1",
        feature_version="f1",
        config_version="c1",
        parameter_schema_version="p1",
        agent_config_version_id=0,
        as_of=AS_OF,
    )


def _evaluate(predictions):
    return evaluate_consensus(
        predictions,
        match_id=1,
        prediction_run_id=1,
        policy=_policy(),
        as_of=AS_OF,
    )


def test_valid_three_agent_consensus():
    result = _evaluate(
        [_pred(A1, prob="0.60"), _pred(A2, prob="0.62"), _pred(A3, prob="0.61")]
    )

    assert result.manager_status == "CONSENSUS"
    assert result.reason_code == "NONE"
    assert result.selected_side == 1
    assert result.active_agent_count == 3
    assert result.abstaining_agent_count == 0
    assert result.consensus_probability == Decimal("0.61")
    assert -result.consensus_probability.as_tuple().exponent == 50
    assert result.probability_spread == Decimal("0.020000")


def test_directional_disagreement_no_bet():
    result = _evaluate(
        [_pred(A1, side=1, prob="0.60"), _pred(A2, side=2, prob="0.62"), _pred(A3, side=1, prob="0.61")]
    )

    assert result.manager_status == "NO_BET"
    assert result.reason_code == "DIRECTIONAL_DISAGREEMENT"
    assert result.selected_side is None
    assert result.consensus_probability is None


def test_three_agent_spread_exactly_boundary_passes():
    result = _evaluate(
        [_pred(A1, prob="0.50"), _pred(A2, prob="0.58"), _pred(A3, prob="0.54")]
    )

    assert result.manager_status == "CONSENSUS"
    assert result.reason_code == "NONE"


def test_three_agent_spread_above_boundary_no_bet():
    result = _evaluate(
        [_pred(A1, prob="0.50"), _pred(A2, prob="0.59"), _pred(A3, prob="0.54")]
    )

    assert result.manager_status == "NO_BET"
    assert result.reason_code == "THREE_AGENT_SPREAD_EXCEEDED"


def test_active_probability_exactly_floor_passes():
    result = _evaluate(
        [_pred(A1, prob="0.50"), _pred(A2, prob="0.55"), _pred(A3, prob="0.55")]
    )

    assert result.manager_status == "CONSENSUS"


def test_active_probability_below_floor_no_bet():
    result = _evaluate(
        [_pred(A1, prob="0.49"), _pred(A2, prob="0.55"), _pred(A3, prob="0.55")]
    )

    assert result.manager_status == "NO_BET"
    assert result.reason_code == "ACTIVE_PROBABILITY_BELOW_MINIMUM"


def test_agent3_abstention_spread_exactly_boundary_passes():
    result = _evaluate(
        [_pred(A1, prob="0.52"), _pred(A2, prob="0.58"), _pred(A3, status="ABSTAINED")]
    )

    assert result.manager_status == "CONSENSUS"
    assert result.reason_code == "NONE"
    assert result.selected_side == 1
    assert result.active_agent_count == 2
    assert result.abstaining_agent_count == 1
    assert result.consensus_probability == Decimal("0.55000000000000000000000000000000000000000000000000")


def test_agent3_abstention_spread_above_boundary_no_bet():
    result = _evaluate(
        [_pred(A1, prob="0.52"), _pred(A2, prob="0.59"), _pred(A3, status="ABSTAINED")]
    )

    assert result.manager_status == "NO_BET"
    assert result.reason_code == "AGENT3_ABSTENTION_SPREAD_EXCEEDED"


def test_agent1_abstention_unsupported():
    result = _evaluate(
        [_pred(A1, status="ABSTAINED"), _pred(A2, prob="0.60"), _pred(A3, prob="0.61")]
    )

    assert result.manager_status == "NO_BET"
    assert result.reason_code == "UNSUPPORTED_ABSTENTION_CONFIGURATION"
    assert result.active_agent_count == 2
    assert result.abstaining_agent_count == 1
    assert result.probability_spread == Decimal("0.010000")


def test_agent2_abstention_unsupported():
    result = _evaluate(
        [_pred(A1, prob="0.60"), _pred(A2, status="ABSTAINED"), _pred(A3, prob="0.61")]
    )

    assert result.manager_status == "NO_BET"
    assert result.reason_code == "UNSUPPORTED_ABSTENTION_CONFIGURATION"


def test_two_abstentions_no_bet():
    result = _evaluate(
        [_pred(A1, prob="0.60"), _pred(A2, status="ABSTAINED"), _pred(A3, status="ABSTAINED")]
    )

    assert result.manager_status == "NO_BET"
    assert result.reason_code == "TWO_OR_MORE_ABSTENTIONS"
    assert result.probability_spread is None


def test_three_abstentions_no_bet():
    result = _evaluate(
        [
            _pred(A1, status="ABSTAINED"),
            _pred(A2, status="ABSTAINED"),
            _pred(A3, status="ABSTAINED"),
        ]
    )

    assert result.manager_status == "NO_BET"
    assert result.reason_code == "TWO_OR_MORE_ABSTENTIONS"


def test_agent3_abstention_min_probability_still_applies():
    result = _evaluate(
        [_pred(A1, prob="0.49"), _pred(A2, prob="0.55"), _pred(A3, status="ABSTAINED")]
    )

    assert result.manager_status == "NO_BET"
    assert result.reason_code == "ACTIVE_PROBABILITY_BELOW_MINIMUM"


def test_repeating_divide_by_three_rounds_half_up():
    result = _evaluate(
        [_pred(A1, prob="0.55"), _pred(A2, prob="0.57"), _pred(A3, prob="0.58")]
    )

    assert result.manager_status == "CONSENSUS"
    assert result.consensus_probability == Decimal("0.5" + "6" * 48 + "7")
    assert -result.consensus_probability.as_tuple().exponent == 50


def test_consensus_mean_uses_local_context_not_global():
    original = getcontext()
    original_prec = original.prec

    getcontext().prec = 5

    try:
        result = consensus_mean((Decimal("0.60"), Decimal("0.60"), Decimal("0.61")))
        assert result == Decimal("0.60" + "3" * 48)
        assert -result.as_tuple().exponent == 50
    finally:
        getcontext().prec = original_prec

    assert getcontext().prec == original_prec


def test_exact_spread_is_exact_under_hostile_context():
    original_prec = getcontext().prec
    getcontext().prec = 3
    try:
        spread = exact_spread(
            (Decimal("0.61123456789012345678901234567890123456789012345678"), Decimal("0.60"))
        )
        assert spread == Decimal("0.01123456789012345678901234567890123456789012345678")
    finally:
        getcontext().prec = original_prec


def test_evaluator_rejects_non_canonical_agent_set():
    with pytest.raises(ManagerConsensusError):
        _evaluate([_pred(A1, prob="0.60"), _pred(A2, prob="0.61")])

    with pytest.raises(ManagerConsensusError):
        _evaluate([_pred(A1, prob="0.60"), _pred(A2, prob="0.61"), _pred(A2, prob="0.62")])


def test_evaluator_rejects_invalid_prediction_state():
    bad = _pred(A1, prob="0.60")
    bad_abstained = _pred(A1, status="ABSTAINED")
    bad_abstained = PersistedPredictiveAgentPrediction(
        prediction_id=0,
        prediction_run_id=1,
        match_id=1,
        agent_code=A1,
        prediction_status="ABSTAINED",
        selection_side=1,
        win_probability=None,
        probability_representation=None,
        model_version="m1",
        feature_version="f1",
        config_version="c1",
        parameter_schema_version="p1",
        agent_config_version_id=0,
        as_of=AS_OF,
    )
    with pytest.raises(ManagerConsensusError):
        _evaluate([bad_abstained, _pred(A2, prob="0.61"), _pred(A3, prob="0.62")])



def test_strict_policy_rejects_agent3_abstention() -> None:
    policy = ManagerPolicyV1(
        manager_policy_version_id=2,
        policy_key="tennis_manager_consensus",
        policy_version="v2",
        three_agent_max_probability_spread=Decimal("0.080000"),
        agent3_abstention_max_probability_spread=Decimal("0.000000"),
        min_active_agent_probability=Decimal("0.500000"),
        allow_agent3_abstention=False,
    )
    result = evaluate_consensus(
        [_pred(A1, prob="0.60"), _pred(A2, prob="0.61"), _pred(A3, status="ABSTAINED")],
        match_id=1,
        prediction_run_id=1,
        policy=policy,
        as_of=AS_OF,
    )
    assert result.manager_status == "NO_BET"
    assert result.reason_code == "UNSUPPORTED_ABSTENTION_CONFIGURATION"
    assert result.active_agent_count == 2
    assert result.abstaining_agent_count == 1


def test_strict_policy_still_accepts_valid_three_of_three() -> None:
    policy = ManagerPolicyV1(
        manager_policy_version_id=2,
        policy_key="tennis_manager_consensus",
        policy_version="v2",
        three_agent_max_probability_spread=Decimal("0.080000"),
        agent3_abstention_max_probability_spread=Decimal("0.000000"),
        min_active_agent_probability=Decimal("0.500000"),
        allow_agent3_abstention=False,
    )
    result = evaluate_consensus(
        [_pred(A1, prob="0.60"), _pred(A2, prob="0.62"), _pred(A3, prob="0.61")],
        match_id=1,
        prediction_run_id=1,
        policy=policy,
        as_of=AS_OF,
    )
    assert result.manager_status == "CONSENSUS"
    assert result.active_agent_count == 3
    assert result.abstaining_agent_count == 0
