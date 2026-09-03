from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.manager.contracts import (
    ManagerConsensusError,
    ManagerIdempotencyMismatchError,
    ManagerPartialSlateError,
    consensus_mean,
)
from sports_edge_tennis.manager.persistence import (
    ensure_manager_policy_version_v1,
    evaluate_and_persist_manager_consensus,
    load_manager_policy_version,
)

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"
AGENTS = (A1, A2, A3)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_player(conn: psycopg.Connection, name: str) -> int:
    return _fetchval(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,))


def _insert_match(conn: psycopg.Connection) -> int:
    tournament_id = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES (%s, 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
        ("Manager Open",),
    )
    edition_id = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) VALUES (%s, 2026, %s) RETURNING tournament_edition_id",
        (tournament_id, "Manager Open 2026"),
    )
    return _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )


def _insert_prediction_run(
    conn: psycopg.Connection,
    *,
    run_status: str,
    contract_version: str,
    as_of: datetime,
    started_at: datetime | None = None,
    prediction_finalized_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> int:
    started = started_at or as_of
    return _fetchval(
        conn,
        """
        INSERT INTO prediction_runs (
            run_status, as_of, started_at, prediction_finalized_at, ended_at, prediction_contract_version
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING prediction_run_id
        """,
        (run_status, as_of, started, prediction_finalized_at, ended_at, contract_version),
    )


def _insert_agent_config_version(conn: psycopg.Connection, agent_code: str) -> int:
    return _fetchval(
        conn,
        """
        INSERT INTO agent_config_versions (
            agent_code, config_version, model_version, feature_version, parameter_schema_version, config_payload
        )
        VALUES (%s, %s, 'm1', 'f1', 'params-v1', %s::jsonb)
        RETURNING agent_config_version_id
        """,
        (agent_code, f"{agent_code.lower()}-config-v1", '{"manager": true}'),
    )


def _insert_target(conn: psycopg.Connection, run_id: int, match_id: int) -> None:
    conn.execute(
        "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
        (run_id, match_id),
    )


def _insert_prediction(
    conn: psycopg.Connection,
    *,
    match_id: int,
    run_id: int,
    config_id: int,
    agent_code: str,
    status: str,
    side: int | None,
    prob: Decimal | None,
    as_of: datetime,
    locked_at: datetime,
) -> int:
    if status == "PREDICTED":
        representation = AGENT_PROBABILITY_REPRESENTATIONS[agent_code]
    else:
        side = None
        prob = None
        representation = None
    return _fetchval(
        conn,
        """
        INSERT INTO predictions (
            match_id, prediction_run_id, agent_config_version_id, agent_code,
            prediction_status, selection_side, win_probability, model_version, feature_version,
            as_of, prediction_locked_at, probability_representation
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'm1', 'f1', %s, %s, %s)
        RETURNING prediction_id
        """,
        (match_id, run_id, config_id, agent_code, status, side, prob, as_of, locked_at, representation),
    )


def _build_finalized_eligible_run(
    conn: psycopg.Connection,
    *,
    targets: list[dict],
    as_of: datetime | None = None,
) -> int:
    """Build a COMPLETED, finalized predictive_agent_result_v1 run.

    Each target is a dict: {'match_id': int, 'agents': {agent_code: (status, side, prob)}}.
    """
    run_as_of = as_of or _utc(10, 0)
    finalized_at = run_as_of + timedelta(minutes=30)
    with conn.transaction():
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        run_id = _insert_prediction_run(
            conn,
            run_status="RUNNING",
            contract_version="predictive_agent_result_v1",
            as_of=run_as_of,
        )
        config_ids = {agent: _insert_agent_config_version(conn, agent) for agent in AGENTS}
        for target in targets:
            match_id = target["match_id"]
            _insert_target(conn, run_id, match_id)
            for agent in AGENTS:
                status, side, prob = target["agents"][agent]
                _insert_prediction(
                    conn,
                    match_id=match_id,
                    run_id=run_id,
                    config_id=config_ids[agent],
                    agent_code=agent,
                    status=status,
                    side=side,
                    prob=prob,
                    as_of=run_as_of,
                    locked_at=finalized_at,
                )
        conn.execute(
            """
            UPDATE prediction_runs
            SET run_status = 'COMPLETED',
                prediction_finalized_at = %s,
                ended_at = %s
            WHERE prediction_run_id = %s
            """,
            (finalized_at, finalized_at + timedelta(minutes=5), run_id),
        )
    return run_id


def _manager_policy_v1_id(conn: psycopg.Connection) -> int:
    return ensure_manager_policy_version_v1(conn).manager_policy_version_id


def _agreeing_agents(*, side: int = 1, prob: str = "0.60") -> dict:
    return {A1: ("PREDICTED", side, Decimal(prob)),
            A2: ("PREDICTED", side, Decimal(prob)),
            A3: ("PREDICTED", side, Decimal(prob))}


def _manager_row_count(conn: psycopg.Connection, run_id: int) -> int:
    return conn.execute(
        "SELECT count(*) FROM manager_consensus_results WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()[0]


def test_migration_seeds_frozen_v1_policy(db: psycopg.Connection):
    policy_id = _manager_policy_v1_id(db)
    policy = load_manager_policy_version(db, manager_policy_version_id=policy_id)
    assert policy.policy_key == "tennis_manager_consensus"
    assert policy.policy_version == "v1"
    assert policy.three_agent_max_probability_spread == Decimal("0.080000")
    assert policy.agent3_abstention_max_probability_spread == Decimal("0.060000")
    assert policy.min_active_agent_probability == Decimal("0.500000")


def test_first_execution_persists_whole_slate(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[{"match_id": match_id, "agents": _agreeing_agents(prob="0.60")}],
    )

    outcome = evaluate_and_persist_manager_consensus(
        db,
        prediction_run_id=run_id,
        manager_policy_version_id=_manager_policy_v1_id(db),
    )

    assert outcome.status == "PERSISTED"
    assert outcome.target_count == 1
    assert _manager_row_count(db, run_id) == 1

    row = db.execute(
        "SELECT manager_status, reason_code, selected_side, consensus_probability FROM manager_consensus_results WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()
    assert row[0] == "CONSENSUS"
    assert row[1] == "NONE"
    assert row[2] == 1
    assert row[3] == Decimal("0.60")


def test_agent1_abstention_persists_no_bet_with_spread(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[
            {
                "match_id": match_id,
                "agents": {
                    A1: ("ABSTAINED", None, None),
                    A2: ("PREDICTED", 1, Decimal("0.60")),
                    A3: ("PREDICTED", 1, Decimal("0.61")),
                },
            }
        ],
    )

    outcome = evaluate_and_persist_manager_consensus(
        db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
    )
    assert outcome.status == "PERSISTED"

    row = db.execute(
        "SELECT manager_status, reason_code, active_agent_count, abstaining_agent_count, probability_spread FROM manager_consensus_results WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()
    assert row[0] == "NO_BET"
    assert row[1] == "UNSUPPORTED_ABSTENTION_CONFIGURATION"
    assert row[2] == 2
    assert row[3] == 1
    assert row[4] == Decimal("0.010000")


def test_identical_retry_is_idempotent_without_writes(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[{"match_id": match_id, "agents": _agreeing_agents(prob="0.60")}],
    )
    policy_id = _manager_policy_v1_id(db)

    first = evaluate_and_persist_manager_consensus(
        db, prediction_run_id=run_id, manager_policy_version_id=policy_id
    )
    assert first.status == "PERSISTED"

    created_before = db.execute(
        "SELECT created_at FROM manager_consensus_results WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()[0]

    second = evaluate_and_persist_manager_consensus(
        db, prediction_run_id=run_id, manager_policy_version_id=policy_id
    )
    assert second.status == "IDEMPOTENT_SUCCESS"
    assert _manager_row_count(db, run_id) == 1

    created_after = db.execute(
        "SELECT created_at FROM manager_consensus_results WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()[0]
    assert created_before == created_after


def test_mismatched_retry_fails_closed(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[{"match_id": match_id, "agents": _agreeing_agents(prob="0.60")}],
    )
    policy_id = _manager_policy_v1_id(db)

    _direct_insert_manager_row(
        db,
        run_id=run_id,
        match_id=match_id,
        policy_id=policy_id,
        as_of=_utc(10, 0),
        prob=Decimal("0.61"),
    )

    with pytest.raises(ManagerIdempotencyMismatchError):
        evaluate_and_persist_manager_consensus(
            db,
            prediction_run_id=run_id,
            manager_policy_version_id=policy_id,
        )


def test_null_safe_mismatch_fails_closed(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[{"match_id": match_id, "agents": _agreeing_agents(prob="0.60")}],
    )
    policy_id = _manager_policy_v1_id(db)

    _direct_insert_manager_row(
        db,
        run_id=run_id,
        match_id=match_id,
        policy_id=policy_id,
        as_of=_utc(10, 0),
        status="NO_BET",
        side=None,
        prob=None,
        reason="DIRECTIONAL_DISAGREEMENT",
    )

    with pytest.raises(ManagerIdempotencyMismatchError):
        evaluate_and_persist_manager_consensus(
            db,
            prediction_run_id=run_id,
            manager_policy_version_id=policy_id,
        )


def test_policy_mismatch_retry_fails_closed(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[{"match_id": match_id, "agents": _agreeing_agents(prob="0.60")}],
    )
    v1_id = _manager_policy_v1_id(db)

    evaluate_and_persist_manager_consensus(db, prediction_run_id=run_id, manager_policy_version_id=v1_id)

    v2_id = _fetchval(
        db,
        """
        INSERT INTO manager_policy_versions (
            policy_key, policy_version, consensus_mode, allow_agent3_abstention,
            three_agent_max_probability_spread, agent3_abstention_max_probability_spread,
            min_active_agent_probability
        )
        VALUES ('tennis_manager_consensus', 'v2', 'UNANIMOUS_ACTIVE_AGENTS', TRUE, 0.080000, 0.060000, 0.500000)
        RETURNING manager_policy_version_id
        """,
    )

    with pytest.raises(ManagerIdempotencyMismatchError):
        evaluate_and_persist_manager_consensus(db, prediction_run_id=run_id, manager_policy_version_id=v2_id)


def test_null_safe_identical_retry(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[
            {
                "match_id": match_id,
                "agents": {
                    A1: ("PREDICTED", 1, Decimal("0.60")),
                    A2: ("PREDICTED", 2, Decimal("0.60")),
                    A3: ("PREDICTED", 1, Decimal("0.60")),
                },
            }
        ],
    )
    policy_id = _manager_policy_v1_id(db)

    first = evaluate_and_persist_manager_consensus(db, prediction_run_id=run_id, manager_policy_version_id=policy_id)
    assert first.status == "PERSISTED"
    assert _manager_row_count(db, run_id) == 1

    second = evaluate_and_persist_manager_consensus(db, prediction_run_id=run_id, manager_policy_version_id=policy_id)
    assert second.status == "IDEMPOTENT_SUCCESS"


def test_partial_slate_fails_closed(db: psycopg.Connection):
    match_1 = _insert_match(db)
    match_2 = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[
            {"match_id": match_1, "agents": _agreeing_agents(prob="0.60")},
            {"match_id": match_2, "agents": _agreeing_agents(prob="0.62")},
        ],
    )

    # Seed exactly one Manager row for the first target only.
    db.execute(
        """
        INSERT INTO manager_consensus_results (
            prediction_run_id, match_id, manager_policy_version_id, manager_status,
            selected_side, consensus_probability, active_agent_count, abstaining_agent_count,
            probability_spread, reason_code, as_of
        )
        VALUES (%s, %s, %s, 'CONSENSUS', 1, 0.60, 3, 0, 0.0, 'NONE', %s)
        """,
        (run_id, match_1, _manager_policy_v1_id(db), _utc(10, 0)),
    )

    with pytest.raises(ManagerPartialSlateError):
        evaluate_and_persist_manager_consensus(
            db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
        )
    assert _manager_row_count(db, run_id) == 1


def test_rollback_leaves_zero_rows(monkeypatch, db: psycopg.Connection):
    match_1 = _insert_match(db)
    match_2 = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[
            {"match_id": match_1, "agents": _agreeing_agents(prob="0.60")},
            {"match_id": match_2, "agents": _agreeing_agents(prob="0.62")},
        ],
    )

    import sports_edge_tennis.manager.persistence as persistence

    original = persistence.evaluate_consensus
    call_count = {"n": 0}

    def flaky(predictions, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("injected mid-slate failure")
        return original(predictions, **kwargs)

    monkeypatch.setattr(persistence, "evaluate_consensus", flaky)

    with pytest.raises(RuntimeError):
        evaluate_and_persist_manager_consensus(
            db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
        )

    assert _manager_row_count(db, run_id) == 0


def test_reconciliation_mismatch_rolls_back(monkeypatch, db: psycopg.Connection):
    match_1 = _insert_match(db)
    match_2 = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[
            {"match_id": match_1, "agents": _agreeing_agents(prob="0.60")},
            {"match_id": match_2, "agents": _agreeing_agents(prob="0.62")},
        ],
    )

    import sports_edge_tennis.manager.persistence as persistence

    original_persist = persistence._persist_slate

    def partial_persist(conn, results):
        original_persist(conn, results[:1])

    monkeypatch.setattr(persistence, "_persist_slate", partial_persist)

    with pytest.raises(ManagerConsensusError):
        evaluate_and_persist_manager_consensus(
            db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
        )

    assert _manager_row_count(db, run_id) == 0


def test_completion_invariant(db: psycopg.Connection):
    match_1 = _insert_match(db)
    match_2 = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db,
        targets=[
            {"match_id": match_1, "agents": _agreeing_agents(prob="0.60")},
            {"match_id": match_2, "agents": _agreeing_agents(prob="0.62")},
        ],
    )

    outcome = evaluate_and_persist_manager_consensus(
        db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
    )
    assert outcome.status == "PERSISTED"
    assert outcome.target_count == 2
    assert _manager_row_count(db, run_id) == 2


def test_empty_slate_produces_zero_rows(db: psycopg.Connection):
    run_id = _insert_prediction_run(
        db,
        run_status="COMPLETED",
        contract_version="predictive_agent_result_v1",
        as_of=_utc(10, 0),
        prediction_finalized_at=_utc(10, 30),
        ended_at=_utc(10, 35),
    )
    outcome = evaluate_and_persist_manager_consensus(
        db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
    )
    assert outcome.status == "EMPTY_SLATE"
    assert outcome.target_count == 0
    assert _manager_row_count(db, run_id) == 0


def test_high_precision_agent3_consensus(db: psycopg.Connection):
    match_id = _insert_match(db)
    high_precision = Decimal("0.61123456789012345678901234567890123456789012345678")
    run_id = _build_finalized_eligible_run(
        db,
        targets=[
            {
                "match_id": match_id,
                "agents": {
                    A1: ("PREDICTED", 1, Decimal("0.60")),
                    A2: ("PREDICTED", 1, Decimal("0.60")),
                    A3: ("PREDICTED", 1, high_precision),
                },
            }
        ],
    )

    evaluate_and_persist_manager_consensus(
        db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
    )

    stored = db.execute(
        "SELECT consensus_probability FROM manager_consensus_results WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()[0]

    expected = consensus_mean((Decimal("0.60"), Decimal("0.60"), high_precision))
    assert stored == expected
    assert -stored.as_tuple().exponent == 50


def _direct_insert_manager_row(
    conn: psycopg.Connection,
    *,
    run_id: int,
    match_id: int,
    policy_id: int,
    as_of: datetime,
    status: str = "CONSENSUS",
    side: int | None = 1,
    prob: Decimal | None = Decimal("0.60"),
    spread: Decimal | None = Decimal("0.0"),
    reason: str = "NONE",
) -> None:
    conn.execute(
        """
        INSERT INTO manager_consensus_results (
            prediction_run_id, match_id, manager_policy_version_id, manager_status,
            selected_side, consensus_probability, active_agent_count, abstaining_agent_count,
            probability_spread, reason_code, as_of
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (run_id, match_id, policy_id, status, side, prob, 3, 0, spread, reason, as_of),
    )


def test_direct_insert_rejects_legacy_run(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _insert_prediction_run(
        db,
        run_status="COMPLETED",
        contract_version="legacy_prediction_ledger_v1",
        as_of=_utc(10, 0),
        prediction_finalized_at=_utc(10, 30),
        ended_at=_utc(10, 35),
    )
    with pytest.raises(psycopg.Error) as exc:
        _direct_insert_manager_row(
            db, run_id=run_id, match_id=match_id, policy_id=_manager_policy_v1_id(db), as_of=_utc(10, 0)
        )
    assert "predictive_agent_result_v1" in str(exc.value)


def test_direct_insert_rejects_running_run(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        contract_version="predictive_agent_result_v1",
        as_of=_utc(10, 0),
    )
    _insert_target(db, run_id, match_id)
    with pytest.raises(psycopg.Error) as exc:
        _direct_insert_manager_row(
            db, run_id=run_id, match_id=match_id, policy_id=_manager_policy_v1_id(db), as_of=_utc(10, 0)
        )
    assert "COMPLETED" in str(exc.value)


def test_direct_insert_rejects_failed_run(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _insert_prediction_run(
        db,
        run_status="FAILED",
        contract_version="predictive_agent_result_v1",
        as_of=_utc(10, 0),
        prediction_finalized_at=_utc(10, 30),
        ended_at=_utc(10, 35),
    )
    with pytest.raises(psycopg.Error) as exc:
        _direct_insert_manager_row(
            db, run_id=run_id, match_id=match_id, policy_id=_manager_policy_v1_id(db), as_of=_utc(10, 0)
        )
    assert "COMPLETED" in str(exc.value)


def test_direct_insert_rejects_cancelled_run(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _insert_prediction_run(
        db,
        run_status="CANCELLED",
        contract_version="predictive_agent_result_v1",
        as_of=_utc(10, 0),
        prediction_finalized_at=_utc(10, 30),
        ended_at=_utc(10, 35),
    )
    with pytest.raises(psycopg.Error) as exc:
        _direct_insert_manager_row(
            db, run_id=run_id, match_id=match_id, policy_id=_manager_policy_v1_id(db), as_of=_utc(10, 0)
        )
    assert "COMPLETED" in str(exc.value)


def test_direct_insert_rejects_unfinalized_run(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _insert_prediction_run(
        db,
        run_status="COMPLETED",
        contract_version="predictive_agent_result_v1",
        as_of=_utc(10, 0),
        prediction_finalized_at=None,
        ended_at=_utc(10, 35),
    )
    with pytest.raises(psycopg.Error) as exc:
        _direct_insert_manager_row(
            db, run_id=run_id, match_id=match_id, policy_id=_manager_policy_v1_id(db), as_of=_utc(10, 0)
        )
    assert "finalized" in str(exc.value)


def test_direct_insert_accepts_valid_finalized_v1_run(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db, targets=[{"match_id": match_id, "agents": _agreeing_agents(prob="0.60")}]
    )
    _direct_insert_manager_row(
        db, run_id=run_id, match_id=match_id, policy_id=_manager_policy_v1_id(db), as_of=_utc(10, 0)
    )
    assert _manager_row_count(db, run_id) == 1


def test_direct_insert_rejection_does_not_mutate_parent(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        contract_version="predictive_agent_result_v1",
        as_of=_utc(10, 0),
    )
    _insert_target(db, run_id, match_id)
    before = db.execute(
        "SELECT run_status, prediction_contract_version FROM prediction_runs WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()

    with pytest.raises(psycopg.Error):
        _direct_insert_manager_row(
            db, run_id=run_id, match_id=match_id, policy_id=_manager_policy_v1_id(db), as_of=_utc(10, 0)
        )

    after = db.execute(
        "SELECT run_status, prediction_contract_version FROM prediction_runs WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()
    assert before == after


def test_manager_results_are_immutable(db: psycopg.Connection):
    match_id = _insert_match(db)
    run_id = _build_finalized_eligible_run(
        db, targets=[{"match_id": match_id, "agents": _agreeing_agents(prob="0.60")}]
    )
    evaluate_and_persist_manager_consensus(
        db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            "UPDATE manager_consensus_results SET consensus_probability = 0.99 WHERE prediction_run_id = %s",
            (run_id,),
        )
    with pytest.raises(psycopg.Error):
        db.execute(
            "DELETE FROM manager_consensus_results WHERE prediction_run_id = %s",
            (run_id,),
        )


def test_legacy_run_rejected_by_manager(db: psycopg.Connection):
    run_id = _insert_prediction_run(
        db,
        run_status="COMPLETED",
        contract_version="legacy_prediction_ledger_v1",
        as_of=_utc(10, 0),
        prediction_finalized_at=_utc(10, 30),
        ended_at=_utc(10, 35),
    )
    with pytest.raises(ManagerConsensusError):
        evaluate_and_persist_manager_consensus(
            db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
        )


def test_unfinalized_run_rejected_by_manager(db: psycopg.Connection):
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        contract_version="predictive_agent_result_v1",
        as_of=_utc(10, 0),
    )
    with pytest.raises(ManagerConsensusError):
        evaluate_and_persist_manager_consensus(
            db, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_v1_id(db)
        )
