"""Schema tests for migration 0018 (tournament_identity_corrections)."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[1]
UP = REPO / "db" / "migrations" / "0018_tournament_identity_corrections.up.sql"
DOWN = REPO / "db" / "migrations" / "0018_tournament_identity_corrections.down.sql"
SNAP = REPO / "db" / "schema" / "0018_tournament_identity_corrections.sql"
MANIFEST = REPO / "db" / "migrations" / "manifest.json"


def _columns(db: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='tournament_identity_corrections'"
        ).fetchall()
    }


def test_0018_table_columns_and_constraints(db: psycopg.Connection):
    assert _columns(db) >= {
        "correction_id",
        "source_system",
        "source_tournament_id",
        "canonical_tournament_id",
        "reason",
        "evidence_ref",
        "created_at",
    }
    constraints = {
        row[0]
        for row in db.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='public.tournament_identity_corrections'::regclass"
        ).fetchall()
    }
    assert "uq_tournament_identity_corrections_provider" in constraints
    fks = db.execute(
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid='public.tournament_identity_corrections'::regclass AND contype='f'"
    ).fetchone()[0]
    assert int(fks) == 1


def test_0018_append_only_trigger_blocks_update_and_delete(db: psycopg.Connection):
    tid = db.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Correction Target', 'ATP_MAIN', 'MASTERS_1000') RETURNING tournament_id"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO tournament_identity_corrections "
        "(source_system, source_tournament_id, canonical_tournament_id, reason) "
        "VALUES ('API_TENNIS', '1667', %s, 'TEST')",
        (tid,),
    )
    with pytest.raises(psycopg.Error):
        db.execute(
            "UPDATE tournament_identity_corrections SET reason = 'x' "
            "WHERE source_system='API_TENNIS' AND source_tournament_id='1667'"
        )
    with pytest.raises(psycopg.Error):
        db.execute(
            "DELETE FROM tournament_identity_corrections "
            "WHERE source_system='API_TENNIS' AND source_tournament_id='1667'"
        )


def test_0018_unique_provider_correction(db: psycopg.Connection):
    tid = db.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Correction Target', 'ATP_MAIN', 'MASTERS_1000') RETURNING tournament_id"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO tournament_identity_corrections "
        "(source_system, source_tournament_id, canonical_tournament_id, reason) "
        "VALUES ('API_TENNIS', '1667', %s, 'TEST')",
        (tid,),
    )
    with pytest.raises(psycopg.Error):
        db.execute(
            "INSERT INTO tournament_identity_corrections "
            "(source_system, source_tournament_id, canonical_tournament_id, reason) "
            "VALUES ('API_TENNIS', '1667', %s, 'DUP')",
            (tid,),
        )


def test_0018_down_and_up_cycle(db: psycopg.Connection):
    db.execute(DOWN.read_text(encoding="utf-8"))
    assert db.execute(
        "SELECT to_regclass('public.tournament_identity_corrections')"
    ).fetchone()[0] is None
    db.execute(UP.read_text(encoding="utf-8"))
    assert db.execute(
        "SELECT to_regclass('public.tournament_identity_corrections')"
    ).fetchone()[0] is not None


def test_0018_snapshot_and_manifest():
    def normalise(text: str) -> str:
        import re

        return re.sub(r"\s+", " ", re.sub(r"--.*", "", text)).strip().lower()

    ups = sorted((REPO / "db" / "migrations").glob("*.up.sql"))
    concat = "\n".join(p.read_text(encoding="utf-8") for p in ups if p.name[:4] <= "0018")
    assert normalise(concat) == normalise(SNAP.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = next(e for e in manifest["migrations"] if e["version"] == "0018")
    assert entry == {
        "version": "0018",
        "name": "tournament_identity_corrections",
        "up": "db/migrations/0018_tournament_identity_corrections.up.sql",
        "down": "db/migrations/0018_tournament_identity_corrections.down.sql",
        "schema_snapshot": "db/schema/0018_tournament_identity_corrections.sql",
    }
