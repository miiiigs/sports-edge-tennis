"""Result-coverage monitor flags terminal fixtures with no result observation."""

from __future__ import annotations

from datetime import timedelta

import psycopg

from sports_edge_tennis.dashboard.result_coverage import (
    load_terminal_fixtures_without_result,
)
from sports_edge_tennis.manager.persistence import ensure_manager_policy_version_v1
from tests.abstention_helpers import (
    A1,
    A2,
    A3,
    insert_match,
    insert_run,
    insert_target,
    raw_insert_and_finalize,
    seed_agent_configs,
    utc,
)

_REFERENCE = utc(day=10, hour=12)


def _insert_finalized_production_run(conn: psycopg.Connection, match_id: int) -> int:
    run_id = insert_run(conn, as_of=utc(day=2, hour=1))
    insert_target(conn, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(conn)
    raw_insert_and_finalize(
        conn,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        specs=(
            (A1, "PREDICTED", 1, "0.700000", "agent1_raw_probability_v1", None),
            (A2, "ABSTAINED", None, None, None, "INSUFFICIENT_HISTORY"),
            (A3, "ABSTAINED", None, None, None, "WEAK_SIGNAL"),
        ),
        finalized_at=utc(day=2, hour=1),
    )
    return run_id


def _insert_manager_consensus(conn: psycopg.Connection, run_id: int, match_id: int) -> None:
    policy = ensure_manager_policy_version_v1(conn)
    conn.execute(
        """
        INSERT INTO manager_consensus_results (
            prediction_run_id, match_id, manager_policy_version_id, manager_status,
            selected_side, consensus_probability, active_agent_count,
            abstaining_agent_count, probability_spread, reason_code, as_of
        )
        VALUES (%s, %s, %s, 'CONSENSUS', 1, 0.700000, 2, 1, 0.050000, 'NONE', %s)
        """,
        (run_id, match_id, policy.manager_policy_version_id, utc(day=2, hour=1)),
    )


def _setup_aligned_pick(
    conn: psycopg.Connection,
    *,
    match_status: str = "COMPLETED",
    match_start_at=None,
    with_terminal_result: bool = False,
) -> int:
    match_id = insert_match(
        conn, status=match_status, match_start_at=match_start_at or utc(day=1, hour=9)
    )
    run_id = _insert_finalized_production_run(conn, match_id)
    _insert_manager_consensus(conn, run_id, match_id)
    if with_terminal_result:
        conn.execute(
            """
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, source_observed_at, as_of
            )
            VALUES (%s, 'COMPLETED', 1, %s, %s)
            """,
            (match_id, utc(day=1, hour=12), utc(day=1, hour=12)),
        )
    return match_id


def test_terminal_fixture_without_result_is_reported(db: psycopg.Connection):
    match_id = _setup_aligned_pick(db)
    report = load_terminal_fixtures_without_result(db, grace_hours=12, now=_REFERENCE)
    assert report.stale_count == 1
    assert report.items[0].match_id == match_id
    assert report.items[0].match_status == "COMPLETED"
    assert report.items[0].age_hours is not None and report.items[0].age_hours > 12


def test_terminal_fixture_with_result_is_not_reported(db: psycopg.Connection):
    _setup_aligned_pick(db, with_terminal_result=True)
    report = load_terminal_fixtures_without_result(db, grace_hours=12, now=_REFERENCE)
    assert report.stale_count == 0


def test_recent_fixture_inside_grace_is_not_reported(db: psycopg.Connection):
    recent = _REFERENCE - timedelta(hours=1)
    _setup_aligned_pick(db, match_start_at=recent)
    report = load_terminal_fixtures_without_result(db, grace_hours=12, now=_REFERENCE)
    assert report.stale_count == 0


def test_non_terminal_fixture_is_not_reported(db: psycopg.Connection):
    _setup_aligned_pick(db, match_status="SCHEDULED")
    report = load_terminal_fixtures_without_result(db, grace_hours=12, now=_REFERENCE)
    assert report.stale_count == 0
