from __future__ import annotations

"""Read-only in-memory reconstruction of Manager v1 consensus.

Nothing here persists: agent replay results are mapped into the canonical
persisted-prediction contract and passed to the pure ``evaluate_consensus``
function, so Manager reasoning can be measured without touching
``manager_consensus_results`` or any production table.
"""

from datetime import datetime

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.data.prediction_persistence import PersistedPredictiveAgentPrediction
from sports_edge_tennis.manager.consensus import evaluate_consensus
from sports_edge_tennis.manager.contracts import ManagerConsensusResultV1, ManagerPolicyV1

AGENT_1 = "AGENT_1_SURFACE_ELO"
AGENT_2 = "AGENT_2_HIERARCHICAL_MARKOV"
AGENT_3 = "AGENT_3_INTRANSITIVITY"


def build_prediction(
    *,
    prediction_id: int,
    prediction_run_id: int,
    match_id: int,
    agent_code: str,
    prediction_status: str,
    selection_side: int | None,
    win_probability,
    model_version: str,
    feature_version: str,
    config_version: str,
    parameter_schema_version: str,
    as_of: datetime,
) -> PersistedPredictiveAgentPrediction:
    representation = (
        AGENT_PROBABILITY_REPRESENTATIONS[agent_code]
        if prediction_status == "PREDICTED"
        else None
    )
    return PersistedPredictiveAgentPrediction(
        prediction_id=prediction_id,
        prediction_run_id=prediction_run_id,
        match_id=match_id,
        agent_code=agent_code,
        prediction_status=prediction_status,
        selection_side=selection_side,
        win_probability=win_probability,
        probability_representation=representation,
        model_version=model_version,
        feature_version=feature_version,
        config_version=config_version,
        parameter_schema_version=parameter_schema_version,
        agent_config_version_id=0,
        as_of=as_of,
    )


def reconstruct_manager_v1(
    *,
    match_id: int,
    prediction_run_id: int,
    agent1,
    agent2,
    agent3,
    policy: ManagerPolicyV1,
    as_of: datetime,
) -> ManagerConsensusResultV1:
    predictions = (
        build_prediction(
            prediction_id=1,
            prediction_run_id=prediction_run_id,
            match_id=match_id,
            agent_code=AGENT_1,
            prediction_status=agent1.prediction_status,
            selection_side=agent1.selection_side,
            win_probability=agent1.win_probability,
            model_version=agent1.model_version,
            feature_version=agent1.feature_version,
            config_version=agent1.config_version,
            parameter_schema_version=agent1.parameter_schema_version,
            as_of=as_of,
        ),
        build_prediction(
            prediction_id=2,
            prediction_run_id=prediction_run_id,
            match_id=match_id,
            agent_code=AGENT_2,
            prediction_status=agent2.prediction_status,
            selection_side=agent2.selection_side,
            win_probability=agent2.win_probability,
            model_version=agent2.model_version,
            feature_version=agent2.feature_version,
            config_version=agent2.config_version,
            parameter_schema_version=agent2.parameter_schema_version,
            as_of=as_of,
        ),
        build_prediction(
            prediction_id=3,
            prediction_run_id=prediction_run_id,
            match_id=match_id,
            agent_code=AGENT_3,
            prediction_status=agent3.prediction_status,
            selection_side=agent3.selected_side,
            win_probability=agent3.selected_probability,
            model_version=agent3.model_version,
            feature_version=agent3.feature_version,
            config_version=agent3.config_version,
            parameter_schema_version=agent3.parameter_schema_version,
            as_of=as_of,
        ),
    )
    return evaluate_consensus(
        predictions,
        match_id=match_id,
        prediction_run_id=prediction_run_id,
        policy=policy,
        as_of=as_of,
    )


__all__ = ["build_prediction", "reconstruct_manager_v1"]
