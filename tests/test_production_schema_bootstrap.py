from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from sports_edge_tennis.live.production_batch import (
    _STAKING_CONSTRAINTS,
    _a1_evidence_cycle,
    _ensure_publication_control_schema,
    _ensure_strict_manager_v2_schema,
    ensure_model_identity_bridge_schema,
    _ensure_quarter_kelly_schema,
    _live_batch_gap_cycle,
    _live_operational_health_cycle,
)


class _Cursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, *, column_present: bool, constraints=()):
        self.column_present = column_present
        self.constraints = set(constraints)
        self.migration_execs = 0

    def execute(self, sql, params=()):
        if "information_schema.columns" in sql:
            return _Cursor(row=(self.column_present,))
        if "FROM pg_constraint" in sql:
            return _Cursor(rows=[(name,) for name in sorted(self.constraints)])
        if "ALTER TABLE decisions" in sql:
            self.migration_execs += 1
            self.column_present = True
            self.constraints = set(_STAKING_CONSTRAINTS)
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {str(sql)[:80]}")


def test_existing_complete_schema_is_idempotent(tmp_path: Path):
    conn = _Conn(column_present=True, constraints=_STAKING_CONSTRAINTS)

    status = _ensure_quarter_kelly_schema(
        conn,
        migration_path=tmp_path / "must-not-be-read.sql",
    )

    assert status == "EXISTING"
    assert conn.migration_execs == 0


def test_missing_schema_applies_committed_migration_then_verifies(tmp_path: Path):
    migration = tmp_path / "0010.sql"
    migration.write_text("BEGIN;\nALTER TABLE decisions ADD COLUMN example TEXT;\nCOMMIT;\n")
    conn = _Conn(column_present=False)

    status = _ensure_quarter_kelly_schema(conn, migration_path=migration)

    assert status == "APPLIED"
    assert conn.migration_execs == 1
    assert conn.column_present is True
    assert conn.constraints == set(_STAKING_CONSTRAINTS)


def test_partial_schema_fails_closed_without_running_migration(tmp_path: Path):
    one_constraint = next(iter(_STAKING_CONSTRAINTS))
    conn = _Conn(column_present=True, constraints={one_constraint})

    with pytest.raises(RuntimeError, match="partially applied"):
        _ensure_quarter_kelly_schema(conn, migration_path=tmp_path / "0010.sql")

    assert conn.migration_execs == 0


def test_a1_evidence_cycle_is_fail_isolated(monkeypatch, capsys):
    def fail_connect(*args, **kwargs):
        raise RuntimeError("diagnostic unavailable")

    monkeypatch.setattr(
        "sports_edge_tennis.live.production_batch.psycopg.connect",
        fail_connect,
    )

    _a1_evidence_cycle("postgresql://example")

    assert "A1_LIVE_EVIDENCE_FAILED error_type=RuntimeError" in capsys.readouterr().out


class _GapConn:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        assert "FROM prediction_runs" in sql
        assert params
        return _Cursor(row=self.row)


def test_live_batch_gap_cycle_alerts_when_previous_completed_run_is_too_old(
    monkeypatch,
    capsys,
):
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    previous = now - timedelta(minutes=117)
    monkeypatch.setattr(
        "sports_edge_tennis.live.production_batch.psycopg.connect",
        lambda *args, **kwargs: _GapConn((299, previous)),
    )

    _live_batch_gap_cycle(
        "postgresql://example",
        cycle_started_at=now,
        threshold_minutes=90,
    )

    out = capsys.readouterr().out
    assert "LIVE_BATCH_GAP_ALERT" in out
    assert "previous_prediction_run_id=299" in out
    assert "age_minutes=117" in out


def test_live_batch_gap_cycle_reports_ok_inside_hourly_window(monkeypatch, capsys):
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    previous = now - timedelta(minutes=58)
    monkeypatch.setattr(
        "sports_edge_tennis.live.production_batch.psycopg.connect",
        lambda *args, **kwargs: _GapConn((300, previous)),
    )

    _live_batch_gap_cycle(
        "postgresql://example",
        cycle_started_at=now,
        threshold_minutes=90,
    )

    out = capsys.readouterr().out
    assert "LIVE_BATCH_GAP_OK" in out
    assert "previous_prediction_run_id=300" in out
    assert "age_minutes=58" in out


def test_live_batch_gap_cycle_is_fail_isolated(monkeypatch, capsys):
    def fail_connect(*args, **kwargs):
        raise RuntimeError("gap check unavailable")

    monkeypatch.setattr(
        "sports_edge_tennis.live.production_batch.psycopg.connect",
        fail_connect,
    )

    _live_batch_gap_cycle(
        "postgresql://example",
        cycle_started_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    )

    assert "LIVE_BATCH_GAP_CHECK_FAILED error_type=RuntimeError" in capsys.readouterr().out


def test_live_operational_health_cycle_forwards_failed_slates(monkeypatch, capsys):
    captured = {}

    class _HealthConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "sports_edge_tennis.live.production_batch.psycopg.connect",
        lambda *args, **kwargs: _HealthConn(),
    )

    def fake_evaluate(conn, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status="WARNING",
            freshness_state="FRESH",
            completed_runs=2,
            target_count=90,
            latest_finalized_at=datetime(2026, 9, 19, 2, 3, tzinfo=UTC),
            error_count=1,
            notes="failed_slates=1 partial_slate_failure=1",
        )

    monkeypatch.setattr(
        "sports_edge_tennis.live.production_batch.evaluate_live_operational_health",
        fake_evaluate,
    )
    monkeypatch.setattr(
        "sports_edge_tennis.live.production_batch.persist_live_operational_health",
        lambda *args, **kwargs: None,
    )

    _live_operational_health_cycle(
        "postgresql://example",
        cycle_started_at=datetime(2026, 9, 19, 2, 0, tzinfo=UTC),
        live_status=0,
        failed_slates=1,
    )

    assert captured["failed_slates"] == 1
    assert "LIVE_OPERATIONAL_HEALTH status=WARNING" in capsys.readouterr().out


def test_live_operational_health_cycle_is_fail_isolated(monkeypatch, capsys):
    def fail_connect(*args, **kwargs):
        raise RuntimeError("health store unavailable")

    monkeypatch.setattr(
        "sports_edge_tennis.live.production_batch.psycopg.connect",
        fail_connect,
    )

    _live_operational_health_cycle(
        "postgresql://example",
        cycle_started_at=datetime(2026, 9, 18, 16, 0, tzinfo=UTC),
        live_status=0,
    )

    assert (
        "LIVE_OPERATIONAL_HEALTH_WRITE_FAILED error_type=RuntimeError"
        in capsys.readouterr().out
    )



class _PublicationSchemaConn:
    def __init__(self, *, table_present: bool, trigger_present: bool):
        self.table_present = table_present
        self.trigger_present = trigger_present
        self.migration_execs = 0

    def execute(self, sql, params=()):
        rendered = str(sql)
        if "to_regclass('public.publication_control_events')" in rendered:
            return _Cursor(row=(self.table_present,))
        if "FROM pg_trigger" in rendered:
            return _Cursor(row=(self.trigger_present,))
        if "CREATE TABLE publication_control_events" in rendered:
            self.migration_execs += 1
            self.table_present = True
            self.trigger_present = True
            return _Cursor()
        raise AssertionError(f"unexpected publication schema SQL: {rendered[:100]}")


def test_publication_control_schema_existing_is_idempotent(tmp_path: Path):
    conn = _PublicationSchemaConn(table_present=True, trigger_present=True)

    status = _ensure_publication_control_schema(
        conn,
        migration_path=tmp_path / "must-not-be-read.sql",
    )

    assert status == "EXISTING"
    assert conn.migration_execs == 0


def test_publication_control_schema_missing_applies_then_verifies(tmp_path: Path):
    migration = tmp_path / "0012.sql"
    migration.write_text(
        "BEGIN;\nCREATE TABLE publication_control_events (id BIGINT);\nCOMMIT;\n"
    )
    conn = _PublicationSchemaConn(table_present=False, trigger_present=False)

    status = _ensure_publication_control_schema(conn, migration_path=migration)

    assert status == "APPLIED"
    assert conn.migration_execs == 1
    assert conn.table_present is True
    assert conn.trigger_present is True


def test_publication_control_partial_schema_fails_closed(tmp_path: Path):
    conn = _PublicationSchemaConn(table_present=True, trigger_present=False)

    with pytest.raises(RuntimeError, match="partially applied"):
        _ensure_publication_control_schema(
            conn,
            migration_path=tmp_path / "0012.sql",
        )

    assert conn.migration_execs == 0



class _BridgeSchemaConn:
    def __init__(self, *, table_present: bool, trigger_present: bool):
        self.table_present = table_present
        self.trigger_present = trigger_present
        self.migration_execs = 0

    def execute(self, sql, params=()):
        rendered = str(sql)
        if "to_regclass('public.model_identity_bridge_events')" in rendered:
            return _Cursor(row=(self.table_present,))
        if "FROM pg_trigger" in rendered:
            return _Cursor(row=(self.trigger_present,))
        if "CREATE TABLE model_identity_bridge_events" in rendered:
            self.migration_execs += 1
            self.table_present = True
            self.trigger_present = True
            return _Cursor()
        raise AssertionError(f"unexpected model bridge schema SQL: {rendered[:100]}")


def test_model_identity_bridge_schema_existing_is_idempotent(tmp_path: Path):
    conn = _BridgeSchemaConn(table_present=True, trigger_present=True)

    status = ensure_model_identity_bridge_schema(
        conn,
        migration_path=tmp_path / "must-not-be-read.sql",
    )

    assert status == "EXISTING"
    assert conn.migration_execs == 0


def test_model_identity_bridge_schema_missing_applies_then_verifies(tmp_path: Path):
    migration = tmp_path / "0013.sql"
    migration.write_text(
        "BEGIN;\nCREATE TABLE model_identity_bridge_events (id BIGINT);\nCOMMIT;\n"
    )
    conn = _BridgeSchemaConn(table_present=False, trigger_present=False)

    status = ensure_model_identity_bridge_schema(conn, migration_path=migration)

    assert status == "APPLIED"
    assert conn.migration_execs == 1
    assert conn.table_present is True
    assert conn.trigger_present is True


def test_model_identity_bridge_partial_schema_fails_closed(tmp_path: Path):
    conn = _BridgeSchemaConn(table_present=True, trigger_present=False)

    with pytest.raises(RuntimeError, match="partially applied"):
        ensure_model_identity_bridge_schema(
            conn,
            migration_path=tmp_path / "0013.sql",
        )

    assert conn.migration_execs == 0



class _StrictManagerSchemaConn:
    def __init__(self, *, row=None):
        self.row = row
        self.migration_execs = 0

    def execute(self, sql, params=()):
        rendered = str(sql)
        if "SELECT" in rendered and "policy_version = 'v2'" in rendered:
            return _Cursor(row=self.row)
        if "INSERT INTO manager_policy_versions" in rendered:
            self.migration_execs += 1
            self.row = (False, "0.080000", "0.000000", "0.500000")
            return _Cursor()
        raise AssertionError(f"unexpected strict manager schema SQL: {rendered[:100]}")


def test_strict_manager_v2_existing_is_idempotent(tmp_path: Path):
    conn = _StrictManagerSchemaConn(
        row=(False, "0.080000", "0.000000", "0.500000")
    )
    status = _ensure_strict_manager_v2_schema(
        conn,
        migration_path=tmp_path / "must-not-be-read.sql",
    )
    assert status == "EXISTING"
    assert conn.migration_execs == 0


def test_strict_manager_v2_missing_applies_then_verifies(tmp_path: Path):
    migration = tmp_path / "0014.sql"
    migration.write_text(
        "BEGIN;\nINSERT INTO manager_policy_versions(policy_key) VALUES ('x');\nCOMMIT;\n"
    )
    conn = _StrictManagerSchemaConn(row=None)
    status = _ensure_strict_manager_v2_schema(conn, migration_path=migration)
    assert status == "APPLIED"
    assert conn.migration_execs == 1


def test_strict_manager_v2_wrong_existing_row_fails_closed(tmp_path: Path):
    conn = _StrictManagerSchemaConn(
        row=(True, "0.080000", "0.060000", "0.500000")
    )
    with pytest.raises(RuntimeError, match="unexpected values"):
        _ensure_strict_manager_v2_schema(
            conn,
            migration_path=tmp_path / "0014.sql",
        )
    assert conn.migration_execs == 0


def test_production_main_fails_closed_when_provider_authority_blocks(monkeypatch, capsys):
    from sports_edge_tennis.live import production_batch

    class _Ctx:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(production_batch, "_db_dsn", lambda: "postgresql://example")
    monkeypatch.setattr(
        production_batch.psycopg,
        "connect",
        lambda *args, **kwargs: _Ctx(),
    )
    monkeypatch.setattr(
        production_batch,
        "load_provider_authority",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("TENNIS_PROVIDER_CUTOVER_BLOCKED TARGET_RUNTIME_NOT_ACTIVATABLE")
        ),
    )

    assert production_batch.main([]) == 2
    out = capsys.readouterr().out
    assert "PROVIDER_AUTHORITY_BLOCKED" in out
    assert "TARGET_RUNTIME_NOT_ACTIVATABLE" in out
