from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


PREDICTIVE_AGENT_CODES = (
    "AGENT_1_SURFACE_ELO",
    "AGENT_2_HIERARCHICAL_MARKOV",
    "AGENT_3_INTRANSITIVITY",
)

PREDICTION_CONTRACT_LEGACY = "legacy_prediction_ledger_v1"
PREDICTION_CONTRACT_V1 = "predictive_agent_result_v1"

AGENT1_RAW_PROBABILITY_V1 = "agent1_raw_probability_v1"
AGENT2_RAW_PROBABILITY_V1 = "agent2_raw_probability_v1"
AGENT3_RAW_PROBABILITY_V1 = "agent3_raw_probability_v1"
LEGACY_PERSISTED_WIN_PROBABILITY_V1 = "legacy_persisted_win_probability_v1"

AGENT_PROBABILITY_REPRESENTATIONS = {
    "AGENT_1_SURFACE_ELO": AGENT1_RAW_PROBABILITY_V1,
    "AGENT_2_HIERARCHICAL_MARKOV": AGENT2_RAW_PROBABILITY_V1,
    "AGENT_3_INTRANSITIVITY": AGENT3_RAW_PROBABILITY_V1,
}

ProbabilityRepresentation = Literal[
    "agent1_raw_probability_v1",
    "agent2_raw_probability_v1",
    "agent3_raw_probability_v1",
    "legacy_persisted_win_probability_v1",
]

PredictionStatus = Literal["PREDICTED", "ABSTAINED"]

_NUMERIC_51_50_SCALE = 50


class PredictiveAgentContractError(ValueError):
    """Raised when a normalized predictive-agent result is not safe to persist."""


def ensure_canonical_probability(value: Decimal, *, field_name: str = "selected_probability") -> Decimal:
    if not isinstance(value, Decimal):
        raise PredictiveAgentContractError(f"{field_name} must be a Decimal")
    if value.is_nan() or value.is_infinite():
        raise PredictiveAgentContractError(f"{field_name} must be finite")
    if not (Decimal("0") <= value <= Decimal("1")):
        raise PredictiveAgentContractError(f"{field_name} must be within [0, 1]")
    if -value.as_tuple().exponent > _NUMERIC_51_50_SCALE:
        raise PredictiveAgentContractError(
            f"{field_name} exceeds NUMERIC(51,50) scale and must not be silently rounded"
        )
    return value


def probability_to_db_string(value: Decimal) -> str:
    probability = ensure_canonical_probability(value)
    return format(probability, "f")


@dataclass(frozen=True)
class PredictiveAgentResultV1:
    agent_code: str
    prediction_status: PredictionStatus
    selected_side: Literal[1, 2] | None
    selected_probability: Decimal | None
    probability_representation: ProbabilityRepresentation | None
    abstain_reason: str | None
    model_version: str
    feature_version: str
    config_version: str
    parameter_schema_version: str
    as_of: datetime
    diagnostics_payload: dict[str, Any] | None = field(default=None)

    def __post_init__(self) -> None:
        if self.agent_code not in PREDICTIVE_AGENT_CODES:
            raise PredictiveAgentContractError("agent_code must be a canonical predictive agent")
        for field_name in (
            "model_version",
            "feature_version",
            "config_version",
            "parameter_schema_version",
        ):
            if not getattr(self, field_name).strip():
                raise PredictiveAgentContractError(f"{field_name} must be non-empty")
        if self.diagnostics_payload is not None and not isinstance(self.diagnostics_payload, dict):
            raise PredictiveAgentContractError("diagnostics_payload must be a dict when provided")
        if self.prediction_status == "PREDICTED":
            if self.selected_side not in (1, 2):
                raise PredictiveAgentContractError("PREDICTED requires selected_side 1 or 2")
            if self.selected_probability is None:
                raise PredictiveAgentContractError("PREDICTED requires selected_probability")
            ensure_canonical_probability(self.selected_probability)
            expected_representation = AGENT_PROBABILITY_REPRESENTATIONS[self.agent_code]
            if self.probability_representation != expected_representation:
                raise PredictiveAgentContractError(
                    "PREDICTED probability_representation must match agent_code"
                )
            if self.abstain_reason is not None:
                raise PredictiveAgentContractError("PREDICTED requires abstain_reason to be NULL")
            return
        if self.prediction_status == "ABSTAINED":
            if self.selected_side is not None:
                raise PredictiveAgentContractError("ABSTAINED requires selected_side to be NULL")
            if self.selected_probability is not None:
                raise PredictiveAgentContractError(
                    "ABSTAINED requires selected_probability to be NULL"
                )
            if self.probability_representation is not None:
                raise PredictiveAgentContractError(
                    "ABSTAINED requires probability_representation to be NULL"
                )
            if not self.abstain_reason:
                raise PredictiveAgentContractError("ABSTAINED requires abstain_reason")
            return
        raise PredictiveAgentContractError("prediction_status must be PREDICTED or ABSTAINED")

    @property
    def db_probability(self) -> str | None:
        if self.selected_probability is None:
            return None
        return probability_to_db_string(self.selected_probability)
