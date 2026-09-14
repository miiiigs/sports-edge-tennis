from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.data.prediction_persistence import PersistedPredictiveAgentPrediction
from sports_edge_tennis.manager.strict_consensus import (
    NO_ACTIVE_AGENTS,
    NO_DIRECTIONAL_MAJORITY,
    SHADOW_ONE_ACTIVE,
    SHADOW_TWO_OF_THREE,
    STRICT_THREE_OF_THREE,
    classify_strict_consensus,
)

AS_OF = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"


def _pred(agent_code: str, *, side: int | None = 1, prob: str = "0.60", abstain: bool = False):
    if abstain:
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
            agent_config_version_id=1,
            as_of=AS_OF,
        )
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
        agent_config_version_id=1,
        as_of=AS_OF,
    )


def test_three_of_three_same_side_is_premium():
    result = classify_strict_consensus(
        [_pred(A1, prob="0.61"), _pred(A2, prob="0.64"), _pred(A3, prob="0.59")]
    )

    assert result.bucket == STRICT_THREE_OF_THREE
    assert result.is_premium is True
    assert result.selected_side == 1
    assert result.active_agent_count == 3
    assert result.agreeing_agent_count == 3
    assert result.min_probability == Decimal("0.59")
    assert result.probability_spread == Decimal("0.05")


def test_two_one_split_is_shadow_two_of_three():
    result = classify_strict_consensus(
        [_pred(A1, side=1), _pred(A2, side=1), _pred(A3, side=2)]
    )

    assert result.bucket == SHADOW_TWO_OF_THREE
    assert result.is_premium is False
    assert result.selected_side == 1
    assert result.agreeing_agent_count == 2


def test_agent3_abstention_with_a1_a2_agreement_is_shadow_not_premium():
    result = classify_strict_consensus(
        [_pred(A1, side=2), _pred(A2, side=2), _pred(A3, abstain=True)]
    )

    assert result.bucket == SHADOW_TWO_OF_THREE
    assert result.selected_side == 2
    assert result.active_agent_count == 2
    assert result.abstaining_agent_count == 1
    assert result.is_premium is False


def test_two_active_agents_disagree_has_no_directional_majority():
    result = classify_strict_consensus(
        [_pred(A1, side=1), _pred(A2, side=2), _pred(A3, abstain=True)]
    )

    assert result.bucket == NO_DIRECTIONAL_MAJORITY
    assert result.selected_side is None
    assert result.agreeing_agent_count == 0


def test_one_active_agent_is_shadow_one_active():
    result = classify_strict_consensus(
        [_pred(A1, side=2), _pred(A2, abstain=True), _pred(A3, abstain=True)]
    )

    assert result.bucket == SHADOW_ONE_ACTIVE
    assert result.selected_side == 2
    assert result.active_agent_count == 1


def test_all_abstain_has_no_active_agents():
    result = classify_strict_consensus(
        [_pred(A1, abstain=True), _pred(A2, abstain=True), _pred(A3, abstain=True)]
    )

    assert result.bucket == NO_ACTIVE_AGENTS
    assert result.selected_side is None
    assert result.mean_probability is None


def test_rejects_noncanonical_or_incomplete_agent_sets():
    with pytest.raises(ValueError):
        classify_strict_consensus([_pred(A1), _pred(A2)])

    with pytest.raises(ValueError):
        classify_strict_consensus([_pred(A1), _pred(A2), _pred(A2)])
