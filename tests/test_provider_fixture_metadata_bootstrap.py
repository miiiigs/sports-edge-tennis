"""Live-runner bootstrap guard must ensure migration 0019 before ingest writes."""

from __future__ import annotations

import psycopg
import pytest

from sports_edge_tennis.live.production_batch import (
    _ensure_provider_fixture_metadata_schema,
    _provider_fixture_metadata_schema_state,
)


def test_ensure_is_idempotent_when_already_applied(db: psycopg.Connection):
    assert _ensure_provider_fixture_metadata_schema(db) == "EXISTING"


def test_ensure_applies_when_schema_absent(db: psycopg.Connection):
    db.execute("DROP TABLE IF EXISTS provider_fixture_metadata")
    assert _provider_fixture_metadata_schema_state(db) == (False, False)

    assert _ensure_provider_fixture_metadata_schema(db) == "APPLIED"

    assert _provider_fixture_metadata_schema_state(db) == (True, True)


def test_ensure_fails_closed_on_partial_schema(migrated_postgres: str):
    # Use a non-autocommit connection and roll back so later tests see the full schema.
    with psycopg.connect(migrated_postgres, autocommit=False) as conn:
        conn.execute(
            "DROP TRIGGER IF EXISTS trg_provider_fixture_metadata_append_only_mutation "
            "ON provider_fixture_metadata"
        )
        with pytest.raises(RuntimeError, match="partially applied"):
            _ensure_provider_fixture_metadata_schema(conn)
        conn.rollback()
