from datetime import date, datetime, timezone

import psycopg

from sports_edge_tennis.dashboard.read_model import get_run_for_date


def test_today_keeps_latest_completed_run_while_newer_run_is_running(
    db: psycopg.Connection,
):
    completed_at = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)
    running_at = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)

    completed_id = db.execute(
        """
        INSERT INTO prediction_runs (
            run_status,
            as_of,
            started_at,
            ended_at,
            prediction_finalized_at,
            prediction_contract_version
        )
        VALUES ('COMPLETED', %s, %s, %s, %s, 'predictive_agent_result_v1')
        RETURNING prediction_run_id
        """,
        (completed_at, completed_at, completed_at, completed_at),
    ).fetchone()[0]

    running_id = db.execute(
        """
        INSERT INTO prediction_runs (
            run_status,
            as_of,
            started_at,
            prediction_contract_version
        )
        VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1')
        RETURNING prediction_run_id
        """,
        (running_at, running_at),
    ).fetchone()[0]

    assert running_id > completed_id

    state = get_run_for_date(db, date(2026, 9, 15))

    assert state.kind == 'POPULATED'
    assert state.prediction_run_id == completed_id
    assert state.run_status == 'COMPLETED'
    assert state.as_of == completed_at
    assert state.matches == []
