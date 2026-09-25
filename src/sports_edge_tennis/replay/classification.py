"""Model Pick classification and funnel outcome mapping.

Category 1 (``MANAGER_QUALIFIED``) is the current system's Manager consensus.
Category 2 (``A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED``) records the
primary-model agreement that the dashboard surfaces as a wide-spread model pick.

Category 2 is deliberately NOT a Value Bet and does not change Manager behaviour.
The wide-spread reporting lane is implemented separately; this module only keeps
the replay schema able to segment it cleanly.
"""

from __future__ import annotations

from collections.abc import Mapping

from sports_edge_tennis.agents.contracts import PredictiveAgentResultV1
from sports_edge_tennis.manager.contracts import ManagerConsensusResultV1
from sports_edge_tennis.replay.contracts import (
    ModelPickClassification,
    OpportunityOutcome,
)

_AGENT_1 = "AGENT_1_SURFACE_ELO"
_AGENT_2 = "AGENT_2_HIERARCHICAL_MARKOV"
_AGENT_3 = "AGENT_3_INTRANSITIVITY"

_ABSTENTION_REASONS = frozenset(
    {
        "TWO_OR_MORE_ABSTENTIONS",
        "UNSUPPORTED_ABSTENTION_CONFIGURATION",
    }
)


def classify_model_pick(
    manager_result: ManagerConsensusResultV1,
    adapted_by_code: Mapping[str, PredictiveAgentResultV1],
) -> ModelPickClassification:
    if manager_result.manager_status == "CONSENSUS":
        return "MANAGER_QUALIFIED"

    agent1 = adapted_by_code.get(_AGENT_1)
    agent2 = adapted_by_code.get(_AGENT_2)
    agent3 = adapted_by_code.get(_AGENT_3)

    if (
        agent1 is not None
        and agent2 is not None
        and agent3 is not None
        and agent1.prediction_status == "PREDICTED"
        and agent2.prediction_status == "PREDICTED"
        and agent1.selected_side is not None
        and agent1.selected_side == agent2.selected_side
        and agent3.prediction_status == "ABSTAINED"
        and manager_result.reason_code == "AGENT3_ABSTENTION_SPREAD_EXCEEDED"
    ):
        return "A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED"

    return "NOT_MANAGER_QUALIFIED"


def manager_rejection_outcome(
    manager_result: ManagerConsensusResultV1,
) -> OpportunityOutcome:
    if manager_result.manager_status == "CONSENSUS":
        raise ValueError("manager_rejection_outcome requires a non-consensus result")
    if manager_result.reason_code in _ABSTENTION_REASONS:
        return "AGENT_ABSTENTION"
    return "MANAGER_REJECTION"


__all__ = ["classify_model_pick", "manager_rejection_outcome"]
