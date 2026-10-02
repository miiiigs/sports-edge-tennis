"""Abstention reasons persist for new rows and stay NULL for legacy/historical rows."""

from __future__ import annotations

import psycopg
import pytest

from sports_edge_tennis.agents.contracts import PredictiveAgentContractError
from sports_edge_tennis.data.prediction_persistence import (
    load_finalized_predictive_agent_predictions,
    persist_predictive_agent_result,
)
from tests.abstention_helpers import (
    A1,
    A2,
    A3,
    finalize_matrix,
    insert_match,
    insert_run,
    insert_target,
    make_result,
    raw_insert_and_finalize,
    seed_agent_configs,
    utc,
)


def _setup(db: psycopg.Connection):
    match_id = insert_match(db)
    run_id = insert_run(db, as_of=utc())
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    return match_id, run_id, configs


def _reasons(db: psycopg.Connection, run_id: int) -> dict[str, str | None]:
    rows = db.execute(
        """
        SELECT agent_code::text, abstain_reason_code
        FROM predictions
        WHERE prediction_run_id = %s
        ORDER BY agent_code
        """,
        (run_id,),
    ).fetchall()
    return {str(agent): reason for agent, reason in rows}


def test_predicted_rows_store_null_and_abstained_rows_store_reason(db: psycopg.Connection):
    match_id, run_id, configs = _setup(db)
    finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=(
            make_result(A1, utc(), status="PREDICTED", side=1, probability="0.700000"),
            make_result(
                A2, utc(), status="ABSTAINED", reason="INSUFFICIENT_HISTORY"
            ),
            make_result(
                A3, utc(), status="ABSTAINED", reason="INSUFFICIENT_MATCHUP_SUPPORT"
            ),
        ),
        finalized_at=utc(),
    )

    reasons = _reasons(db, run_id)
    assert reasons[A1] is None
    assert reasons[A2] == "INSUFFICIENT_HISTORY"
    assert reasons[A3] == "INSUFFICIENT_MATCHUP_SUPPORT"


def test_load_returns_abstain_reason_code(db: psycopg.Connection):
    match_id, run_id, configs = _setup(db)
    finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=(
            make_result(A1, utc(), status="PREDICTED", side=2, probability="0.640000"),
            make_result(A2, utc(), status="ABSTAINED", reason="INSUFFICIENT_POINT_HISTORY"),
            make_result(A3, utc(), status="ABSTAINED", reason="WEAK_SIGNAL"),
        ),
        finalized_at=utc(),
    )

    loaded = {
        row.agent_code: row.abstain_reason_code
        for row in load_finalized_predictive_agent_predictions(
            db, prediction_run_id=run_id
        )
    }
    assert loaded[A1] is None
    assert loaded[A2] == "INSUFFICIENT_POINT_HISTORY"
    assert loaded[A3] == "WEAK_SIGNAL"


def test_non_canonical_reason_is_rejected_before_any_write(db: psycopg.Connection):
    result = make_result(A2, utc(), status="ABSTAINED", reason="NOT_A_CANONICAL_REASON")
    with pytest.raises(PredictiveAgentContractError):
        persist_predictive_agent_result(
            db,
            prediction_run_id=1,
            match_id=1,
            agent_config_version_id=1,
            result=result,
            prediction_locked_at=utc(),
        )


def test_historical_abstained_rows_with_null_reason_remain_readable(db: psycopg.Connection):
    match_id, run_id, configs = _setup(db)
    raw_insert_and_finalize(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        specs=(
            (A1, "PREDICTED", 1, "0.700000", "agent1_raw_probability_v1", None),
            (A2, "ABSTAINED", None, None, None, None),
            (A3, "ABSTAINED", None, None, None, None),
        ),
        finalized_at=utc(),
    )

    reasons = _reasons(db, run_id)
    assert reasons[A1] is None
    assert reasons[A2] is None
    assert reasons[A3] is None
