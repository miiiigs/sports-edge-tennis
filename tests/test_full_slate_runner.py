from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as A1_CONFIG,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG as A2_CONFIG,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG as A3_CONFIG,
)
from sports_edge_tennis.manager.persistence import (
    ensure_manager_policy_version_current,
    ensure_manager_policy_version_v1,
)
from sports_edge_tennis.runner import (
    AgentConfigRefV1,
    FullSlateRunRequestV1,
    RunnerLifecycleError,
    RunnerRequestError,
    run_full_slate,
)
from sports_edge_tennis.runner.persistence import (
    mark_prediction_run_failed,
    persisted_counts,
)


A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_player(conn: psycopg.Connection, name: str) -> int:
    return _fetchval(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,))


def _insert_scheduled_target(
    conn: psycopg.Connection,
    *,
    surface: str | None = "HARD",
    round_code: str | None = "R32",
    best_of_sets: int = 3,
    qualifying_flag: bool = False,
    match_status: str = "SCHEDULED",
    source_observed_at: datetime | None = None,
    as_of: datetime | None = None,
) -> dict:
    version_as_of = as_of or _utc(8, 0)
    version_source = source_observed_at or version_as_of
    p1 = _insert_player(conn, "Player One")
    p2 = _insert_player(conn, "Player Two")
    tournament_id = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES ('Manager Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
    )
    edition_id = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) VALUES (%s, 2026, 'Manager Open 2026') RETURNING tournament_edition_id",
        (tournament_id,),
    )
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    match_version_id = _fetchval(
        conn,
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', %s, %s, %s, %s, %s, %s, %s)
        RETURNING match_version_id
        """,
        (
            match_id,
            p1,
            p2,
            version_as_of + timedelta(days=1),
            surface,
            round_code,
            best_of_sets,
            qualifying_flag,
            match_status,
            version_source,
            version_as_of,
        ),
    )
    return {"match_id": match_id, "match_version_id": match_version_id, "player_1_id": p1, "player_2_id": p2}


def _insert_agent_config(conn: psycopg.Connection, agent_code: str, config) -> int:
    return _fetchval(
        conn,
        """
        INSERT INTO agent_config_versions (
            agent_code, config_version, model_version, feature_version, calibration_version,
            parameter_schema_version, config_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING agent_config_version_id
        """,
        (
            agent_code,
            config.config_version,
            config.model_version,
            config.feature_version,
            None,
            config.parameter_schema_version,
            json.dumps(config.to_payload()),
        ),
    )


def _insert_agent_config_raw(
    conn: psycopg.Connection,
    *,
    agent_code: str,
    config_version: str,
    model_version: str,
    feature_version: str,
    parameter_schema_version: str,
    calibration_version: str | None = None,
    payload: dict | None = None,
) -> int:
    return _fetchval(
        conn,
        """
        INSERT INTO agent_config_versions (
            agent_code, config_version, model_version, feature_version, calibration_version,
            parameter_schema_version, config_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING agent_config_version_id
        """,
        (
            agent_code,
            config_version,
            model_version,
            feature_version,
            calibration_version,
            parameter_schema_version,
            json.dumps(payload if payload is not None else {"empty": True}),
        ),
    )


def _seed_configs(conn: psycopg.Connection) -> None:
    _insert_agent_config(conn, A1, A1_CONFIG)
    _insert_agent_config(conn, A2, A2_CONFIG)
    _insert_agent_config(conn, A3, A3_CONFIG)


def _request(conn: psycopg.Connection, *, match_ids, as_of=_utc(10, 0)) -> FullSlateRunRequestV1:
    return FullSlateRunRequestV1(
        match_ids=tuple(match_ids),
        as_of=as_of,
        agent_config_refs=(
            AgentConfigRefV1(A1, A1_CONFIG.config_version),
            AgentConfigRefV1(A2, A2_CONFIG.config_version),
            AgentConfigRefV1(A3, A3_CONFIG.config_version),
        ),
        manager_policy_version_id=ensure_manager_policy_version_v1(conn).manager_policy_version_id,
    )


def test_valid_one_target_full_slate(db: psycopg.Connection):
    _seed_configs(db)
    target = _insert_scheduled_target(db)
    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))

    assert outcome.runner_status == "COMPLETED"
    assert outcome.target_count == 1
    assert outcome.prediction_result_count == 3
    assert outcome.manager_result_count == 1

    run = db.execute(
        "SELECT run_status, prediction_finalized_at FROM prediction_runs WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()
    assert run[0] == "COMPLETED"
    assert run[1] is not None


def test_multi_target_full_slate(db: psycopg.Connection):
    _seed_configs(db)
    t1 = _insert_scheduled_target(db)
    t2 = _insert_scheduled_target(db)
    outcome = run_full_slate(db, _request(db, match_ids=[t1["match_id"], t2["match_id"]]))

    assert outcome.runner_status == "COMPLETED"
    assert outcome.target_count == 2
    assert outcome.prediction_result_count == 6
    assert outcome.manager_result_count == 2


def test_unknown_surface_still_completes(db: psycopg.Connection):
    _seed_configs(db)
    target = _insert_scheduled_target(db, surface=None)
    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))

    assert outcome.runner_status == "COMPLETED"
    assert outcome.prediction_result_count == 3
    assert outcome.manager_result_count == 1


def test_agent_exception_fails_run(db: psycopg.Connection, monkeypatch):
    _seed_configs(db)
    target = _insert_scheduled_target(db)

    import sports_edge_tennis.runner.full_slate as full_slate

    def boom(request, history=(), config=None):
        raise RuntimeError("agent1 exploded")

    monkeypatch.setattr(full_slate, "predict_agent1", boom)

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))

    assert outcome.runner_status == "PREDICTION_FAILED"
    assert outcome.failure_stage == "AGENT_EXECUTION"
    assert outcome.prediction_result_count == 0

    run = db.execute(
        "SELECT run_status, prediction_finalized_at FROM prediction_runs WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()
    assert run[0] == "FAILED"
    assert run[1] is None
    assert db.execute(
        "SELECT count(*) FROM prediction_run_targets WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()[0] == 1


def test_agent2_exception_fails_run(db: psycopg.Connection, monkeypatch):
    _seed_configs(db)
    target = _insert_scheduled_target(db)

    import sports_edge_tennis.runner.full_slate as full_slate

    monkeypatch.setattr(full_slate, "predict_agent2", lambda request, config=None: (_ for _ in ()).throw(RuntimeError("agent2 exploded")))

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))
    assert outcome.runner_status == "PREDICTION_FAILED"


def test_agent3_exception_fails_run(db: psycopg.Connection, monkeypatch):
    _seed_configs(db)
    target = _insert_scheduled_target(db)

    import sports_edge_tennis.runner.full_slate as full_slate

    monkeypatch.setattr(full_slate, "predict_agent3", lambda request, config=None: (_ for _ in ()).throw(RuntimeError("agent3 exploded")))

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))
    assert outcome.runner_status == "PREDICTION_FAILED"


def test_wrong_agent_code_fails_run(db: psycopg.Connection, monkeypatch):
    _seed_configs(db)
    target = _insert_scheduled_target(db)

    import sports_edge_tennis.runner.full_slate as full_slate

    real = full_slate.adapt_agent1_result

    def bad(result, config=None):
        adapted = real(result, config=config)
        return replace(adapted, agent_code=A2)

    monkeypatch.setattr(full_slate, "adapt_agent1_result", bad)

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))
    assert outcome.runner_status == "PREDICTION_FAILED"
    assert outcome.failure_stage == "AGENT_EXECUTION"


def test_wrong_as_of_fails_run(db: psycopg.Connection, monkeypatch):
    _seed_configs(db)
    target = _insert_scheduled_target(db)

    import sports_edge_tennis.runner.full_slate as full_slate

    real = full_slate.adapt_agent1_result

    def bad(result, config=None):
        adapted = real(result, config=config)
        return replace(adapted, as_of=_utc(9, 59))

    monkeypatch.setattr(full_slate, "adapt_agent1_result", bad)

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))
    assert outcome.runner_status == "PREDICTION_FAILED"


def test_duplicate_target_rejected_without_run(db: psycopg.Connection):
    _seed_configs(db)
    target = _insert_scheduled_target(db)
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, _request(db, match_ids=[target["match_id"], target["match_id"]]))
    assert db.execute("SELECT count(*) FROM prediction_runs").fetchone()[0] == 0


def test_unknown_target_rejected_without_run(db: psycopg.Connection):
    _seed_configs(db)
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, _request(db, match_ids=[999999]))
    assert db.execute("SELECT count(*) FROM prediction_runs").fetchone()[0] == 0


def test_future_as_of_rejected_without_run(db: psycopg.Connection):
    _seed_configs(db)
    _insert_scheduled_target(db, as_of=_utc(8, 0))
    times = iter([_utc(8, 0)])

    request = _request(db, match_ids=[], as_of=_utc(9, 0))
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, request, clock=lambda: next(times))
    assert db.execute("SELECT count(*) FROM prediction_runs").fetchone()[0] == 0


def test_non_scheduled_latest_version_rejected(db: psycopg.Connection):
    _seed_configs(db)
    target = _insert_scheduled_target(db, match_status="SCHEDULED", as_of=_utc(8, 0))
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'CANCELLED', %s, %s)
        """,
        (target["match_id"], target["player_1_id"], target["player_2_id"], _utc(13, 0), _utc(9, 0), _utc(9, 0)),
    )
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, _request(db, match_ids=[target["match_id"]]))
    assert db.execute("SELECT count(*) FROM prediction_runs").fetchone()[0] == 0


def test_future_match_version_excluded(db: psycopg.Connection):
    _seed_configs(db)
    target = _insert_scheduled_target(db, match_status="SCHEDULED", as_of=_utc(8, 0))
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'CANCELLED', %s, %s)
        """,
        (target["match_id"], target["player_1_id"], target["player_2_id"], _utc(20, 0), _utc(12, 0), _utc(12, 0)),
    )
    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))
    assert outcome.runner_status == "COMPLETED"


def test_invalid_manager_policy_rejected(db: psycopg.Connection):
    _seed_configs(db)
    _insert_scheduled_target(db)
    request = FullSlateRunRequestV1(
        match_ids=(),
        as_of=_utc(10, 0),
        agent_config_refs=(
            AgentConfigRefV1(A1, A1_CONFIG.config_version),
            AgentConfigRefV1(A2, A2_CONFIG.config_version),
            AgentConfigRefV1(A3, A3_CONFIG.config_version),
        ),
        manager_policy_version_id=999999,
    )
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, request)
    assert db.execute("SELECT count(*) FROM prediction_runs").fetchone()[0] == 0


def test_unsupported_manager_policy_version_rejected(db: psycopg.Connection):
    _seed_configs(db)
    _insert_scheduled_target(db)
    v2_id = _fetchval(
        db,
        """
        INSERT INTO manager_policy_versions (
            policy_key, policy_version, consensus_mode, allow_agent3_abstention,
            three_agent_max_probability_spread, agent3_abstention_max_probability_spread,
            min_active_agent_probability
        )
        VALUES ('tennis_manager_consensus', 'v3', 'UNANIMOUS_ACTIVE_AGENTS', TRUE, 0.080000, 0.060000, 0.500000)
        RETURNING manager_policy_version_id
        """,
    )
    request = FullSlateRunRequestV1(
        match_ids=(),
        as_of=_utc(10, 0),
        agent_config_refs=(
            AgentConfigRefV1(A1, A1_CONFIG.config_version),
            AgentConfigRefV1(A2, A2_CONFIG.config_version),
            AgentConfigRefV1(A3, A3_CONFIG.config_version),
        ),
        manager_policy_version_id=v2_id,
    )
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, request)


def test_missing_config_rejected(db: psycopg.Connection):
    _insert_agent_config(db, A1, A1_CONFIG)
    _insert_agent_config(db, A2, A2_CONFIG)
    _insert_scheduled_target(db)
    request = _request(db, match_ids=[])
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, request)


def test_typed_config_mismatch_rejected(db: psycopg.Connection):
    _insert_agent_config(db, A1, A1_CONFIG)
    _insert_agent_config(db, A2, A2_CONFIG)
    _insert_agent_config_raw(
        db,
        agent_code=A3,
        config_version=A3_CONFIG.config_version,
        model_version="wrong_model",
        feature_version=A3_CONFIG.feature_version,
        parameter_schema_version=A3_CONFIG.parameter_schema_version,
        payload=A3_CONFIG.to_payload(),
    )
    _insert_scheduled_target(db)
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, _request(db, match_ids=[]))


def test_non_null_calibration_rejected(db: psycopg.Connection):
    _insert_agent_config(db, A1, A1_CONFIG)
    _insert_agent_config(db, A2, A2_CONFIG)
    _insert_agent_config_raw(
        db,
        agent_code=A3,
        config_version=A3_CONFIG.config_version,
        model_version=A3_CONFIG.model_version,
        feature_version=A3_CONFIG.feature_version,
        parameter_schema_version=A3_CONFIG.parameter_schema_version,
        calibration_version="rejected-calibration-v1",
        payload=A3_CONFIG.to_payload(),
    )
    _insert_scheduled_target(db)
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, _request(db, match_ids=[]))


def test_config_payload_mismatch_rejected(db: psycopg.Connection):
    _insert_agent_config(db, A1, A1_CONFIG)
    _insert_agent_config(db, A2, A2_CONFIG)
    wrong_payload = dict(A3_CONFIG.to_payload())
    wrong_payload["half_life_days"] = 9999
    _insert_agent_config_raw(
        db,
        agent_code=A3,
        config_version=A3_CONFIG.config_version,
        model_version=A3_CONFIG.model_version,
        feature_version=A3_CONFIG.feature_version,
        parameter_schema_version=A3_CONFIG.parameter_schema_version,
        payload=wrong_payload,
    )
    _insert_scheduled_target(db)
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, _request(db, match_ids=[]))


def test_clock_timestamps_distinct_from_as_of(db: psycopg.Connection):
    _seed_configs(db)
    target = _insert_scheduled_target(db, as_of=_utc(8, 0))
    times = iter([_utc(9, 0), _utc(9, 30), _utc(9, 35)])

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]], as_of=_utc(8, 0)), clock=lambda: next(times))

    run = db.execute(
        "SELECT started_at, prediction_finalized_at, ended_at, as_of FROM prediction_runs WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()
    assert run[0] == _utc(9, 0)
    assert run[1] == _utc(9, 30)
    assert run[2] == _utc(9, 35)
    assert run[3] == _utc(8, 0)
    assert run[0] != run[3]


def test_empty_slate_audited(db: psycopg.Connection):
    _seed_configs(db)
    outcome = run_full_slate(db, _request(db, match_ids=[]))

    assert outcome.runner_status == "EMPTY_SLATE"
    assert outcome.prediction_run_id is not None
    assert outcome.target_count == 0
    assert outcome.prediction_result_count == 0
    assert outcome.manager_result_count == 0

    run = db.execute(
        "SELECT run_status, prediction_finalized_at FROM prediction_runs WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()
    assert run[0] == "COMPLETED"
    assert run[1] is not None


def test_manager_failure_preserves_prediction_truth(db: psycopg.Connection, monkeypatch):
    _seed_configs(db)
    target = _insert_scheduled_target(db)

    import sports_edge_tennis.runner.full_slate as full_slate

    monkeypatch.setattr(full_slate, "evaluate_and_persist_manager_consensus", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("manager failed")))

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))

    assert outcome.runner_status == "MANAGER_FAILED"
    assert outcome.failure_stage == "MANAGER"
    assert outcome.prediction_result_count == 3
    assert outcome.manager_result_count == 0

    run = db.execute(
        "SELECT run_status, prediction_finalized_at FROM prediction_runs WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()
    assert run[0] == "COMPLETED"
    assert run[1] is not None
    assert db.execute(
        "SELECT count(*) FROM predictions WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()[0] == 3


def test_manager_retry_on_same_run(db: psycopg.Connection, monkeypatch):
    _seed_configs(db)
    target = _insert_scheduled_target(db)

    import sports_edge_tennis.runner.full_slate as full_slate
    from sports_edge_tennis.manager.persistence import evaluate_and_persist_manager_consensus as real_manager

    calls = {"n": 0}

    def flaky(conn, *, prediction_run_id, manager_policy_version_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("manager failed")
        return real_manager(conn, prediction_run_id=prediction_run_id, manager_policy_version_id=manager_policy_version_id)

    monkeypatch.setattr(full_slate, "evaluate_and_persist_manager_consensus", flaky)

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))
    assert outcome.runner_status == "MANAGER_FAILED"

    retry = real_manager(
        db,
        prediction_run_id=outcome.prediction_run_id,
        manager_policy_version_id=ensure_manager_policy_version_v1(db).manager_policy_version_id,
    )
    assert retry.status == "PERSISTED"
    assert db.execute(
        "SELECT count(*) FROM manager_consensus_results WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()[0] == 1


def test_mark_failed_refuses_completed_run(db: psycopg.Connection):
    _seed_configs(db)
    target = _insert_scheduled_target(db)
    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))
    assert outcome.runner_status == "COMPLETED"

    with pytest.raises(RunnerLifecycleError):
        mark_prediction_run_failed(db, prediction_run_id=outcome.prediction_run_id, ended_at=_utc(11, 0))


def test_outcome_counts_from_db(db: psycopg.Connection):
    _seed_configs(db)
    t1 = _insert_scheduled_target(db)
    t2 = _insert_scheduled_target(db)
    t3 = _insert_scheduled_target(db)
    outcome = run_full_slate(db, _request(db, match_ids=[t1["match_id"], t2["match_id"], t3["match_id"]]))

    counts = persisted_counts(db, prediction_run_id=outcome.prediction_run_id)
    assert outcome.target_count == counts.target_count == 3
    assert outcome.prediction_result_count == counts.prediction_count == 9
    assert outcome.manager_result_count == counts.manager_count == 3


def test_retry_lineage_parent_validation(db: psycopg.Connection):
    _seed_configs(db)
    target = _insert_scheduled_target(db)
    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))

    request = _request(db, match_ids=[])
    bad_retry = FullSlateRunRequestV1(
        match_ids=(),
        as_of=_utc(10, 0),
        agent_config_refs=request.agent_config_refs,
        manager_policy_version_id=request.manager_policy_version_id,
        parent_prediction_run_id=outcome.prediction_run_id,
        parent_relation_type="RETRY",
    )
    with pytest.raises(RunnerRequestError):
        run_full_slate(db, bad_retry)


def test_finalization_failure_while_running_marks_failed(db: psycopg.Connection, monkeypatch):
    _seed_configs(db)
    target = _insert_scheduled_target(db)

    import sports_edge_tennis.runner.full_slate as full_slate

    def boom(*args, **kwargs):
        raise RuntimeError("finalization failed before commit")

    monkeypatch.setattr(full_slate, "persist_and_finalize_prediction_matrix", boom)

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))

    assert outcome.runner_status == "PREDICTION_FAILED"
    assert outcome.failure_stage == "PREDICTION_PERSISTENCE"

    run = db.execute(
        "SELECT run_status, prediction_finalized_at, ended_at FROM prediction_runs WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()
    assert run[0] == "FAILED"
    assert run[1] is None
    assert run[2] is not None
    assert db.execute(
        "SELECT count(*) FROM predictions WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM prediction_run_targets WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM prediction_runs").fetchone()[0] == 1


def test_finalization_exception_after_commit_does_not_rewrite_failed(
    db: psycopg.Connection, monkeypatch
):
    _seed_configs(db)
    target = _insert_scheduled_target(db)

    import sports_edge_tennis.runner.full_slate as full_slate
    from sports_edge_tennis.data.prediction_persistence import (
        persist_and_finalize_prediction_matrix as real_persist,
    )

    def finalize_then_raise(*args, **kwargs):
        real_persist(*args, **kwargs)
        raise RuntimeError("synthetic post-commit failure")

    monkeypatch.setattr(full_slate, "persist_and_finalize_prediction_matrix", finalize_then_raise)

    mark_failed_calls = []

    def spy_mark_failed(conn, *, prediction_run_id, ended_at):
        mark_failed_calls.append(prediction_run_id)
        raise AssertionError("mark_prediction_run_failed must not be called")

    monkeypatch.setattr(full_slate, "mark_prediction_run_failed", spy_mark_failed)

    outcome = run_full_slate(db, _request(db, match_ids=[target["match_id"]]))

    assert outcome.runner_status == "COMPLETED"
    assert mark_failed_calls == []

    run = db.execute(
        "SELECT run_status, prediction_finalized_at FROM prediction_runs WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()
    assert run[0] == "COMPLETED"
    assert run[1] is not None
    assert db.execute(
        "SELECT count(*) FROM predictions WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()[0] == 3
    assert db.execute(
        "SELECT count(*) FROM prediction_run_targets WHERE prediction_run_id = %s",
        (outcome.prediction_run_id,),
    ).fetchone()[0] == 1




def test_strict_manager_policy_v2_is_supported_by_full_slate_preflight(db: psycopg.Connection):
    _seed_configs(db)
    target = _insert_scheduled_target(db)
    policy_id = ensure_manager_policy_version_current(
        db
    ).manager_policy_version_id

    request = _request(db, match_ids=[target["match_id"]])
    request = request.__class__(
        match_ids=request.match_ids,
        as_of=request.as_of,
        agent_config_refs=request.agent_config_refs,
        manager_policy_version_id=policy_id,
        parent_prediction_run_id=request.parent_prediction_run_id,
        parent_relation_type=request.parent_relation_type,
    )
    outcome = run_full_slate(db, request)
    assert outcome.prediction_run_id > 0
    assert outcome.target_count == 1
    assert outcome.prediction_result_count == 3
