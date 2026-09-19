from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from sports_edge_tennis.agents.adapters import (
    adapt_agent1_result,
    adapt_agent2_result,
    adapt_agent3_result,
)
from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as AGENT1_DEFAULT_CONFIG,
    predict_from_replay as predict_agent1,
    replay_history as replay_agent1_history,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG as AGENT2_DEFAULT_CONFIG,
    predict as predict_agent2,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG as AGENT3_DEFAULT_CONFIG,
    predict as predict_agent3,
)
from sports_edge_tennis.data.prediction_persistence import (
    PredictiveAgentMatrixPrediction,
    persist_and_finalize_prediction_matrix,
)
from sports_edge_tennis.manager.contracts import (
    MANAGER_POLICY_KEY,
    SUPPORTED_MANAGER_POLICY_VERSIONS,
)
from sports_edge_tennis.manager.persistence import (
    evaluate_and_persist_manager_consensus,
    load_manager_policy_version,
)
from sports_edge_tennis.runner.contracts import (
    AGENT_1,
    AGENT_2,
    AGENT_3,
    FullSlateRunOutcomeV1,
    FullSlateRunRequestV1,
    ResolvedAgentConfigV1,
    ResolvedTargetV1,
    RunnerExecutionError,
    RunnerLifecycleError,
    RunnerRequestError,
)
from sports_edge_tennis.runner.inputs import (
    build_agent1_request,
    build_agent2_history_snapshot,
    build_agent2_request,
    build_agent3_history_snapshot,
    build_agent3_request,
    load_agent1_history_observations,
)
from sports_edge_tennis.runner.persistence import (
    create_prediction_run,
    is_completed_state,
    is_started_run_failure_state,
    mark_prediction_run_failed,
    persisted_counts,
    resolve_agent_config,
)
from sports_edge_tennis.runner.targets import resolve_targets


DEFAULT_CONFIGS = {
    AGENT_1: AGENT1_DEFAULT_CONFIG,
    AGENT_2: AGENT2_DEFAULT_CONFIG,
    AGENT_3: AGENT3_DEFAULT_CONFIG,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _diagnostic_value(result, key: str) -> str:
    payload = result.diagnostics_payload or {}
    value = payload.get(key)
    return "-" if value is None else str(value)


def _status_reason(result) -> str:
    return f"{result.prediction_status}:{result.abstain_reason or 'NONE'}"


def _log_target_diagnostics(match_id: int, agent1, agent2, agent3) -> None:
    """Emit one bounded line explaining model-history eligibility for a live target."""
    print(
        "AGENT_DIAGNOSTIC "
        f"match_id={match_id} "
        f"a1={_status_reason(agent1)} "
        f"a1_overall={_diagnostic_value(agent1, 'player_1_overall_matches')}/"
        f"{_diagnostic_value(agent1, 'player_2_overall_matches')} "
        f"a1_surface={_diagnostic_value(agent1, 'player_1_surface_matches')}/"
        f"{_diagnostic_value(agent1, 'player_2_surface_matches')} "
        f"a2={_status_reason(agent2)} "
        f"a2_usable={_diagnostic_value(agent2, 'player_1_prior_usable_matches')}/"
        f"{_diagnostic_value(agent2, 'player_2_prior_usable_matches')} "
        f"a2_serve={_diagnostic_value(agent2, 'player_1_overall_serve_points')}/"
        f"{_diagnostic_value(agent2, 'player_2_overall_serve_points')} "
        f"a2_return={_diagnostic_value(agent2, 'player_1_overall_return_points')}/"
        f"{_diagnostic_value(agent2, 'player_2_overall_return_points')} "
        f"a2_surface_serve={_diagnostic_value(agent2, 'player_1_surface_serve_points')}/"
        f"{_diagnostic_value(agent2, 'player_2_surface_serve_points')} "
        f"a2_surface_return={_diagnostic_value(agent2, 'player_1_surface_return_points')}/"
        f"{_diagnostic_value(agent2, 'player_2_surface_return_points')} "
        f"a3={_status_reason(agent3)} "
        f"a3_direct_all={_diagnostic_value(agent3, 'direct_raw_count_all')} "
        f"a3_direct_surface={_diagnostic_value(agent3, 'direct_raw_count_surface')} "
        f"a3_shared_all={_diagnostic_value(agent3, 'network_shared_opponents_all')} "
        f"a3_q_all={_diagnostic_value(agent3, 'network_q_all')} "
        f"a3_shared_surface={_diagnostic_value(agent3, 'network_shared_opponents_surface')} "
        f"a3_q_surface={_diagnostic_value(agent3, 'network_q_surface')} "
        f"a3_mode={_diagnostic_value(agent3, 'activation_mode')}",
        flush=True,
    )


def run_full_slate(
    conn: psycopg.Connection,
    request: FullSlateRunRequestV1,
    *,
    clock: callable | None = None,
) -> FullSlateRunOutcomeV1:
    clock = clock or _utc_now
    execution_now = clock()

    targets, configs = _preflight(conn, request, execution_now)

    run_id = create_prediction_run(conn, request=request, started_at=execution_now)

    try:
        matrix = _execute_matrix(conn, targets, configs, request.as_of)
    except Exception as exc:
        return _fail_run(conn, run_id, request.as_of, clock, "AGENT_EXECUTION", exc)

    finalized_at = clock()
    ended_at = clock()
    if finalized_at < execution_now or finalized_at < request.as_of:
        raise RunnerLifecycleError("prediction_finalized_at violates timestamp ordering")
    if ended_at < finalized_at:
        raise RunnerLifecycleError("ended_at must be >= prediction_finalized_at")

    try:
        persist_and_finalize_prediction_matrix(
            conn,
            prediction_run_id=run_id,
            rows=matrix,
            prediction_finalized_at=finalized_at,
            ended_at=ended_at,
        )
    except Exception as exc:
        try:
            if is_started_run_failure_state(conn, prediction_run_id=run_id):
                return _fail_run(conn, run_id, request.as_of, clock, "PREDICTION_PERSISTENCE", exc)
            if is_completed_state(conn, prediction_run_id=run_id):
                pass
            else:
                raise RunnerLifecycleError(
                    "prediction-run state is inconsistent after finalization failure"
                )
        except RunnerLifecycleError:
            raise
        except Exception as read_exc:
            raise RunnerLifecycleError(
                "unable to determine authoritative prediction-run state after finalization failure"
            ) from read_exc

    return _manager_handoff(conn, run_id, request, targets)


def _preflight(
    conn: psycopg.Connection,
    request: FullSlateRunRequestV1,
    execution_now: datetime,
) -> tuple[list[ResolvedTargetV1], dict[str, ResolvedAgentConfigV1]]:
    if not isinstance(request.as_of, datetime) or request.as_of.tzinfo is None:
        raise RunnerRequestError("request.as_of must be timezone-aware")
    if request.as_of > execution_now:
        raise RunnerRequestError("request.as_of must not be in the future")

    if len(set(request.match_ids)) != len(request.match_ids):
        raise RunnerRequestError("request.match_ids must not contain duplicates")

    if (request.parent_prediction_run_id is None) != (request.parent_relation_type is None):
        raise RunnerRequestError("parent lineage fields must both be set or both be None")

    codes = [ref.agent_code for ref in request.agent_config_refs]
    if len(codes) != 3 or set(codes) != {AGENT_1, AGENT_2, AGENT_3}:
        raise RunnerRequestError(
            "request must supply exactly one config ref for each canonical agent"
        )

    targets = resolve_targets(conn, match_ids=request.match_ids, as_of=request.as_of)

    configs: dict[str, ResolvedAgentConfigV1] = {}
    for ref in request.agent_config_refs:
        configs[ref.agent_code] = resolve_agent_config(
            conn,
            agent_code=ref.agent_code,
            config_version=ref.config_version,
            default_config=DEFAULT_CONFIGS[ref.agent_code],
        )

    try:
        policy = load_manager_policy_version(
            conn,
            manager_policy_version_id=request.manager_policy_version_id,
        )
    except Exception as exc:
        raise RunnerRequestError(f"invalid manager policy reference: {exc}") from exc
    if policy.policy_key != MANAGER_POLICY_KEY or policy.policy_version not in SUPPORTED_MANAGER_POLICY_VERSIONS:
        raise RunnerRequestError(
            "manager policy must be a supported tennis_manager_consensus version"
        )

    _validate_lineage(conn, request)

    return targets, configs


def _validate_lineage(conn: psycopg.Connection, request: FullSlateRunRequestV1) -> None:
    if request.parent_prediction_run_id is None:
        return
    if request.parent_relation_type not in ("RETRY", "RECALCULATION"):
        raise RunnerRequestError("parent_relation_type must be RETRY or RECALCULATION")
    parent = conn.execute(
        "SELECT run_status FROM prediction_runs WHERE prediction_run_id = %s",
        (request.parent_prediction_run_id,),
    ).fetchone()
    if parent is None:
        raise RunnerRequestError("parent_prediction_run_id must reference an existing run")
    parent_status = parent[0]
    if request.parent_relation_type == "RETRY":
        if parent_status not in ("FAILED", "CANCELLED"):
            raise RunnerRequestError("RETRY parent must be FAILED or CANCELLED")
    else:
        if parent_status not in ("COMPLETED", "FAILED", "CANCELLED"):
            raise RunnerRequestError("RECALCULATION parent must be a terminal run")


def _execute_matrix(
    conn: psycopg.Connection,
    targets: list[ResolvedTargetV1],
    configs: dict[str, ResolvedAgentConfigV1],
    as_of: datetime,
) -> list[PredictiveAgentMatrixPrediction]:
    agent1_history = load_agent1_history_observations(conn, as_of)
    agent1_replay = replay_agent1_history(agent1_history, as_of, AGENT1_DEFAULT_CONFIG)
    agent2_snapshot = build_agent2_history_snapshot(conn, as_of)
    agent3_snapshot = build_agent3_history_snapshot(conn, as_of)

    matrix: list[PredictiveAgentMatrixPrediction] = []
    for target in targets:
        agent1 = _run_agent1(target, agent1_replay, configs[AGENT_1], as_of)
        agent2 = _run_agent2(target, agent2_snapshot, configs[AGENT_2], as_of)
        agent3 = _run_agent3(target, agent3_snapshot, configs[AGENT_3], as_of)
        matrix.extend((agent1, agent2, agent3))
        _log_target_diagnostics(
            target.match_id,
            agent1.result,
            agent2.result,
            agent3.result,
        )
    return matrix


def _run_agent1(target, replay, config, as_of) -> PredictiveAgentMatrixPrediction:
    request = build_agent1_request(target, as_of)
    result = predict_agent1(request, replay, config=AGENT1_DEFAULT_CONFIG)
    adapted = adapt_agent1_result(result, config=AGENT1_DEFAULT_CONFIG)
    _validate_result(adapted, AGENT_1, config, as_of)
    return PredictiveAgentMatrixPrediction(
        match_id=target.match_id,
        agent_config_version_id=config.agent_config_version_id,
        result=adapted,
    )


def _run_agent2(target, snapshot, config, as_of) -> PredictiveAgentMatrixPrediction:
    request = build_agent2_request(target, snapshot, as_of)
    result = predict_agent2(request, config=AGENT2_DEFAULT_CONFIG)
    adapted = adapt_agent2_result(result)
    _validate_result(adapted, AGENT_2, config, as_of)
    return PredictiveAgentMatrixPrediction(
        match_id=target.match_id,
        agent_config_version_id=config.agent_config_version_id,
        result=adapted,
    )


def _run_agent3(target, snapshot, config, as_of) -> PredictiveAgentMatrixPrediction:
    request = build_agent3_request(target, snapshot, as_of)
    result = predict_agent3(request, config=AGENT3_DEFAULT_CONFIG)
    adapted = adapt_agent3_result(result)
    _validate_result(adapted, AGENT_3, config, as_of)
    return PredictiveAgentMatrixPrediction(
        match_id=target.match_id,
        agent_config_version_id=config.agent_config_version_id,
        result=adapted,
    )


def _validate_result(
    result,
    agent_code: str,
    config: ResolvedAgentConfigV1,
    as_of: datetime,
) -> None:
    if result.agent_code != agent_code:
        raise RunnerExecutionError(
            f"adapted result agent_code {result.agent_code!r} != expected {agent_code!r}"
        )
    if result.as_of != as_of:
        raise RunnerExecutionError("adapted result as_of does not match the prediction run as_of")
    if result.config_version != config.config_version:
        raise RunnerExecutionError("adapted result config_version does not match resolved config")
    if result.model_version != config.model_version:
        raise RunnerExecutionError("adapted result model_version does not match resolved config")
    if result.feature_version != config.feature_version:
        raise RunnerExecutionError("adapted result feature_version does not match resolved config")
    if result.parameter_schema_version != config.parameter_schema_version:
        raise RunnerExecutionError(
            "adapted result parameter_schema_version does not match resolved config"
        )


def _fail_run(
    conn: psycopg.Connection,
    run_id: int,
    as_of: datetime,
    clock: callable,
    stage: str,
    exc: Exception,
) -> FullSlateRunOutcomeV1:
    if is_started_run_failure_state(conn, prediction_run_id=run_id):
        mark_prediction_run_failed(conn, prediction_run_id=run_id, ended_at=clock())
    counts = persisted_counts(conn, prediction_run_id=run_id)
    return FullSlateRunOutcomeV1(
        prediction_run_id=run_id,
        runner_status="PREDICTION_FAILED",
        target_count=counts.target_count,
        prediction_result_count=counts.prediction_count,
        manager_result_count=counts.manager_count,
        as_of=as_of,
        failure_stage=stage,
        failure_reason=_reason(exc),
    )


def _manager_handoff(
    conn: psycopg.Connection,
    run_id: int,
    request: FullSlateRunRequestV1,
    targets: list[ResolvedTargetV1],
) -> FullSlateRunOutcomeV1:
    try:
        evaluate_and_persist_manager_consensus(
            conn,
            prediction_run_id=run_id,
            manager_policy_version_id=request.manager_policy_version_id,
        )
    except Exception as exc:
        counts = persisted_counts(conn, prediction_run_id=run_id)
        return FullSlateRunOutcomeV1(
            prediction_run_id=run_id,
            runner_status="MANAGER_FAILED",
            target_count=counts.target_count,
            prediction_result_count=counts.prediction_count,
            manager_result_count=counts.manager_count,
            as_of=request.as_of,
            failure_stage="MANAGER",
            failure_reason=_reason(exc),
        )

    counts = persisted_counts(conn, prediction_run_id=run_id)

    if targets:
        if counts.prediction_count != counts.target_count * 3:
            raise RunnerLifecycleError("prediction count does not reconcile with target count")
        if counts.manager_count != counts.target_count:
            raise RunnerLifecycleError("manager result count does not reconcile with target count")

    status = "EMPTY_SLATE" if not targets else "COMPLETED"
    return FullSlateRunOutcomeV1(
        prediction_run_id=run_id,
        runner_status=status,
        target_count=counts.target_count,
        prediction_result_count=counts.prediction_count,
        manager_result_count=counts.manager_count,
        as_of=request.as_of,
    )


def _reason(exc: Exception) -> str:
    return str(exc) or type(exc).__name__
