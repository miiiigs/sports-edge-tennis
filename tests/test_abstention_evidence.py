"""Read-only abstention diagnostics aggregate participation and reason shares."""

from __future__ import annotations

import psycopg

from sports_edge_tennis.performance.abstention_evidence import (
    UNKNOWN_REASON_BUCKET,
    load_abstention_window,
    load_abstention_windows,
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


def _window(db):
    start = utc(day=1, hour=0)
    end = utc(day=2, hour=0)
    return load_abstention_window(db, window_start=start, window_end=end)


def test_participation_and_reason_shares(db: psycopg.Connection):
    match_id = insert_match(db)
    run_id = insert_run(db, as_of=utc())
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=(
            make_result(A1, utc(), status="PREDICTED", side=1, probability="0.700000"),
            make_result(A2, utc(), status="ABSTAINED", reason="INSUFFICIENT_HISTORY"),
            make_result(A3, utc(), status="ABSTAINED", reason="WEAK_SIGNAL"),
        ),
        finalized_at=utc(),
    )

    agents = {agent.agent_code: agent for agent in _window(db)}
    assert agents[A1].evaluations == 1
    assert agents[A1].predicted == 1
    assert agents[A1].abstained == 0
    assert agents[A1].reasons == ()

    assert agents[A2].abstained == 1
    assert agents[A2].reasons[0].reason_code == "INSUFFICIENT_HISTORY"
    assert agents[A2].reasons[0].count == 1
    assert agents[A2].reasons[0].share == 1.0
    assert agents[A2].unknown_reason_count == 0

    assert agents[A3].reasons[0].reason_code == "WEAK_SIGNAL"


def test_pre_0017_rows_are_reported_as_unknown_not_inferred(db: psycopg.Connection):
    match_id = insert_match(db)
    run_id = insert_run(db, as_of=utc())
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
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

    agents = {agent.agent_code: agent for agent in _window(db)}
    assert agents[A2].unknown_reason_count == 1
    assert agents[A2].reasons[0].reason_code == UNKNOWN_REASON_BUCKET
    assert agents[A3].unknown_reason_count == 1


def test_windows_return_three_utc_rollups(db: psycopg.Connection):
    match_id = insert_match(db)
    run_id = insert_run(db, as_of=utc())
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=(
            make_result(A1, utc(), status="PREDICTED", side=1, probability="0.700000"),
            make_result(A2, utc(), status="ABSTAINED", reason="INSUFFICIENT_HISTORY"),
            make_result(A3, utc(), status="ABSTAINED", reason="WEAK_SIGNAL"),
        ),
        finalized_at=utc(),
    )

    windows = load_abstention_windows(db, now=utc(day=1, hour=12))
    assert [w.window for w in windows] == ["TODAY", "LAST_7_DAYS", "LAST_30_DAYS"]
    assert all(any(agent.agent_code == A2 for agent in w.agents) for w in windows)
