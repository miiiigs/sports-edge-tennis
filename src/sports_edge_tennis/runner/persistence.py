from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from sports_edge_tennis.runner.contracts import (
    FullSlateRunRequestV1,
    ResolvedAgentConfigV1,
    RunnerLifecycleError,
    RunnerRequestError,
)


@dataclass(frozen=True)
class RunnerCounts:
    target_count: int
    prediction_count: int
    manager_count: int


def resolve_agent_config(
    conn: psycopg.Connection,
    *,
    agent_code: str,
    config_version: str,
    default_config: Any,
) -> ResolvedAgentConfigV1:
    row = conn.execute(
        """
        SELECT
            agent_config_version_id,
            agent_code::text,
            config_version,
            model_version,
            feature_version,
            calibration_version,
            parameter_schema_version,
            config_payload
        FROM agent_config_versions
        WHERE agent_code = %s AND config_version = %s
        """,
        (agent_code, config_version),
    ).fetchone()

    if row is None:
        raise RunnerRequestError(
            f"no agent_config_versions row for ({agent_code}, {config_version})"
        )

    (
        config_id,
        row_agent_code,
        row_config_version,
        model_version,
        feature_version,
        calibration_version,
        parameter_schema_version,
        config_payload,
    ) = row

    expected_payload = default_config.to_payload()

    if (
        row_agent_code != agent_code
        or row_config_version != default_config.config_version
        or model_version != default_config.model_version
        or feature_version != default_config.feature_version
        or parameter_schema_version != default_config.parameter_schema_version
    ):
        raise RunnerRequestError(
            f"agent config ({agent_code}, {config_version}) typed lineage does not match "
            "the frozen DEFAULT_CONFIG"
        )

    if calibration_version is not None:
        raise RunnerRequestError(
            f"agent config ({agent_code}, {config_version}) has non-NULL calibration_version"
        )

    if not _jsonb_equals(config_payload, expected_payload):
        raise RunnerRequestError(
            f"agent config ({agent_code}, {config_version}) config_payload does not match "
            "the frozen DEFAULT_CONFIG payload"
        )

    return ResolvedAgentConfigV1(
        agent_code=agent_code,
        agent_config_version_id=config_id,
        config_version=default_config.config_version,
        model_version=default_config.model_version,
        feature_version=default_config.feature_version,
        parameter_schema_version=default_config.parameter_schema_version,
    )


def create_prediction_run(
    conn: psycopg.Connection,
    *,
    request: FullSlateRunRequestV1,
    started_at: datetime,
) -> int:
    with conn.transaction():
        run_id = conn.execute(
            """
            INSERT INTO prediction_runs (
                run_status,
                as_of,
                started_at,
                prediction_finalized_at,
                ended_at,
                experiment_id,
                parent_prediction_run_id,
                parent_relation_type,
                prediction_contract_version
            )
            VALUES (
                'RUNNING',
                %s,
                %s,
                NULL,
                NULL,
                %s,
                %s,
                %s,
                'predictive_agent_result_v1'
            )
            RETURNING prediction_run_id
            """,
            (
                request.as_of,
                started_at,
                request.experiment_id,
                request.parent_prediction_run_id,
                request.parent_relation_type,
            ),
        ).fetchone()[0]

        for match_id in request.match_ids:
            conn.execute(
                "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
                (run_id, match_id),
            )
    return run_id

def mark_prediction_run_failed(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    ended_at: datetime,
) -> None:
    with conn.transaction():
        row = conn.execute(
            """
            SELECT run_status, prediction_finalized_at, started_at
            FROM prediction_runs
            WHERE prediction_run_id = %s
            FOR UPDATE
            """,
            (prediction_run_id,),
        ).fetchone()
        if row is None:
            raise RunnerLifecycleError("prediction_run_id must reference an existing run")
        run_status, finalized_at, started_at = row
        if run_status != "RUNNING":
            raise RunnerLifecycleError("only a RUNNING run can be marked FAILED")
        if finalized_at is not None:
            raise RunnerLifecycleError("cannot mark a finalized run FAILED")
        if ended_at < started_at:
            raise RunnerLifecycleError("failure ended_at must be >= started_at")

        prediction_count = conn.execute(
            "SELECT count(*) FROM predictions WHERE prediction_run_id = %s",
            (prediction_run_id,),
        ).fetchone()[0]
        if prediction_count != 0:
            raise RunnerLifecycleError(
                "cannot mark a run with committed predictions FAILED"
            )

        conn.execute(
            "UPDATE prediction_runs SET run_status = 'FAILED', ended_at = %s WHERE prediction_run_id = %s",
            (ended_at, prediction_run_id),
        )


def read_run_state(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
) -> tuple[str, datetime | None]:
    row = conn.execute(
        "SELECT run_status, prediction_finalized_at FROM prediction_runs WHERE prediction_run_id = %s",
        (prediction_run_id,),
    ).fetchone()
    if row is None:
        raise RunnerLifecycleError("prediction_run_id must reference an existing run")
    return row[0], row[1]


def is_started_run_failure_state(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
) -> bool:
    run_status, finalized_at = read_run_state(conn, prediction_run_id=prediction_run_id)
    if run_status != "RUNNING" or finalized_at is not None:
        return False
    prediction_count = conn.execute(
        "SELECT count(*) FROM predictions WHERE prediction_run_id = %s",
        (prediction_run_id,),
    ).fetchone()[0]
    return prediction_count == 0


def is_completed_state(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
) -> bool:
    run_status, finalized_at = read_run_state(conn, prediction_run_id=prediction_run_id)
    return run_status == "COMPLETED" and finalized_at is not None


def persisted_counts(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
) -> RunnerCounts:
    target_count = conn.execute(
        "SELECT count(*) FROM prediction_run_targets WHERE prediction_run_id = %s",
        (prediction_run_id,),
    ).fetchone()[0]
    prediction_count = conn.execute(
        "SELECT count(*) FROM predictions WHERE prediction_run_id = %s",
        (prediction_run_id,),
    ).fetchone()[0]
    manager_count = conn.execute(
        "SELECT count(*) FROM manager_consensus_results WHERE prediction_run_id = %s",
        (prediction_run_id,),
    ).fetchone()[0]
    return RunnerCounts(
        target_count=target_count,
        prediction_count=prediction_count,
        manager_count=manager_count,
    )


def _jsonb_equals(actual: Any, expected: Any) -> bool:
    if isinstance(actual, dict) and isinstance(expected, dict):
        if set(actual.keys()) != set(expected.keys()):
            return False
        return all(_jsonb_equals(actual[key], expected[key]) for key in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _jsonb_equals(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected
