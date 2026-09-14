from __future__ import annotations

from pathlib import Path

import pytest

from sports_edge_tennis.live.production_batch import (
    _STAKING_CONSTRAINTS,
    _ensure_quarter_kelly_schema,
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
