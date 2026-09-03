from __future__ import annotations

from typing import Any

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as AGENT1_DEFAULT_CONFIG,
    PredictionResult as Agent1PredictionResult,
    SurfaceEloConfig,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    PredictionResult as Agent2PredictionResult,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    PredictionResult as Agent3PredictionResult,
)
from sports_edge_tennis.agents.contracts import (
    AGENT1_RAW_PROBABILITY_V1,
    AGENT2_RAW_PROBABILITY_V1,
    AGENT3_RAW_PROBABILITY_V1,
    PredictiveAgentResultV1,
)


def _diagnostics_payload(diagnostics: Any) -> dict[str, Any] | None:
    if diagnostics is None:
        return None
    to_payload = getattr(diagnostics, "to_payload", None)
    if callable(to_payload):
        payload = to_payload()
        return payload if isinstance(payload, dict) else {"value": payload}
    return {"value": diagnostics}


def _abstain_reason_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def adapt_agent1_result(
    result: Agent1PredictionResult,
    *,
    config: SurfaceEloConfig = AGENT1_DEFAULT_CONFIG,
) -> PredictiveAgentResultV1:
    return PredictiveAgentResultV1(
        agent_code=result.agent_code,
        prediction_status=result.prediction_status,
        selected_side=result.selection_side,
        selected_probability=result.win_probability,
        probability_representation=AGENT1_RAW_PROBABILITY_V1,
        abstain_reason=None,
        model_version=result.model_version,
        feature_version=result.feature_version,
        config_version=config.config_version,
        parameter_schema_version=config.parameter_schema_version,
        as_of=result.as_of,
        diagnostics_payload=_diagnostics_payload(result.diagnostics),
    )


def adapt_agent2_result(result: Agent2PredictionResult) -> PredictiveAgentResultV1:
    return PredictiveAgentResultV1(
        agent_code=result.agent_code,
        prediction_status=result.prediction_status,
        selected_side=result.selection_side,
        selected_probability=result.win_probability,
        probability_representation=(
            AGENT2_RAW_PROBABILITY_V1 if result.prediction_status == "PREDICTED" else None
        ),
        abstain_reason=_abstain_reason_value(result.diagnostics.abstain_reason),
        model_version=result.model_version,
        feature_version=result.feature_version,
        config_version=result.config_version,
        parameter_schema_version=result.parameter_schema_version,
        as_of=result.as_of,
        diagnostics_payload=_diagnostics_payload(result.diagnostics),
    )


def adapt_agent3_result(result: Agent3PredictionResult) -> PredictiveAgentResultV1:
    return PredictiveAgentResultV1(
        agent_code=result.agent_code,
        prediction_status=result.prediction_status,
        selected_side=result.selected_side,
        selected_probability=result.selected_probability,
        probability_representation=(
            AGENT3_RAW_PROBABILITY_V1 if result.prediction_status == "PREDICTED" else None
        ),
        abstain_reason=_abstain_reason_value(result.abstain_reason),
        model_version=result.model_version,
        feature_version=result.feature_version,
        config_version=result.config_version,
        parameter_schema_version=result.parameter_schema_version,
        as_of=result.as_of,
        diagnostics_payload=_diagnostics_payload(result.diagnostics),
    )
