from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Sequence

import psycopg

from sports_edge_tennis.agents.contracts import (
    PREDICTIVE_AGENT_CODES,
    PredictiveAgentContractError,
    PredictiveAgentResultV1,
)


@dataclass(frozen=True)
class PersistedPredictiveAgentPrediction:
    prediction_id: int
    prediction_run_id: int
    match_id: int
    agent_code: str
    prediction_status: str
    selection_side: int | None
    win_probability: Decimal | None
    probability_representation: str | None
    model_version: str
    feature_version: str
    config_version: str
    parameter_schema_version: str
    agent_config_version_id: int
    as_of: datetime


@dataclass(frozen=True)
class PredictiveAgentMatrixPrediction:
    match_id: int
    agent_config_version_id: int
    result: PredictiveAgentResultV1
    experiment_id: str | None = None
    walk_forward_fold: int | None = None
    variant_id: str | None = None


def insert_prediction_run_targets(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    match_ids: Iterable[int],
) -> None:
    rows = [(prediction_run_id, match_id) for match_id in match_ids]
    if not rows:
        return
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO prediction_run_targets (prediction_run_id, match_id)
            VALUES (%s, %s)
            """,
            rows,
        )


def persist_predictive_agent_result(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    match_id: int,
    agent_config_version_id: int,
    result: PredictiveAgentResultV1,
    prediction_locked_at: datetime,
    experiment_id: str | None = None,
    walk_forward_fold: int | None = None,
    variant_id: str | None = None,
) -> int:
    if result.agent_code not in PREDICTIVE_AGENT_CODES:
        raise PredictiveAgentContractError("result.agent_code must be a predictive agent")
    return conn.execute(
        """
        INSERT INTO predictions (
            match_id,
            prediction_run_id,
            agent_config_version_id,
            agent_code,
            prediction_status,
            selection_side,
            win_probability,
            probability_representation,
            model_version,
            feature_version,
            experiment_id,
            walk_forward_fold,
            variant_id,
            as_of,
            prediction_locked_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s::numeric, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        RETURNING prediction_id
        """,
        (
            match_id,
            prediction_run_id,
            agent_config_version_id,
            result.agent_code,
            result.prediction_status,
            result.selected_side,
            result.db_probability,
            result.probability_representation,
            result.model_version,
            result.feature_version,
            experiment_id,
            walk_forward_fold,
            variant_id,
            result.as_of,
            prediction_locked_at,
        ),
    ).fetchone()[0]


def persist_and_finalize_prediction_matrix(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    rows: Sequence[PredictiveAgentMatrixPrediction],
    prediction_finalized_at: datetime,
    ended_at: datetime,
) -> list[int]:
    inserted_ids: list[int] = []
    with conn.transaction():
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        parent = conn.execute(
            """
            SELECT run_status, prediction_finalized_at, prediction_contract_version
            FROM prediction_runs
            WHERE prediction_run_id = %s
            FOR UPDATE
            """,
            (prediction_run_id,),
        ).fetchone()
        if parent is None:
            raise PredictiveAgentContractError("prediction_run_id must reference an existing run")
        run_status, existing_finalized_at, contract_version = parent
        if (
            run_status != "RUNNING"
            or existing_finalized_at is not None
            or contract_version != "predictive_agent_result_v1"
        ):
            raise PredictiveAgentContractError(
                "predictive_agent_result_v1 matrix persistence requires a RUNNING, unfinalized run"
            )

        for row in rows:
            inserted_ids.append(
                persist_predictive_agent_result(
                    conn,
                    prediction_run_id=prediction_run_id,
                    match_id=row.match_id,
                    agent_config_version_id=row.agent_config_version_id,
                    result=row.result,
                    prediction_locked_at=prediction_finalized_at,
                    experiment_id=row.experiment_id,
                    walk_forward_fold=row.walk_forward_fold,
                    variant_id=row.variant_id,
                )
            )

        conn.execute(
            """
            UPDATE prediction_runs
            SET run_status = 'COMPLETED',
                prediction_finalized_at = %s,
                ended_at = %s
            WHERE prediction_run_id = %s
            """,
            (prediction_finalized_at, ended_at, prediction_run_id),
        )
    return inserted_ids


def load_finalized_predictive_agent_predictions(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    include_legacy: bool = False,
) -> list[PersistedPredictiveAgentPrediction]:
    contract_filter = (
        "pr.prediction_contract_version IN ('predictive_agent_result_v1', 'legacy_prediction_ledger_v1')"
        if include_legacy
        else "pr.prediction_contract_version = 'predictive_agent_result_v1'"
    )
    rows = conn.execute(
        f"""
        SELECT
            p.prediction_id,
            p.prediction_run_id,
            p.match_id,
            p.agent_code::text,
            p.prediction_status::text,
            p.selection_side,
            p.win_probability,
            p.probability_representation,
            p.model_version,
            p.feature_version,
            acv.config_version,
            acv.parameter_schema_version,
            p.agent_config_version_id,
            p.as_of
        FROM predictions p
        JOIN prediction_runs pr
          ON pr.prediction_run_id = p.prediction_run_id
        JOIN agent_config_versions acv
          ON acv.agent_config_version_id = p.agent_config_version_id
        WHERE p.prediction_run_id = %s
          AND pr.run_status = 'COMPLETED'
          AND pr.prediction_finalized_at IS NOT NULL
          AND {contract_filter}
        ORDER BY p.match_id, p.agent_code
        """,
        (prediction_run_id,),
    ).fetchall()
    return [PersistedPredictiveAgentPrediction(*row) for row in rows]
