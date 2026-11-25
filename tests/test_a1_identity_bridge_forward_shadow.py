from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
import pytest

from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.validation.a1_identity_bridge_forward_shadow import (
    CAPTURE_SCHEMA_VERSION,
    ShadowTarget,
    ShadowValidationError,
    build_record,
    run,
)


def _utc(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def _target(**overrides) -> ShadowTarget:
    values = {
        "prediction_run_id": 11,
        "match_id": 22,
        "prediction_id": 33,
        "prediction_as_of": _utc(20, 8),
        "prediction_finalized_at": _utc(20, 8, 1),
        "match_version_id": 4444,
        "match_start_at": _utc(20, 12),
        "live_player_1_id": 1001,
        "live_player_2_id": 1002,
        "model_player_1_id": 2001,
        "model_player_2_id": 1002,
        "surface": "HARD",
        "tour_scope": "CHALLENGER",
        "competition_tier": "CHALLENGER",
        "best_of_sets": 3,
        "qualifying_flag": False,
        "original_selection_side": 1,
        "original_win_probability": 0.72,
        "main_draw_eligible": True,
        "is_earliest_issued": True,
    }
    values.update(overrides)
    return ShadowTarget(**values)


def _bridged(probability: float, side: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        prediction_status="PREDICTED",
        selection_side=side,
        player_1_probability=probability,
        win_probability=probability if side == 1 else 1 - probability,
        model_version="agent1_surface_elo_v1",
        feature_version="agent1_surface_elo_inputs_v1",
    )


def test_target_requires_thirty_minute_forward_lead() -> None:
    with pytest.raises(ShadowValidationError):
        _target(match_start_at=_utc(20, 8) + timedelta(minutes=20))


def test_record_is_outcome_free_and_deterministic() -> None:
    target = _target()
    record = build_record(
        target=target,
        bridged=_bridged(0.66),
        history_rows=134_000,
        eligible_history_matches=130_000,
        capture_at=_utc(20, 8, 5),
        source_revision="abc123",
    )
    assert record["schema_version"] == CAPTURE_SCHEMA_VERSION
    assert record["outcome_included"] is False
    assert record["production_changed"] is False
    assert record["can_promote"] is False
    assert record["player_1_bridged"] is True
    assert record["player_2_bridged"] is False
    assert record["selection_changed"] is False
    assert record["match_version_id"] == 4444
    assert record["record_type"] == "capture"
    assert record["settlement_key"] == "22:33"
    assert record["main_draw_eligible"] is True
    assert record["coverage_scope"] == "CHALLENGER_MAIN_DRAW"
    assert record["player_1_resolution"]["method"] == "UNKNOWN"
    assert len(record["record_sha256"]) == 64

    again = build_record(
        target=target,
        bridged=_bridged(0.66),
        history_rows=134_000,
        eligible_history_matches=130_000,
        capture_at=_utc(20, 8, 5),
        source_revision="abc123",
    )
    assert again == record


def test_record_marks_selection_change_without_rewriting_original() -> None:
    record = build_record(
        target=_target(original_selection_side=1),
        bridged=_bridged(0.34, side=2),
        history_rows=10,
        eligible_history_matches=9,
        capture_at=_utc(20, 8, 5),
        source_revision=None,
    )
    assert record["original_selection_side"] == 1
    assert record["bridged_selection_side"] == 2
    assert record["selection_changed"] is True
    assert record["bridged_win_probability"] == pytest.approx(0.66)


def test_ingested_guard_is_opt_in_and_appended_to_both_queries() -> None:
    class Cursor:
        def fetchall(self):
            return []

    class FakeConn:
        def __init__(self) -> None:
            self.sql = ""
            self.params: dict[str, object] = {}

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params or {}
            return Cursor()

    fake = FakeConn()
    load_agent1_history(fake, cutoff=_utc(20), ingested_by=_utc(21))
    assert "mro.ingested_at <= %(ingested_by)s" in fake.sql
    assert "mv.ingested_at <= %(ingested_by)s" in fake.sql
    assert fake.params["ingested_by"] == _utc(21)

    plain = FakeConn()
    load_agent1_history(plain, cutoff=_utc(20))
    assert "ingested_by" not in plain.sql
    assert "ingested_by" not in plain.params


def test_empty_forward_capture_is_explicit_and_does_not_claim_promotion() -> None:
    class Cursor:
        def fetchall(self):
            return []

    class FakeConn:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, sql, params=None):
            self.statements.append(sql)
            return Cursor()

    conn = FakeConn()
    logs: list[str] = []
    summary = run(
        conn,
        run_ids=[999],
        capture_at=_utc(20, 8),
        log=logs.append,
    )
    assert summary["targets"] == 0
    assert summary["captured"] == 0
    assert summary["failed"] == 0
    assert summary["modified_production"] is False
    assert summary["outcome_included"] is False
    assert summary["can_promote"] is False
    assert any("TENNIS_A1_BRIDGE_FORWARD_SUMMARY" in line for line in logs)
    assert conn.statements[0] == "SET default_transaction_read_only=on"


def test_empty_capture_can_be_appended_to_archive(tmp_path) -> None:
    class Cursor:
        def fetchall(self):
            return []

    class FakeConn:
        def execute(self, sql, params=None):
            return Cursor()

    archive = tmp_path / "capture.jsonl"
    summary_1 = run(
        FakeConn(),
        run_ids=[1],
        capture_at=_utc(20, 8),
        archive_path=str(archive),
        log=lambda _: None,
    )
    first = archive.read_text(encoding="utf-8")
    assert '"record_type":"summary"' in first
    assert summary_1["archive"]["append_only"] is True

    run(
        FakeConn(),
        run_ids=[2],
        capture_at=_utc(20, 9),
        archive_path=str(archive),
        log=lambda _: None,
    )
    second = archive.read_text(encoding="utf-8")
    assert second.startswith(first)
    assert second.count('"record_type":"summary"') == 2


class _ReadOnlyViolation(AssertionError):
    pass


class _StatementGuard:
    """Wrap a real connection and reject any statement that is not read-only."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        head = " ".join(str(sql).strip().split()[:2]).upper()
        if not head.startswith(
            ("SELECT", "WITH", "SET DEFAULT_TRANSACTION_READ_ONLY", "SHOW")
        ):
            raise _ReadOnlyViolation(f"shadow issued a non-read-only statement: {head}")
        return self._conn.execute(sql, params)


def test_shadow_run_never_changes_original_decision_rows(db) -> None:
    from tests.abstention_helpers import (
        A1,
        A2,
        A3,
        finalize_matrix,
        insert_match,
        insert_run,
        insert_target,
        make_result,
        seed_agent_configs,
        utc,
    )

    match_id = insert_match(db, match_start_at=utc(hour=12))
    # insert_match's default version is observed at match start; add an earlier
    # PIT-visible version so the observer has a real target at the 08:00 run cutoff.
    version_player_1, version_player_2 = db.execute(
        "SELECT player_1_id, player_2_id FROM match_versions WHERE match_id = %s",
        (match_id,),
    ).fetchone()
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE,
                'SCHEDULED', %s, %s, %s)
        """,
        (
            match_id,
            version_player_1,
            version_player_2,
            utc(hour=12),
            utc(hour=7),
            utc(hour=7),
            utc(hour=7),
        ),
    )
    run_id = insert_run(db, as_of=utc(hour=8))
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=[
            make_result(
                code, utc(hour=8), status="PREDICTED", side=1, probability="0.61"
            )
            for code in (A1, A2, A3)
        ],
        finalized_at=utc(hour=8, minute=5),
    )
    before = {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "predictions",
            "prediction_runs",
            "decisions",
            "manager_consensus_results",
            "publication_control_events",
            "decision_settlements",
        )
    }
    stored = db.execute(
        "SELECT selection_side, win_probability FROM predictions "
        "WHERE prediction_run_id = %s AND agent_code = 'AGENT_1_SURFACE_ELO'",
        (run_id,),
    ).fetchone()

    with psycopg.connect(os.environ["TEST_POSTGRES_DSN"], autocommit=True) as observer_conn:
        summary = run(
            _StatementGuard(observer_conn),
            run_ids=[run_id],
            capture_at=utc(hour=9),
            log=lambda _: None,
        )
    assert summary["modified_production"] is False
    assert summary["outcome_included"] is False

    after = {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert before == after
    assert db.execute(
        "SELECT selection_side, win_probability FROM predictions "
        "WHERE prediction_run_id = %s AND agent_code = 'AGENT_1_SURFACE_ELO'",
        (run_id,),
    ).fetchone() == stored


def test_discovery_cycle_does_not_rearchive_a_run(tmp_path) -> None:
    from sports_edge_tennis.validation.a1_identity_bridge_forward_shadow import (
        run_discovered,
    )

    class Cursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self.rows = rows
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append(sql)
            if "FROM prediction_runs pr" in sql and "prediction_finalized_at >=" in sql:
                return Cursor(self.rows)
            return Cursor([])

    archive = tmp_path / "capture.jsonl"
    conn = FakeConn([(501, _utc(20, 8), _utc(20, 8))])
    first = run_discovered(
        conn,
        since=_utc(20),
        capture_at=_utc(20, 9),
        archive_path=str(archive),
        log=lambda _: None,
    )
    assert first["discovered_runs"] == 1
    assert first["captured_runs"] == 1

    second = run_discovered(
        conn,
        since=_utc(20),
        capture_at=_utc(20, 10),
        archive_path=str(archive),
        log=lambda _: None,
    )
    assert second["discovered_runs"] == 1
    assert second["captured_runs"] == 0
    assert second["already_archived_runs"] == 1
    assert all(
        not str(statement).strip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for statement in conn.statements
    )
