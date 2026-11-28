"""Schema and guard tests for migration 0025 identity provenance."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "db/migrations/manifest.json").read_text())["migrations"]
ENTRY_0025 = MANIFEST[24]
assert ENTRY_0025["version"] == "0025"


def _player(conn: psycopg.Connection, name: str, *, created_at: datetime | None = None) -> int:
    created = created_at or datetime(2024, 1, 1, tzinfo=UTC)
    return conn.execute(
        "INSERT INTO players (canonical_name, status, created_at, updated_at) "
        "VALUES (%s, 'ACTIVE', %s, %s) RETURNING player_id",
        (name, created, created),
    ).fetchone()[0]


def _versions(conn: psycopg.Connection, player_id: int) -> list[tuple]:
    return conn.execute(
        "SELECT canonical_name, valid_from FROM player_name_versions "
        "WHERE player_id = %s ORDER BY valid_from",
        (player_id,),
    ).fetchall()


def test_new_player_and_rename_create_append_only_name_versions(db: psycopg.Connection):
    player = _player(db, "Initial Name")
    rows = _versions(db, player)
    assert [row[0] for row in rows] == ["Initial Name"]
    assert rows[0][1] == datetime(2024, 1, 1, tzinfo=UTC)

    rename_at = datetime(2025, 6, 1, tzinfo=UTC)
    db.execute(
        "UPDATE players SET canonical_name = 'Renamed Player', updated_at = %s "
        "WHERE player_id = %s",
        (rename_at, player),
    )
    rows = _versions(db, player)
    assert [row[0] for row in rows] == ["Initial Name", "Renamed Player"]
    assert rows[1][1] == rename_at

    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute(
            "UPDATE player_name_versions SET canonical_name = 'Forged' "
            "WHERE player_id = %s",
            (player,),
        )
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute("DELETE FROM player_name_versions WHERE player_id = %s", (player,))


def test_alias_insert_is_versioned_and_linkage_is_immutable(db: psycopg.Connection):
    player = _player(db, "Alias Player")
    other = _player(db, "Other Player")
    alias_id = db.execute(
        """
        INSERT INTO player_aliases (
            player_id, source_system, alias, normalized_alias,
            first_observed_at, created_at, updated_at
        )
        VALUES (%s, 'TENNIS_DATA', 'Alias Player', 'alias player', %s, %s, %s)
        RETURNING player_alias_id
        """,
        (
            player,
            datetime(2024, 2, 1, tzinfo=UTC),
            datetime(2024, 2, 1, tzinfo=UTC),
            datetime(2024, 2, 1, tzinfo=UTC),
        ),
    ).fetchone()[0]
    version = db.execute(
        "SELECT player_id, normalized_alias, valid_from FROM player_alias_versions "
        "WHERE player_alias_id = %s",
        (alias_id,),
    ).fetchone()
    assert version == (player, "alias player", datetime(2024, 2, 1, tzinfo=UTC))

    with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        db.execute(
            "UPDATE player_aliases SET player_id = %s WHERE player_alias_id = %s",
            (other, alias_id),
        )

    # Re-observation is the normal pipeline behaviour and must remain allowed.
    db.execute(
        "UPDATE player_aliases SET last_observed_at = %s, updated_at = %s "
        "WHERE player_alias_id = %s",
        (
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 1, tzinfo=UTC),
            alias_id,
        ),
    )


def test_alias_version_rows_are_append_only(db: psycopg.Connection):
    player = _player(db, "Versioned Alias")
    db.execute(
        "INSERT INTO player_aliases (player_id, source_system, alias, normalized_alias) "
        "VALUES (%s, 'TENNIS_DATA', 'Versioned Alias', 'versioned alias')",
        (player,),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute(
            "DELETE FROM player_alias_versions WHERE player_id = %s", (player,)
        )


def test_provenance_row_is_append_only(db: psycopg.Connection):
    from tests.abstention_helpers import insert_match, insert_run, utc

    match_id = insert_match(db, match_start_at=utc(hour=12))
    run_id = insert_run(db, as_of=utc(hour=8))
    version_id, p1, p2 = db.execute(
        "SELECT match_version_id, player_1_id, player_2_id FROM match_versions "
        "WHERE match_id = %s",
        (match_id,),
    ).fetchone()
    db.execute(
        """
        INSERT INTO identity_resolution_provenance (
            prediction_run_id, match_id, match_version_id, predicted_at,
            original_player_id, model_player_id, resolution_method, evidence,
            name_source, reference_policy, reference_sha256, record_sha256,
            capture_source
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'NO_HISTORICAL_CANDIDATE', 'none',
                'PIT_CANONICAL_NAME', 'identity_reference_pit_v3', %s, %s,
                'LIVE_RUNTIME')
        """,
        (run_id, match_id, version_id, utc(hour=8), p1, p2, "a" * 64, "b" * 64),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute(
            "UPDATE identity_resolution_provenance SET model_player_id = %s",
            (p1,),
        )


def test_snapshot_contains_identity_tables_once():
    snapshot = (ROOT / ENTRY_0025["schema_snapshot"]).read_text(encoding="utf-8")
    for table in (
        "player_name_versions",
        "player_alias_versions",
        "identity_resolution_provenance",
    ):
        assert snapshot.count(f"CREATE TABLE {table} (") == 1
    assert "trg_player_aliases_identity_guard" in snapshot
    assert "capture_player_name_version" in snapshot


def test_0025_downgrades_and_reupgrades_cleanly(db: psycopg.Connection):
    db.execute((ROOT / ENTRY_0025["down"]).read_text(encoding="utf-8"))
    tables = {
        row[0]
        for row in db.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    }
    assert "identity_resolution_provenance" not in tables
    assert "player_name_versions" not in tables

    db.execute((ROOT / ENTRY_0025["up"]).read_text(encoding="utf-8"))
    tables = {
        row[0]
        for row in db.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    }
    assert "identity_resolution_provenance" in tables
    assert "player_name_versions" in tables
