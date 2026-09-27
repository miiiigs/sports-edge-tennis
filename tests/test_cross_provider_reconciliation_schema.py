"""Schema-only tests for migration 0016 (cross_provider_match_reconciliations)."""

from __future__ import annotations

from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[1]
UP = REPO / "db" / "migrations" / "0016_cross_provider_match_reconciliations.up.sql"
DOWN = REPO / "db" / "migrations" / "0016_cross_provider_match_reconciliations.down.sql"
SNAP = REPO / "db" / "schema" / "0016_cross_provider_match_reconciliations.sql"


def _columns(db: psycopg.Connection) -> set[str]:
    return {
        r[0]
        for r in db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='cross_provider_match_reconciliations'"
        ).fetchall()
    }


def test_0016_table_columns_and_constraints(db: psycopg.Connection):
    assert db.execute("SELECT to_regclass('public.cross_provider_match_reconciliations')").fetchone()[0] is not None
    assert _columns(db) >= {
        "reconciliation_id",
        "canonical_target_match_id",
        "evidence_match_id",
        "target_source_system",
        "target_source_match_id",
        "evidence_source_system",
        "evidence_source_match_id",
        "evidence_result_observation_id",
        "classification",
        "player_order_mapping",
        "reconciliation_policy_version",
        "reviewed_at",
        "evidence_payload",
        "created_at",
    }
    constraints = {
        r[0]
        for r in db.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid='public.cross_provider_match_reconciliations'::regclass"
        ).fetchall()
    }
    assert "uq_cross_provider_match_reconciliations_identity" in constraints
    assert "chk_cross_provider_match_reconciliations_not_self" in constraints
    fks = db.execute(
        "SELECT count(*) FROM pg_constraint WHERE conrelid='public.cross_provider_match_reconciliations'::regclass AND contype='f'"
    ).fetchone()[0]
    assert int(fks) == 3


def test_0016_append_only_trigger_present(db: psycopg.Connection):
    trig = db.execute(
        "SELECT tgname FROM pg_trigger WHERE tgrelid='public.cross_provider_match_reconciliations'::regclass AND NOT tgisinternal"
    ).fetchall()
    assert "trg_cross_provider_match_reconciliations_append_only_mutation" in {t[0] for t in trig}


def test_0016_down_and_up_cycle(db: psycopg.Connection):
    db.execute(DOWN.read_text(encoding="utf-8"))
    assert db.execute("SELECT to_regclass('public.cross_provider_match_reconciliations')").fetchone()[0] is None
    # shared append-only function must survive the down migration
    assert db.execute("SELECT to_regproc('raise_append_only_mutation')").fetchone()[0] is not None
    db.execute(UP.read_text(encoding="utf-8"))
    assert db.execute("SELECT to_regclass('public.cross_provider_match_reconciliations')").fetchone()[0] is not None


def test_0016_snapshot_contains_table():
    text = SNAP.read_text(encoding="utf-8")
    assert "CREATE TABLE cross_provider_match_reconciliations" in text
    assert "uq_cross_provider_match_reconciliations_identity" in text
