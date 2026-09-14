from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal, Sequence

from sports_edge_tennis.data.prediction_persistence import PersistedPredictiveAgentPrediction

STRICT_THREE_OF_THREE = "THREE_OF_THREE"
SHADOW_TWO_OF_THREE = "TWO_OF_THREE"
SHADOW_ONE_ACTIVE = "ONE_ACTIVE"
NO_DIRECTIONAL_MAJORITY = "NO_DIRECTIONAL_MAJORITY"
NO_ACTIVE_AGENTS = "NO_ACTIVE_AGENTS"

ConsensusBucket = Literal[
    "THREE_OF_THREE",
    "TWO_OF_THREE",
    "ONE_ACTIVE",
    "NO_DIRECTIONAL_MAJORITY",
    "NO_ACTIVE_AGENTS",
]

CANONICAL_AGENTS = {
    "AGENT_1_SURFACE_ELO",
    "AGENT_2_HIERARCHICAL_MARKOV",
    "AGENT_3_INTRANSITIVITY",
}


@dataclass(frozen=True)
class StrictConsensusClassification:
    """Odds-independent classification of the three predictive-agent votes.

    This is intentionally separate from the production Manager policy. It does
    not apply value, bookmaker, price, probability-floor, or probability-spread
    gates. Its purpose is to measure Brian's proposed winner-selection funnel:
    all three agents see the same match independently, then 3/3 same-player
    agreement is the premium winner selection while weaker agreement is shadow
    tracked for comparison.
    """

    bucket: ConsensusBucket
    selected_side: int | None
    active_agent_count: int
    abstaining_agent_count: int
    agreeing_agent_count: int
    min_probability: Decimal | None
    mean_probability: Decimal | None
    probability_spread: Decimal | None

    @property
    def is_premium(self) -> bool:
        return self.bucket == STRICT_THREE_OF_THREE


def classify_strict_consensus(
    predictions: Sequence[PersistedPredictiveAgentPrediction],
) -> StrictConsensusClassification:
    if len(predictions) != 3:
        raise ValueError("strict consensus requires exactly three predictive-agent results")

    codes = [prediction.agent_code for prediction in predictions]
    if len(set(codes)) != 3 or set(codes) != CANONICAL_AGENTS:
        raise ValueError("strict consensus requires exactly the canonical three agents")

    active = [
        prediction
        for prediction in predictions
        if prediction.prediction_status == "PREDICTED"
    ]
    abstaining_count = 3 - len(active)

    for prediction in active:
        if prediction.selection_side not in (1, 2) or prediction.win_probability is None:
            raise ValueError("active prediction requires side 1/2 and win probability")

    if not active:
        return StrictConsensusClassification(
            bucket=NO_ACTIVE_AGENTS,
            selected_side=None,
            active_agent_count=0,
            abstaining_agent_count=3,
            agreeing_agent_count=0,
            min_probability=None,
            mean_probability=None,
            probability_spread=None,
        )

    side_counts = Counter(int(prediction.selection_side) for prediction in active)
    selected_side, agreeing_count = max(
        side_counts.items(),
        key=lambda item: (item[1], -item[0]),
    )

    if len(active) == 3 and agreeing_count == 3:
        bucket: ConsensusBucket = STRICT_THREE_OF_THREE
    elif agreeing_count >= 2:
        bucket = SHADOW_TWO_OF_THREE
    elif len(active) == 1:
        bucket = SHADOW_ONE_ACTIVE
    else:
        bucket = NO_DIRECTIONAL_MAJORITY
        selected_side = None
        agreeing_count = 0

    probabilities = [prediction.win_probability for prediction in active]
    minimum = min(probabilities)
    maximum = max(probabilities)
    with localcontext() as context:
        context.prec = 60
        mean = sum(probabilities, Decimal("0")) / Decimal(len(probabilities))
        spread = maximum - minimum

    return StrictConsensusClassification(
        bucket=bucket,
        selected_side=selected_side,
        active_agent_count=len(active),
        abstaining_agent_count=abstaining_count,
        agreeing_agent_count=agreeing_count,
        min_probability=minimum,
        mean_probability=mean,
        probability_spread=spread,
    )
