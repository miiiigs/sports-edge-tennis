"""Schema tests for migration 0017 (prediction_abstention_reasons)."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

from tests.abstention_helpers import (
    A1,
    A2,
    insert_match,
    insert_run,
    insert_target,
    raw_insert_and_finalize,
    seed_agent_configs,
    utc,
)

REPO = Path(__file__).resolve().parents[1]
UP = REPO / "db" / "migrations" / "0017_prediction_abstention_reasons.up.sql"
DOWN = REPO / "db" / "migrations" / "0017_prediction_abstention_reasons.down.sql"
SNAP = REPO / "db" / "schema" / "0017_prediction_abstention_reasons.sql"
MANIFEST = REPO / "db" / "migrations" / "manifest.json"


def _column_names(db: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='predictions'"
        ).fetchall()
    }


def test_0017_column_is_nullable_and_exists(db: psycopg.Connection):
    assert "abstain_reason_code" in _column_names(db)
    nullable = db.execute(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='predictions' "
        "AND column_name='abstain_reason_code'"
    ).fetchone()[0]
    assert nullable == "YES"


def test_0017_constraints_and_index_present(db: psycopg.Connection):
    constraints = {
        row[0]
        for row in db.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid='public.predictions'::regclass"
        ).fetchall()
    }
    assert "chk_predictions_abstain_reason_code" in constraints
    assert "chk_predictions_abstain_reason_status" in constraints

    indexes = {
        row[0]
        for row in db.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='predictions'"
        ).fetchall()
    }
    assert "idx_predictions_agent_status_abstain_reason" in indexes


def test_0017_rejects_non_canonical_reason(db: psycopg.Connection):
    match_id = insert_match(db)
    run_id = insert_run(db, as_of=utc())
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        raw_insert_and_finalize(
            db,
            run_id=run_id,
            match_id=match_id,
            configs=configs,
            specs=((A2, "ABSTAINED", None, None, None, "NOT_A_REAL_REASON"),),
            finalized_at=utc(),
        )


def test_0017_rejects_reason_on_predicted_row(db: psycopg.Connection):
    match_id = insert_match(db)
    run_id = insert_run(db, as_of=utc())
    insert_target(db, run_id=run_id, match_id=match_id)
    configs = seed_agent_configs(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        raw_insert_and_finalize(
            db,
            run_id=run_id,
            match_id=match_id,
            configs=configs,
            specs=((A1, "PREDICTED", 1, "0.700000", "agent1_raw_probability_v1", "WEAK_SIGNAL"),),
            finalized_at=utc(),
        )


def test_0017_down_and_up_cycle(db: psycopg.Connection):
    db.execute(DOWN.read_text(encoding="utf-8"))
    assert "abstain_reason_code" not in _column_names(db)
    db.execute(UP.read_text(encoding="utf-8"))
    assert "abstain_reason_code" in _column_names(db)


def test_0017_snapshot_matches_concatenated_migrations():
    def normalise(text: str) -> str:
        import re

        text = re.sub(r"--.*", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip().lower()

    ups = sorted((REPO / "db" / "migrations").glob("*.up.sql"))
    concat = "\n".join(p.read_text(encoding="utf-8") for p in ups if p.name[:4] <= "0017")
    assert normalise(concat) == normalise(SNAP.read_text(encoding="utf-8"))


def test_0017_manifest_entry():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = next(e for e in manifest["migrations"] if e["version"] == "0017")
    assert entry == {
        "version": "0017",
        "name": "prediction_abstention_reasons",
        "up": "db/migrations/0017_prediction_abstention_reasons.up.sql",
        "down": "db/migrations/0017_prediction_abstention_reasons.down.sql",
        "schema_snapshot": "db/schema/0017_prediction_abstention_reasons.sql",
    }


def test_0017_snapshot_contains_column_and_constraint():
    text = SNAP.read_text(encoding="utf-8")
    assert "abstain_reason_code" in text
    assert "chk_predictions_abstain_reason_code" in text
