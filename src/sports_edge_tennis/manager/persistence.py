from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import psycopg

from sports_edge_tennis.data.prediction_persistence import (
    load_finalized_predictive_agent_predictions,
)
from sports_edge_tennis.manager.consensus import evaluate_consensus
from sports_edge_tennis.manager.contracts import (
    MANAGER_POLICY_KEY,
    MANAGER_POLICY_VERSION,
    ManagerConsensusError,
    ManagerConsensusResultV1,
    ManagerIdempotencyMismatchError,
    ManagerPartialSlateError,
    ManagerPolicyV1,
)


@dataclass(frozen=True)
class ManagerSlateOutcome:
    prediction_run_id: int
    target_count: int
    status: Literal["PERSISTED", "IDEMPOTENT_SUCCESS", "EMPTY_SLATE"]


def load_manager_policy_version(
    conn: psycopg.Connection,
    *,
    manager_policy_version_id: int,
) -> ManagerPolicyV1:
    row = conn.execute(
        """
        SELECT
            manager_policy_version_id,
            policy_key,
            policy_version,
            three_agent_max_probability_spread,
            agent3_abstention_max_probability_spread,
            min_active_agent_probability
        FROM manager_policy_versions
        WHERE manager_policy_version_id = %s
        """,
        (manager_policy_version_id,),
    ).fetchone()
    if row is None:
        raise ManagerConsensusError(
            "manager_policy_version_id must reference an existing Manager policy version"
        )
    return ManagerPolicyV1(*row)


def ensure_manager_policy_version_v1(conn: psycopg.Connection) -> ManagerPolicyV1:
    """Insert the frozen v1 Manager policy if absent and return it.

    Idempotent reference-data seeding: ON CONFLICT DO NOTHING is used only to
    guarantee a single frozen policy row exists, never to mask Manager truth.
    """
    conn.execute(
        """
        INSERT INTO manager_policy_versions (
            policy_key,
            policy_version,
            consensus_mode,
            allow_agent3_abstention,
            three_agent_max_probability_spread,
            agent3_abstention_max_probability_spread,
            min_active_agent_probability
        )
        VALUES (%s, %s, 'UNANIMOUS_ACTIVE_AGENTS', TRUE, 0.080000, 0.060000, 0.500000)
        ON CONFLICT (policy_key, policy_version) DO NOTHING
        """,
        (MANAGER_POLICY_KEY, MANAGER_POLICY_VERSION),
    )
    row = conn.execute(
        """
        SELECT
            manager_policy_version_id,
            policy_key,
            policy_version,
            three_agent_max_probability_spread,
            agent3_abstention_max_probability_spread,
            min_active_agent_probability
        FROM manager_policy_versions
        WHERE policy_key = %s AND policy_version = %s
        """,
        (MANAGER_POLICY_KEY, MANAGER_POLICY_VERSION),
    ).fetchone()
    if row is None:
        raise ManagerConsensusError("failed to resolve the v1 Manager policy version")
    return ManagerPolicyV1(*row)


def evaluate_and_persist_manager_consensus(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    manager_policy_version_id: int,
) -> ManagerSlateOutcome:
    with conn.transaction():
        parent = conn.execute(
            """
            SELECT run_status, prediction_finalized_at, prediction_contract_version, as_of
            FROM prediction_runs
            WHERE prediction_run_id = %s
            FOR UPDATE
            """,
            (prediction_run_id,),
        ).fetchone()
        if parent is None:
            raise ManagerConsensusError("prediction_run_id must reference an existing run")
        run_status, finalized_at, contract_version, run_as_of = parent
        if contract_version != "predictive_agent_result_v1":
            raise ManagerConsensusError(
                "Manager consumes only predictive_agent_result_v1 prediction runs"
            )
        if run_status != "COMPLETED":
            raise ManagerConsensusError("Manager consumes only COMPLETED prediction runs")
        if finalized_at is None:
            raise ManagerConsensusError("Manager consumes only finalized prediction runs")

        policy = load_manager_policy_version(
            conn,
            manager_policy_version_id=manager_policy_version_id,
        )

        targets = [
            row[0]
            for row in conn.execute(
                """
                SELECT match_id
                FROM prediction_run_targets
                WHERE prediction_run_id = %s
                ORDER BY match_id
                """,
                (prediction_run_id,),
            ).fetchall()
        ]

        if not targets:
            return ManagerSlateOutcome(
                prediction_run_id=prediction_run_id,
                target_count=0,
                status="EMPTY_SLATE",
            )

        predictions = load_finalized_predictive_agent_predictions(
            conn,
            prediction_run_id=prediction_run_id,
        )
        predictions_by_match: dict[int, list] = {}
        for prediction in predictions:
            predictions_by_match.setdefault(prediction.match_id, []).append(prediction)

        canonical_slate: list[ManagerConsensusResultV1] = []
        for match_id in targets:
            if match_id not in predictions_by_match:
                raise ManagerConsensusError(
                    f"target match {match_id} is missing persisted predictions"
                )
            if len(predictions_by_match[match_id]) != 3:
                raise ManagerConsensusError(
                    f"target match {match_id} does not have exactly three canonical predictions"
                )
            canonical_slate.append(
                evaluate_consensus(
                    predictions_by_match[match_id],
                    match_id=match_id,
                    prediction_run_id=prediction_run_id,
                    policy=policy,
                    as_of=run_as_of,
                )
            )

        if set(predictions_by_match) != set(targets):
            raise ManagerConsensusError(
                "persisted predictions exist outside the run's prediction_run_targets"
            )

        existing = _load_existing_results(conn, prediction_run_id)
        target_count = len(targets)
        existing_count = len(existing)

        if existing_count == 0:
            _persist_slate(conn, canonical_slate)
            persisted_count = _count_persisted_results(conn, prediction_run_id)
            if persisted_count != target_count:
                raise ManagerConsensusError(
                    "Manager slate persistence produced an unexpected row count "
                    f"({persisted_count} != {target_count})"
                )
            return ManagerSlateOutcome(
                prediction_run_id=prediction_run_id,
                target_count=target_count,
                status="PERSISTED",
            )

        if existing_count == target_count:
            by_match = {result.match_id: result for result in existing}
            for computed in canonical_slate:
                persisted = by_match.get(computed.match_id)
                if persisted is None or not _results_semantically_equal(persisted, computed):
                    raise ManagerIdempotencyMismatchError(
                        "persisted Manager truth differs from the computed canonical slate"
                    )
            return ManagerSlateOutcome(
                prediction_run_id=prediction_run_id,
                target_count=target_count,
                status="IDEMPOTENT_SUCCESS",
            )

        if existing_count < target_count:
            raise ManagerPartialSlateError(
                "partial Manager slate detected; refusing to repair or append"
            )

        raise ManagerConsensusError(
            "Manager result count exceeds the prediction run target count"
        )


def _load_existing_results(
    conn: psycopg.Connection,
    prediction_run_id: int,
) -> list[ManagerConsensusResultV1]:
    rows = conn.execute(
        """
        SELECT
            prediction_run_id,
            match_id,
            manager_policy_version_id,
            manager_status::text,
            selected_side,
            consensus_probability,
            active_agent_count,
            abstaining_agent_count,
            probability_spread,
            reason_code::text,
            as_of
        FROM manager_consensus_results
        WHERE prediction_run_id = %s
        ORDER BY match_id
        """,
        (prediction_run_id,),
    ).fetchall()
    return [ManagerConsensusResultV1(*row) for row in rows]


def _count_persisted_results(conn: psycopg.Connection, prediction_run_id: int) -> int:
    return conn.execute(
        "SELECT count(*) FROM manager_consensus_results WHERE prediction_run_id = %s",
        (prediction_run_id,),
    ).fetchone()[0]


def _persist_slate(
    conn: psycopg.Connection,
    results: list[ManagerConsensusResultV1],
) -> None:
    for result in results:
        conn.execute(
            """
            INSERT INTO manager_consensus_results (
                prediction_run_id,
                match_id,
                manager_policy_version_id,
                manager_status,
                selected_side,
                consensus_probability,
                active_agent_count,
                abstaining_agent_count,
                probability_spread,
                reason_code,
                as_of
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                result.prediction_run_id,
                result.match_id,
                result.manager_policy_version_id,
                result.manager_status,
                result.selected_side,
                result.consensus_probability,
                result.active_agent_count,
                result.abstaining_agent_count,
                result.probability_spread,
                result.reason_code,
                result.as_of,
            ),
        )


def _nullsafe_eq(left: object, right: object) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return left == right


def _results_semantically_equal(
    left: ManagerConsensusResultV1,
    right: ManagerConsensusResultV1,
) -> bool:
    return (
        left.prediction_run_id == right.prediction_run_id
        and left.match_id == right.match_id
        and left.manager_policy_version_id == right.manager_policy_version_id
        and left.manager_status == right.manager_status
        and _nullsafe_eq(left.selected_side, right.selected_side)
        and _nullsafe_eq(left.consensus_probability, right.consensus_probability)
        and left.active_agent_count == right.active_agent_count
        and left.abstaining_agent_count == right.abstaining_agent_count
        and _nullsafe_eq(left.probability_spread, right.probability_spread)
        and left.reason_code == right.reason_code
        and left.as_of == right.as_of
    )
