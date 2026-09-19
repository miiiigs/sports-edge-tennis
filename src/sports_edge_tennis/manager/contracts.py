from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Literal


MANAGER_POLICY_KEY = "tennis_manager_consensus"
MANAGER_POLICY_VERSION = "v1"
MANAGER_POLICY_CURRENT_VERSION = "v2"

MANAGER_PRECISION = 60
MANAGER_QUANTIZER = Decimal("1E-50")

ManagerStatus = Literal["CONSENSUS", "NO_BET"]

ManagerReasonCode = Literal[
    "NONE",
    "DIRECTIONAL_DISAGREEMENT",
    "THREE_AGENT_SPREAD_EXCEEDED",
    "AGENT3_ABSTENTION_SPREAD_EXCEEDED",
    "ACTIVE_PROBABILITY_BELOW_MINIMUM",
    "UNSUPPORTED_ABSTENTION_CONFIGURATION",
    "TWO_OR_MORE_ABSTENTIONS",
]

THREE_AGENT_MAX_PROBABILITY_SPREAD = Decimal("0.080000")
AGENT3_ABSTENTION_MAX_PROBABILITY_SPREAD = Decimal("0.060000")
MIN_ACTIVE_AGENT_PROBABILITY = Decimal("0.500000")


class ManagerConsensusError(ValueError):
    """Raised for invalid Manager input or invariant violations."""


class ManagerIdempotencyMismatchError(ManagerConsensusError):
    """Raised when persisted Manager truth differs from the computed canonical slate."""


class ManagerPartialSlateError(ManagerConsensusError):
    """Raised when persisted Manager rows are a strict subset of the target slate."""


@dataclass(frozen=True)
class ManagerPolicyV1:
    manager_policy_version_id: int
    policy_key: str
    policy_version: str
    three_agent_max_probability_spread: Decimal
    agent3_abstention_max_probability_spread: Decimal
    min_active_agent_probability: Decimal
    allow_agent3_abstention: bool = True


@dataclass(frozen=True)
class ManagerConsensusResultV1:
    prediction_run_id: int
    match_id: int
    manager_policy_version_id: int
    manager_status: ManagerStatus
    selected_side: int | None
    consensus_probability: Decimal | None
    active_agent_count: int
    abstaining_agent_count: int
    probability_spread: Decimal | None
    reason_code: ManagerReasonCode
    as_of: datetime


def consensus_mean(probabilities: tuple[Decimal, ...]) -> Decimal:
    """Return the unweighted arithmetic mean under one local Decimal context.

    The context begins before any arithmetic and covers sum, division, and
    quantization. It never mutates the global Decimal context.
    """
    if not probabilities:
        raise ManagerConsensusError("consensus_mean requires at least one probability")
    with localcontext() as ctx:
        ctx.prec = MANAGER_PRECISION
        ctx.rounding = ROUND_HALF_UP

        total = Decimal("0")
        for probability in probabilities:
            total = total + probability

        mean = total / Decimal(len(probabilities))
        return mean.quantize(MANAGER_QUANTIZER, rounding=ROUND_HALF_UP)


def exact_spread(probabilities: tuple[Decimal, ...]) -> Decimal:
    """Return max - min across probabilities, computed under the local context."""
    if len(probabilities) < 2:
        raise ManagerConsensusError("exact_spread requires at least two probabilities")
    with localcontext() as ctx:
        ctx.prec = MANAGER_PRECISION
        ctx.rounding = ROUND_HALF_UP
        return max(probabilities) - min(probabilities)
