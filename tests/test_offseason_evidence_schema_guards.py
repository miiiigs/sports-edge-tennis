"""Database-level guards for the combined evidence foundation (0023 + 0024)."""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

from tests.abstention_helpers import utc

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "db/migrations/manifest.json").read_text())["migrations"]


def _apply(conn, path: str) -> None:
    conn.execute((ROOT / path).read_text(encoding="utf-8"))


def _tables(conn) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    }


def _logical_blocks(conn) -> tuple[set, set, set]:
    columns = set(
        conn.execute(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_schema = 'public'"
        ).fetchall()
    )
    constraints = set(
        conn.execute(
            "SELECT c.relname, con.conname, pg_get_constraintdef(con.oid) "
            "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public'"
        ).fetchall()
    )
    indexes = set(
        conn.execute(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public'"
        ).fetchall()
    )
    return columns, constraints, indexes


def test_0024_and_0023_downgrade_and_reupgrade_cleanly(db):
    _apply(db, MANIFEST[23]["down"])
    assert "oddspapi_event_identity_history" not in _tables(db)
    _apply(db, MANIFEST[23]["up"])
    assert "oddspapi_event_identity_history" in _tables(db)

    _apply(db, MANIFEST[22]["down"])
    assert "prediction_feature_snapshots" not in _tables(db)
    _apply(db, MANIFEST[22]["up"])
    assert "prediction_feature_snapshots" in _tables(db)

    trigger = db.execute(
        "SELECT tgname FROM pg_trigger WHERE tgrelid = "
        "to_regclass('public.prediction_feature_snapshots') "
        "AND NOT tgisinternal AND tgenabled <> 'D'"
    ).fetchall()
    assert trigger == [("trg_prediction_feature_snapshots_append_only_mutation",)]


def test_latest_cumulative_snapshot_matches_migrated_schema(db):
    # Build both states from an empty schema so runtime code that adds columns
    # to existing tables during other tests cannot contaminate the comparison.
    def _rebuild_migrated() -> None:
        db.execute("DROP SCHEMA public CASCADE")
        db.execute("CREATE SCHEMA public")
        for entry in MANIFEST:
            _apply(db, entry["up"])

    _rebuild_migrated()
    migrated = _logical_blocks(db)
    db.execute("DROP SCHEMA public CASCADE")
    db.execute("CREATE SCHEMA public")
    _apply(db, MANIFEST[-1]["schema_snapshot"])
    snapshot = _logical_blocks(db)
    assert migrated[0] == snapshot[0]
    assert migrated[1] == snapshot[1]
    assert migrated[2] == snapshot[2]
    _rebuild_migrated()


def test_combined_snapshot_contains_each_evidence_table_once():
    first = (ROOT / MANIFEST[22]["schema_snapshot"]).read_text(encoding="utf-8")
    combined = (ROOT / MANIFEST[23]["schema_snapshot"]).read_text(encoding="utf-8")
    assert combined.startswith(first.rstrip())
    for table in ("prediction_feature_snapshots", "oddspapi_event_identity_history"):
        assert combined.count(f"CREATE TABLE {table} (") == 1
    assert "fk_prediction_feature_snapshot_match_orientation" in combined
    assert "uq_match_versions_id_orientation" in combined
    assert "chk_prediction_feature_snapshot_uncertainty_source" in combined


def test_cumulative_snapshots_are_exact_migration_concatenations():
    for index in (22, 23, 24):
        expected = "\n\n".join(
            (ROOT / entry["up"]).read_text(encoding="utf-8").strip()
            for entry in MANIFEST[: index + 1]
        )
        actual = (ROOT / MANIFEST[index]["schema_snapshot"]).read_text(
            encoding="utf-8"
        )
        assert actual.strip() == expected


def _seed_prediction(db):
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

    match_id = insert_match(db)
    match_version_id, p1, p2 = db.execute(
        "SELECT match_version_id, player_1_id, player_2_id FROM match_versions "
        "WHERE match_id = %s",
        (match_id,),
    ).fetchone()
    run_id = insert_run(db, as_of=utc())
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    ids = finalize_matrix(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=configs,
        results=[
            make_result(code, utc(), status="PREDICTED", side=1, probability="0.6")
            for code in (A1, A2, A3)
        ],
        finalized_at=utc(minute=5),
    )
    return match_id, match_version_id, p1, p2, run_id, configs, ids[0]


def test_snapshot_rejects_reversed_player_orientation(db):
    (match_id, match_version_id, p1, p2, run_id, configs,
     prediction_id) = _seed_prediction(db)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            """
            INSERT INTO prediction_feature_snapshots (
                prediction_id, prediction_run_id, match_id, match_version_id,
                agent_config_version_id, agent_code, player_1_id, player_2_id,
                identity_complete, surface_known, main_draw_verified,
                model_version, feature_version, as_of, prediction_locked_at)
            VALUES (%s,%s,%s,%s,%s,'AGENT_1_SURFACE_ELO',%s,%s,false,false,true,
                    'm','f',%s,%s)
            """,
            (prediction_id, run_id, match_id, match_version_id,
             configs["AGENT_1_SURFACE_ELO"], p2, p1, utc(), utc(minute=5)),
        )


def test_snapshot_rejects_mismatched_prediction_lineage(db):
    from tests.abstention_helpers import A1, insert_match

    _match_id, match_version_id, p1, p2, run_id, configs, prediction_id = (
        _seed_prediction(db)
    )
    other_match = insert_match(db)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            """
            INSERT INTO prediction_feature_snapshots (
                prediction_id, prediction_run_id, match_id, match_version_id,
                agent_config_version_id, agent_code, player_1_id, player_2_id,
                identity_complete, surface_known, main_draw_verified,
                model_version, feature_version, as_of, prediction_locked_at)
            VALUES (%s,%s,%s,%s,%s,'AGENT_1_SURFACE_ELO',%s,%s,false,false,true,
                    'm','f',%s,%s)
            """,
            (prediction_id, run_id, other_match, match_version_id,
             configs[A1], p1, p2, utc(), utc(minute=5)),
        )


def test_history_table_rejects_invalid_orientation_and_resolution(db):
    from tests.abstention_helpers import insert_match

    match_id = insert_match(db)
    for orientation, resolution in (("NORMAL", "EXACT"), ("DIRECT", "GUESS")):
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                """
                INSERT INTO oddspapi_event_identity_history (
                    canonical_match_id, oddspapi_event_id, oddspapi_tournament_id,
                    player_orientation, resolution_status, resolved_at,
                    last_verified_at, event_type)
                VALUES (%s,'op-1',47624,%s,%s,%s,%s,'UPSERTED')
                """,
                (match_id, orientation, resolution, utc(), utc()),
            )


def test_0024_down_migration_refuses_to_drop_collected_history(db):
    from tests.abstention_helpers import insert_match

    match_id = insert_match(db)
    db.execute(
        """
        INSERT INTO oddspapi_event_identity_history (
            canonical_match_id, oddspapi_event_id, oddspapi_tournament_id,
            player_orientation, resolution_status, resolved_at,
            last_verified_at, event_type)
        VALUES (%s, 'op-guard', 47624, 'DIRECT', 'EXACT', %s, %s, 'UPSERTED')
        """,
        (match_id, utc(), utc()),
    )
    down = (ROOT / MANIFEST[23]["down"]).read_text(encoding="utf-8")
    assert "refusing to drop oddspapi_event_identity_history" in down
    with pytest.raises(psycopg.errors.RaiseException):
        _apply(db, MANIFEST[23]["down"])
    db.rollback()
    assert "oddspapi_event_identity_history" in _tables(db)
    assert db.execute(
        "SELECT count(*) FROM oddspapi_event_identity_history").fetchone()[0] == 1
