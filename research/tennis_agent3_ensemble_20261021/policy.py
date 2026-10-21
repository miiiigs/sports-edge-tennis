"""Research-only copy of the production Manager consensus policy.

The production Manager is the source of truth. This module mirrors the v1/v2
policy so a shadow candidate can be evaluated without mutating production
predictions, Manager rows, thresholds, weights, or betting logic. A unit test
asserts exact agreement with
``sports_edge_tennis.manager.consensus.evaluate_consensus`` on the baseline
inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Literal, Mapping

AGENT_1 = "AGENT_1_SURFACE_ELO"
AGENT_2 = "AGENT_2_HIERARCHICAL_MARKOV"
AGENT_3 = "AGENT_3_INTRANSITIVITY"
AGENTS = (AGENT_1, AGENT_2, AGENT_3)

ManagerStatus = Literal["CONSENSUS", "NO_BET"]
ReasonCode = Literal[
    "NONE",
    "DIRECTIONAL_DISAGREEMENT",
    "THREE_AGENT_SPREAD_EXCEEDED",
    "AGENT3_ABSTENTION_SPREAD_EXCEEDED",
    "ACTIVE_PROBABILITY_BELOW_MINIMUM",
    "UNSUPPORTED_ABSTENTION_CONFIGURATION",
    "TWO_OR_MORE_ABSTENTIONS",
]

MANAGER_PRECISION = 60
MANAGER_QUANTIZER = Decimal("1E-50")


@dataclass(frozen=True)
class AgentVote:
    agent_code: str
    prediction_status: Literal["PREDICTED", "ABSTAINED"]
    selection_side: int | None
    win_probability: Decimal | None


@dataclass(frozen=True)
class PolicyConfig:
    policy_version: str
    allow_agent3_abstention: bool
    three_agent_max_probability_spread: Decimal
    agent3_abstention_max_probability_spread: Decimal
    min_active_agent_probability: Decimal


@dataclass(frozen=True)
class Decision:
    manager_status: ManagerStatus
    selected_side: int | None
    consensus_probability: Decimal | None
    active_agent_count: int
    abstaining_agent_count: int
    probability_spread: Decimal | None
    reason_code: ReasonCode


def _mean(probabilities: tuple[Decimal, ...]) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MANAGER_PRECISION
        ctx.rounding = ROUND_HALF_UP
        total = Decimal("0")
        for probability in probabilities:
            total += probability
        return (total / Decimal(len(probabilities))).quantize(
            MANAGER_QUANTIZER, rounding=ROUND_HALF_UP
        )


def _spread(probabilities: tuple[Decimal, ...]) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = MANAGER_PRECISION
        ctx.rounding = ROUND_HALF_UP
        return max(probabilities) - min(probabilities)


def policy_from_row(row: Mapping[str, object]) -> PolicyConfig:
    return PolicyConfig(
        policy_version=str(row["policy_version"]),
        allow_agent3_abstention=bool(row["allow_agent3_abstention"]),
        three_agent_max_probability_spread=Decimal(
            str(row["three_agent_max_probability_spread"])
        ),
        agent3_abstention_max_probability_spread=Decimal(
            str(row["agent3_abstention_max_probability_spread"])
        ),
        min_active_agent_probability=Decimal(str(row["min_active_agent_probability"])),
    )


def simulate_manager(
    votes: Mapping[str, AgentVote],
    *,
    policy: PolicyConfig,
    as_of: datetime | None = None,
) -> Decision:
    del as_of  # accepted for symmetry with the production call; no time logic here
    if set(votes) != set(AGENTS):
        raise ValueError("simulate_manager requires exactly the three canonical agents")

    agent1 = votes[AGENT_1]
    agent2 = votes[AGENT_2]
    agent3 = votes[AGENT_3]
    active = [vote for vote in (agent1, agent2, agent3) if vote.prediction_status == "PREDICTED"]
    abstaining_count = 3 - len(active)

    spread = _spread(tuple(vote.win_probability for vote in active)) if len(active) >= 2 else None

    def no_bet(reason_code: ReasonCode) -> Decision:
        return Decision(
            manager_status="NO_BET",
            selected_side=None,
            consensus_probability=None,
            active_agent_count=len(active),
            abstaining_agent_count=abstaining_count,
            probability_spread=spread,
            reason_code=reason_code,
        )

    if abstaining_count and not policy.allow_agent3_abstention:
        return no_bet("UNSUPPORTED_ABSTENTION_CONFIGURATION")
    if abstaining_count >= 2:
        return no_bet("TWO_OR_MORE_ABSTENTIONS")
    if abstaining_count == 1:
        abstained = next(
            vote for vote in (agent1, agent2, agent3) if vote.prediction_status == "ABSTAINED"
        )
        if abstained.agent_code != AGENT_3:
            return no_bet("UNSUPPORTED_ABSTENTION_CONFIGURATION")

    sides = {vote.selection_side for vote in active}
    if len(sides) != 1:
        return no_bet("DIRECTIONAL_DISAGREEMENT")
    selected_side = next(iter(sides))
    if selected_side not in (1, 2):
        raise ValueError("active vote requires selection_side 1 or 2")

    for vote in active:
        if vote.win_probability is None:
            raise ValueError("active vote requires a win probability")
        if vote.win_probability < policy.min_active_agent_probability:
            return no_bet("ACTIVE_PROBABILITY_BELOW_MINIMUM")

    if abstaining_count == 0:
        threshold = policy.three_agent_max_probability_spread
        fail_reason: ReasonCode = "THREE_AGENT_SPREAD_EXCEEDED"
    else:
        threshold = policy.agent3_abstention_max_probability_spread
        fail_reason = "AGENT3_ABSTENTION_SPREAD_EXCEEDED"

    if spread is None or spread > threshold:
        return no_bet(fail_reason)

    probabilities = tuple(vote.win_probability for vote in active)
    return Decision(
        manager_status="CONSENSUS",
        selected_side=selected_side,
        consensus_probability=_mean(probabilities),
        active_agent_count=len(active),
        abstaining_agent_count=abstaining_count,
        probability_spread=spread,
        reason_code="NONE",
    )
