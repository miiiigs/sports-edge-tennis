from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[1]
UP = REPO / "db" / "migrations" / "0021_walkover_winner_optional.up.sql"
DOWN = REPO / "db" / "migrations" / "0021_walkover_winner_optional.down.sql"
SNAP = REPO / "db" / "schema" / "0021_walkover_winner_optional.sql"
MANIFEST = REPO / "db" / "migrations" / "manifest.json"


def _seed_match(db: psycopg.Connection) -> int:
    existing = db.execute(
        "SELECT match_id FROM matches ORDER BY match_id LIMIT 1"
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    observed_at = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    p1 = db.execute(
        "INSERT INTO players (canonical_name) VALUES ('Walkover One') RETURNING player_id"
    ).fetchone()[0]
    p2 = db.execute(
        "INSERT INTO players (canonical_name) VALUES ('Walkover Two') RETURNING player_id"
    ).fetchone()[0]
    tournament = db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Walkover Test', 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """
    ).fetchone()[0]
    edition = db.execute(
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2026, 'Walkover Test 2026')
        RETURNING tournament_edition_id
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
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, surface, round_code, best_of_sets,
            qualifying_flag, match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'QF', 3,
                FALSE, 'WALKOVER', %s, %s)
        """,
        (match_id, p1, p2, observed_at, observed_at, observed_at),
    )
    return int(match_id)


def test_walkover_without_winner_is_allowed(db: psycopg.Connection):
    match_id = _seed_match(db)
    observed_at = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    db.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        )
        VALUES (%s, 'WALKOVER', NULL, %s, %s)
        """,
        (match_id, observed_at, observed_at),
    )
    row = db.execute(
        "SELECT outcome_status::text, winner_side FROM match_result_observations "
        "WHERE match_id = %s",
        (match_id,),
    ).fetchone()
    assert row == ("WALKOVER", None)


def test_completed_without_winner_is_still_rejected(db: psycopg.Connection):
    match_id = _seed_match(db)
    observed_at = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            """
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, source_observed_at, as_of
            )
            VALUES (%s, 'COMPLETED', NULL, %s, %s)
            """,
            (match_id, observed_at, observed_at),
        )


def test_0021_snapshot_and_manifest():
    def normalise(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"--.*", "", text)).strip().lower()

    ups = sorted((REPO / "db" / "migrations").glob("*.up.sql"))
    concat = "\n".join(
        p.read_text(encoding="utf-8") for p in ups if p.name[:4] <= "0021"
    )
    assert normalise(concat) == normalise(SNAP.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = next(e for e in manifest["migrations"] if e["version"] == "0021")
    assert entry == {
        "version": "0021",
        "name": "walkover_winner_optional",
        "up": "db/migrations/0021_walkover_winner_optional.up.sql",
        "down": "db/migrations/0021_walkover_winner_optional.down.sql",
        "schema_snapshot": "db/schema/0021_walkover_winner_optional.sql",
    }
    assert UP.is_file() and DOWN.is_file()
