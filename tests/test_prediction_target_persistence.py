from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from sports_edge_tennis.data.prediction_persistence import insert_prediction_run_targets


def _utc() -> datetime:
    return datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_match(conn: psycopg.Connection) -> int:
    tournament_id = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES ('Target Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
    )
    edition_id = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) VALUES (%s, 2026, 'Target Open 2026') RETURNING tournament_edition_id",
        (tournament_id,),
    )
    return _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )


def _insert_run(conn: psycopg.Connection) -> int:
    return _fetchval(
        conn,
        """
        INSERT INTO prediction_runs (run_status, as_of, started_at, prediction_contract_version)
        VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1')
        RETURNING prediction_run_id
        """,
        (_utc(), _utc()),
    )


def _target_count(conn: psycopg.Connection, run_id: int) -> int:
    return conn.execute(
        "SELECT count(*) FROM prediction_run_targets WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()[0]


def test_insert_targets_persists_exact_rows(db: psycopg.Connection):
    run_id = _insert_run(db)
    m1 = _insert_match(db)
    m2 = _insert_match(db)
    m3 = _insert_match(db)

    insert_prediction_run_targets(db, prediction_run_id=run_id, match_ids=[m1, m2, m3])

    rows = db.execute(
        "SELECT match_id FROM prediction_run_targets WHERE prediction_run_id = %s ORDER BY match_id",
        (run_id,),
    ).fetchall()
    assert [r[0] for r in rows] == sorted([m1, m2, m3])
    assert _target_count(db, run_id) == 3


def test_insert_targets_accepts_generator(db: psycopg.Connection):
    run_id = _insert_run(db)
    m1 = _insert_match(db)
    m2 = _insert_match(db)

    insert_prediction_run_targets(db, prediction_run_id=run_id, match_ids=(m for m in (m1, m2)))

    assert _target_count(db, run_id) == 2


def test_insert_targets_empty_is_noop(db: psycopg.Connection):
    run_id = _insert_run(db)
    insert_prediction_run_targets(db, prediction_run_id=run_id, match_ids=[])
    assert _target_count(db, run_id) == 0


def test_insert_targets_duplicate_raises(db: psycopg.Connection):
    run_id = _insert_run(db)
    m1 = _insert_match(db)
    with pytest.raises(psycopg.Error):
        insert_prediction_run_targets(db, prediction_run_id=run_id, match_ids=[m1, m1])


def test_insert_targets_rolls_back_atomically(db: psycopg.Connection):
    run_id = _insert_run(db)
    m1 = _insert_match(db)
    m2 = _insert_match(db)

    with pytest.raises(psycopg.Error):
        with db.transaction():
            insert_prediction_run_targets(db, prediction_run_id=run_id, match_ids=[m1, m2, m1])

    assert _target_count(db, run_id) == 0


def test_insert_targets_requires_predictive_agent_v1_run(db: psycopg.Connection):
    legacy_run_id = _fetchval(
        db,
        """
        INSERT INTO prediction_runs (run_status, as_of, started_at, prediction_contract_version)
        VALUES ('RUNNING', %s, %s, 'legacy_prediction_ledger_v1')
        RETURNING prediction_run_id
        """,
        (_utc(), _utc()),
    )
    m1 = _insert_match(db)
    with pytest.raises(psycopg.Error):
        insert_prediction_run_targets(db, prediction_run_id=legacy_run_id, match_ids=[m1])
