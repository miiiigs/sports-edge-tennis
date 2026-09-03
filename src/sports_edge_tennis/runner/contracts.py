from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


AGENT_1 = "AGENT_1_SURFACE_ELO"
AGENT_2 = "AGENT_2_HIERARCHICAL_MARKOV"
AGENT_3 = "AGENT_3_INTRANSITIVITY"

CANONICAL_AGENT_CODES: tuple[str, ...] = (AGENT_1, AGENT_2, AGENT_3)

RunnerStatus = Literal[
    "COMPLETED",
    "PREDICTION_FAILED",
    "MANAGER_FAILED",
    "EMPTY_SLATE",
]

RunnerFailureStage = Literal[
    "NONE",
    "TARGET_RESOLUTION",
    "AGENT_EXECUTION",
    "PREDICTION_PERSISTENCE",
    "MANAGER",
]

ParentRelationType = Literal["RETRY", "RECALCULATION"]


class RunnerError(Exception):
    """Base error for the full-slate execution runner."""


class RunnerRequestError(RunnerError):
    """A deterministic request/preflight defect that prevents run creation."""


class TargetResolutionError(RunnerRequestError):
    """A target cannot be resolved to a PIT-visible SCHEDULED match version."""


class RunnerExecutionError(RunnerError):
    """A started-run execution failure (Agent/input/matrix) after run creation."""


class RunnerLifecycleError(RunnerError):
    """A prediction-run lifecycle invariant was violated (fail closed)."""


@dataclass(frozen=True)
class AgentConfigRefV1:
    agent_code: str
    config_version: str


@dataclass(frozen=True)
class FullSlateRunRequestV1:
    match_ids: tuple[int, ...]
    as_of: datetime
    agent_config_refs: tuple[AgentConfigRefV1, ...]
    manager_policy_version_id: int
    experiment_id: str | None = None
    parent_prediction_run_id: int | None = None
    parent_relation_type: ParentRelationType | None = None


@dataclass(frozen=True)
class ResolvedTargetV1:
    match_id: int
    match_version_id: int
    player_1_id: int
    player_2_id: int
    surface: str | None
    tour_scope: str
    competition_tier: str
    round_code: str | None
    best_of_sets: int
    qualifying_flag: bool
    match_status: str
    canonical_tournament_name: str
    normalized_tournament_alias: str
    season_year: int


@dataclass(frozen=True)
class ResolvedAgentConfigV1:
    agent_code: str
    agent_config_version_id: int
    config_version: str
    model_version: str
    feature_version: str
    parameter_schema_version: str


@dataclass(frozen=True)
class FullSlateRunOutcomeV1:
    prediction_run_id: int | None
    runner_status: RunnerStatus
    target_count: int
    prediction_result_count: int
    manager_result_count: int
    as_of: datetime
    failure_stage: RunnerFailureStage = "NONE"
    failure_reason: str | None = None
