from __future__ import annotations

from datetime import datetime

from sports_edge_tennis.agents.contracts import (
    AGENT_PROBABILITY_REPRESENTATIONS,
    PREDICTIVE_AGENT_CODES,
)
from sports_edge_tennis.data.prediction_persistence import PersistedPredictiveAgentPrediction
from sports_edge_tennis.manager.contracts import (
    ManagerConsensusError,
    ManagerConsensusResultV1,
    ManagerPolicyV1,
    consensus_mean,
    exact_spread,
)


def evaluate_consensus(
    predictions: list[PersistedPredictiveAgentPrediction] | tuple[PersistedPredictiveAgentPrediction, ...],
    *,
    match_id: int,
    prediction_run_id: int,
    policy: ManagerPolicyV1,
    as_of: datetime,
) -> ManagerConsensusResultV1:
    """Evaluate C1-C4 for one target match against the canonical three-agent set.

    Pure function: no database writes, no market/odds data, no external side effects.
    """
    by_agent = _validate_and_index(predictions)

    agent1 = by_agent["AGENT_1_SURFACE_ELO"]
    agent2 = by_agent["AGENT_2_HIERARCHICAL_MARKOV"]
    agent3 = by_agent["AGENT_3_INTRANSITIVITY"]

    active = [p for p in (agent1, agent2, agent3) if p.prediction_status == "PREDICTED"]
    abstaining_count = 3 - len(active)

    # Spread is meaningful whenever at least two agents are active, and is
    # stored regardless of direction so that NO_BET reasons remain auditable.
    spread = exact_spread(tuple(p.win_probability for p in active)) if len(active) >= 2 else None

    def no_bet(reason_code: str) -> ManagerConsensusResultV1:
        return ManagerConsensusResultV1(
            prediction_run_id=prediction_run_id,
            match_id=match_id,
            manager_policy_version_id=policy.manager_policy_version_id,
            manager_status="NO_BET",
            selected_side=None,
            consensus_probability=None,
            active_agent_count=len(active),
            abstaining_agent_count=abstaining_count,
            probability_spread=spread,
            reason_code=reason_code,  # type: ignore[arg-type]
            as_of=as_of,
        )

    # 1. Abstention configuration (structural precedence).
    # v1 historically permits only Agent 3 to abstain. v2 disables all abstention
    # without mutating or reinterpreting historical v1 Manager truth.
    if abstaining_count and not policy.allow_agent3_abstention:
        return no_bet("UNSUPPORTED_ABSTENTION_CONFIGURATION")

    if abstaining_count >= 2:
        return no_bet("TWO_OR_MORE_ABSTENTIONS")

    if abstaining_count == 1:
        abstained_agent = next(
            p for p in (agent1, agent2, agent3) if p.prediction_status == "ABSTAINED"
        )
        if abstained_agent.agent_code != "AGENT_3_INTRANSITIVITY":
            return no_bet("UNSUPPORTED_ABSTENTION_CONFIGURATION")

    # 2. C1 directional agreement across all active agents.
    sides = {p.selection_side for p in active}
    if len(sides) != 1:
        return no_bet("DIRECTIONAL_DISAGREEMENT")

    selected_side = next(iter(sides))

    # 3. C3 individual probability floor.
    for prediction in active:
        if prediction.win_probability < policy.min_active_agent_probability:
            return no_bet("ACTIVE_PROBABILITY_BELOW_MINIMUM")

    # 4. Probability spread (C2 for three agents, C4 for the Agent 3 abstention path).
    if abstaining_count == 0:
        threshold = policy.three_agent_max_probability_spread
        fail_reason = "THREE_AGENT_SPREAD_EXCEEDED"
    else:
        threshold = policy.agent3_abstention_max_probability_spread
        fail_reason = "AGENT3_ABSTENTION_SPREAD_EXCEEDED"

    if spread > threshold:
        return no_bet(fail_reason)

    consensus_probability = consensus_mean(tuple(p.win_probability for p in active))
    return ManagerConsensusResultV1(
        prediction_run_id=prediction_run_id,
        match_id=match_id,
        manager_policy_version_id=policy.manager_policy_version_id,
        manager_status="CONSENSUS",
        selected_side=selected_side,
        consensus_probability=consensus_probability,
        active_agent_count=len(active),
        abstaining_agent_count=abstaining_count,
        probability_spread=spread,
        reason_code="NONE",
        as_of=as_of,
    )


def _validate_and_index(
    predictions: list[PersistedPredictiveAgentPrediction] | tuple[PersistedPredictiveAgentPrediction, ...],
) -> dict[str, PersistedPredictiveAgentPrediction]:
    if len(predictions) != 3:
        raise ManagerConsensusError(
            "evaluate_consensus requires exactly three canonical predictive-agent results"
        )

    by_agent: dict[str, PersistedPredictiveAgentPrediction] = {}
    for prediction in predictions:
        if prediction.agent_code not in PREDICTIVE_AGENT_CODES:
            raise ManagerConsensusError(
                f"non-canonical predictive agent {prediction.agent_code!r} in Manager input"
            )
        if prediction.agent_code in by_agent:
            raise ManagerConsensusError(
                f"duplicate predictive agent {prediction.agent_code!r} in Manager input"
            )
        by_agent[prediction.agent_code] = prediction

    if set(by_agent) != set(PREDICTIVE_AGENT_CODES):
        raise ManagerConsensusError(
            "evaluate_consensus requires exactly AGENT_1, AGENT_2, and AGENT_3"
        )

    for prediction in by_agent.values():
        if prediction.prediction_status == "PREDICTED":
            if prediction.selection_side not in (1, 2):
                raise ManagerConsensusError("PREDICTED result requires selection_side 1 or 2")
            if prediction.win_probability is None:
                raise ManagerConsensusError("PREDICTED result requires a win_probability")
            expected_representation = AGENT_PROBABILITY_REPRESENTATIONS[prediction.agent_code]
            if prediction.probability_representation != expected_representation:
                raise ManagerConsensusError(
                    f"{prediction.agent_code} probability_representation does not match "
                    "the canonical predictive_agent_result_v1 representation"
                )
        elif prediction.prediction_status == "ABSTAINED":
            if prediction.selection_side is not None or prediction.win_probability is not None:
                raise ManagerConsensusError("ABSTAINED result must not carry side or probability")
            if prediction.probability_representation is not None:
                raise ManagerConsensusError(
                    "ABSTAINED result must not carry a probability_representation"
                )
        else:
            raise ManagerConsensusError(
                f"unsupported prediction_status {prediction.prediction_status!r}"
            )

    return by_agent
