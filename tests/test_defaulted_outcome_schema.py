from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[1]
UP = REPO / "db" / "migrations" / "0022_defaulted_outcome.up.sql"
DOWN = REPO / "db" / "migrations" / "0022_defaulted_outcome.down.sql"
SNAP = REPO / "db" / "schema" / "0022_defaulted_outcome.sql"
MANIFEST = REPO / "db" / "migrations" / "manifest.json"


def _seed_match(db: psycopg.Connection) -> int:
    observed = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    p1 = db.execute(
        "INSERT INTO players (canonical_name) VALUES ('Default Winner') RETURNING player_id"
    ).fetchone()[0]
    p2 = db.execute(
        "INSERT INTO players (canonical_name) VALUES ('Defaulted Player') RETURNING player_id"
    ).fetchone()[0]
    tournament = db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Default Test', 'ATP_MAIN', 'ATP_500') RETURNING tournament_id
        """
    ).fetchone()[0]
    edition = db.execute(
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2026, 'Default Test 2026') RETURNING tournament_edition_id
        """,
        (tournament,),
    ).fetchone()[0]
    match_id = db.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition,),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, best_of_sets, match_status, source_observed_at, as_of
        ) VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_500', 3, 'DEFAULTED', %s, %s)
        """,
        (match_id, p1, p2, observed, observed, observed),
    )
    return int(match_id)


def test_defaulted_result_requires_winner_and_is_storable(db: psycopg.Connection):
    match_id = _seed_match(db)
    observed = datetime(2026, 10, 5, 12, 1, tzinfo=UTC)
    db.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        ) VALUES (%s, 'DEFAULTED', 1, %s, %s)
        """,
        (match_id, observed, observed),
    )
    assert db.execute(
        "SELECT outcome_status::text, winner_side FROM match_result_observations WHERE match_id=%s",
        (match_id,),
    ).fetchone() == ("DEFAULTED", 1)


def test_defaulted_without_declared_winner_fails_closed(db: psycopg.Connection):
    match_id = _seed_match(db)
    observed = datetime(2026, 10, 5, 12, 2, tzinfo=UTC)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            """
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, source_observed_at, as_of
            ) VALUES (%s, 'DEFAULTED', NULL, %s, %s)
            """,
            (match_id, observed, observed),
        )


def test_0022_snapshot_manifest_and_forward_only_rollback_contract():
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"--.*", "", text)).strip().lower()

    ups = sorted((REPO / "db" / "migrations").glob("*.up.sql"))
    concat = "\n".join(p.read_text(encoding="utf-8") for p in ups if p.name[:4] <= "0022")
    assert normalize(concat) == normalize(SNAP.read_text(encoding="utf-8"))

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = next(e for e in manifest["migrations"] if e["version"] == "0022")
    assert entry["name"] == "defaulted_outcome"
    assert entry["up"] == "db/migrations/0022_defaulted_outcome.up.sql"
    assert entry["down"] == "db/migrations/0022_defaulted_outcome.down.sql"
    assert entry["schema_snapshot"] == "db/schema/0022_defaulted_outcome.sql"
    assert "forward-only" in DOWN.read_text(encoding="utf-8")
