from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sports_edge_tennis.live.production_batch import (
    _STAKING_CONSTRAINTS,
    _a1_evidence_cycle,
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
