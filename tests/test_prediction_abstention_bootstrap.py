"""Live-runner bootstrap guard must ensure migration 0017 before prediction writes."""

from __future__ import annotations

import psycopg
import pytest

from sports_edge_tennis.live.production_batch import (
    _ensure_prediction_abstention_reason_schema,
    _prediction_abstention_schema_state,
)

_EXPECTED_CONSTRAINTS = {
    "chk_predictions_abstain_reason_code",
    "chk_predictions_abstain_reason_status",
}


def test_ensure_is_idempotent_when_already_applied(db: psycopg.Connection):
    assert _ensure_prediction_abstention_reason_schema(db) == "EXISTING"


def test_ensure_applies_when_schema_absent(db: psycopg.Connection):
    db.execute(
        """
        ALTER TABLE predictions
            DROP CONSTRAINT IF EXISTS chk_predictions_abstain_reason_code,
            DROP CONSTRAINT IF EXISTS chk_predictions_abstain_reason_status,
            DROP COLUMN IF EXISTS abstain_reason_code
        """
    )
    assert _prediction_abstention_schema_state(db) == (False, frozenset(), False)

    assert _ensure_prediction_abstention_reason_schema(db) == "APPLIED"

    column_present, constraints, index_present = _prediction_abstention_schema_state(db)
    assert column_present and index_present
    assert set(constraints) == _EXPECTED_CONSTRAINTS


def test_ensure_fails_closed_on_partial_schema(migrated_postgres: str):
    # Use a non-autocommit connection and roll back so later tests see the full schema.
    with psycopg.connect(migrated_postgres, autocommit=False) as conn:
        conn.execute(
            """
            ALTER TABLE predictions
                DROP CONSTRAINT IF EXISTS chk_predictions_abstain_reason_code,
                DROP CONSTRAINT IF EXISTS chk_predictions_abstain_reason_status
            """
        )
        with pytest.raises(RuntimeError, match="partially applied"):
            _ensure_prediction_abstention_reason_schema(conn)
        conn.rollback()
