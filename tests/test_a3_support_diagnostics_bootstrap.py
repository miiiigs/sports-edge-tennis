"""Live-runner bootstrap guard must ensure migration 0020 before A3 diagnostics."""

from __future__ import annotations

import psycopg
import pytest

from sports_edge_tennis.live.production_batch import (
    _a3_support_diagnostics_schema_state,
    _ensure_a3_support_diagnostics_schema,
)


def test_ensure_is_idempotent_when_already_applied(db: psycopg.Connection):
    assert _ensure_a3_support_diagnostics_schema(db) == "EXISTING"


def test_ensure_applies_when_schema_absent(db: psycopg.Connection):
    db.execute("DROP TABLE IF EXISTS a3_support_diagnostics")
    assert _a3_support_diagnostics_schema_state(db) == (False, False)

    assert _ensure_a3_support_diagnostics_schema(db) == "APPLIED"

    assert _a3_support_diagnostics_schema_state(db) == (True, True)


def test_ensure_fails_closed_on_partial_schema(migrated_postgres: str):
    with psycopg.connect(migrated_postgres, autocommit=False) as conn:
        conn.execute(
            "DROP TRIGGER IF EXISTS trg_a3_support_diagnostics_append_only_mutation "
            "ON a3_support_diagnostics"
        )
        with pytest.raises(RuntimeError, match="partially applied"):
            _ensure_a3_support_diagnostics_schema(conn)
        conn.rollback()
