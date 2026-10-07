"""Provider fixture provenance + qualifying round fidelity tests."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.data.sources.api_tennis_live import (
    ingest_api_tennis_source_a_fixtures,
)
from sports_edge_tennis.live.api_tennis_fixture_compat import (
    normalize_api_tennis_fixture_rounds,
)

REPO = Path(__file__).resolve().parents[1]
UP = REPO / "db" / "migrations" / "0019_provider_fixture_metadata.up.sql"
DOWN = REPO / "db" / "migrations" / "0019_provider_fixture_metadata.down.sql"
SNAP = REPO / "db" / "schema" / "0019_provider_fixture_metadata.sql"
MANIFEST = REPO / "db" / "migrations" / "manifest.json"


def _fixture(**overrides):
    payload = {
        "event_key": "900001",
        "tournament_key": "1667",
        "tournament_name": "Shanghai",
        "event_type_type": "Atp Singles",
        "event_date": "2026-10-05",
        "event_time": "04:10",
        "event_status": "Not Started",
        "event_first_player": "Player One",
        "event_second_player": "Player Two",
        "first_player_key": "101",
        "second_player_key": "202",
        "tournament_round": "Shanghai - Semi-finals",
        "event_qualification": "True",
        "event_surface": "Hard",
    }
    payload.update(overrides)
    return payload


def _repo_kwargs(**overrides):
    kwargs = {
        "source_system": "API_TENNIS",
        "source_match_id": "12168332",
        "source_tournament_id": "1667",
        "source_tournament_season": 2026,
        "raw_tournament_name": "Shanghai",
        "raw_event_type_key": "265",
        "raw_event_type_type": "Atp Singles",
        "raw_tournament_round": "Shanghai - Semi-finals",
        "raw_event_qualification": "True",
        "raw_surface": "Hard",
        "raw_country": None,
        "raw_location": None,
        "raw_stage": None,
        "payload_hash": "SE_SOURCE_HASH_V1:aaaa",
        "observed_at": datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
    }
    kwargs.update(overrides)
    return kwargs


def test_repository_records_dedup_and_appends_on_change(db: psycopg.Connection):
    repo = HistoricalRepository(db, None)
    kwargs = _repo_kwargs()

    assert repo.record_provider_fixture_metadata(**kwargs) is True
    assert repo.record_provider_fixture_metadata(**kwargs) is False
    assert repo.record_provider_fixture_metadata(**kwargs) is False

    changed = _repo_kwargs(payload_hash="SE_SOURCE_HASH_V1:bbbb")
    assert repo.record_provider_fixture_metadata(**changed) is True

    rows = db.execute(
        "SELECT source_match_id, raw_tournament_round, raw_event_qualification, "
        "raw_event_type_type, payload_hash FROM provider_fixture_metadata "
        "ORDER BY provider_fixture_metadata_id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][:4] == ("12168332", "Shanghai - Semi-finals", "True", "Atp Singles")
    assert {row[4] for row in rows} == {
        "SE_SOURCE_HASH_V1:aaaa",
        "SE_SOURCE_HASH_V1:bbbb",
    }


def test_provider_provenance_is_append_only(db: psycopg.Connection):
    HistoricalRepository(db, None).record_provider_fixture_metadata(**_repo_kwargs())

    with pytest.raises(psycopg.Error):
        db.execute("UPDATE provider_fixture_metadata SET raw_tournament_name = 'x'")
    with pytest.raises(psycopg.Error):
        db.execute("DELETE FROM provider_fixture_metadata")


def test_ingest_retains_provider_metadata_and_q_rounds(db: psycopg.Connection):
    received_at = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    fixtures = normalize_api_tennis_fixture_rounds(
        [
            _fixture(event_key="12168332", tournament_round="Shanghai - Semi-finals"),
            _fixture(
                event_key="12168418",
                event_time="10:20",
                tournament_round="Shanghai - Final",
            ),
        ]
    )

    outcome = ingest_api_tennis_source_a_fixtures(
        db, fixtures, received_at=received_at
    )
    assert outcome.rows_ingested == 2

    provenance = db.execute(
        "SELECT source_match_id, source_tournament_id, source_tournament_season, "
        "raw_tournament_name, raw_event_type_type, raw_tournament_round, "
        "raw_event_qualification, raw_surface "
        "FROM provider_fixture_metadata ORDER BY source_match_id"
    ).fetchall()
    assert [row[0] for row in provenance] == ["12168332", "12168418"]
    for row in provenance:
        assert row[1] == "1667"
        assert row[2] == 2026
        assert row[3] == "Shanghai"
        assert row[4] == "Atp Singles"
        assert row[6] == "True"
        assert row[7] == "Hard"

    rounds = db.execute(
        "SELECT msi.source_match_id, mv.round_code, mv.qualifying_flag "
        "FROM match_versions mv JOIN match_source_ids msi ON msi.match_id = mv.match_id "
        "WHERE msi.source_match_id IN ('12168332', '12168418') "
        "ORDER BY msi.source_match_id"
    ).fetchall()
    assert [(row[0], row[1]) for row in rounds] == [
        ("12168332", "Q1"),
        ("12168418", "Q2"),
    ]
    assert all(row[2] is True for row in rounds)


def test_ingest_does_not_persist_api_key(db: psycopg.Connection):
    received_at = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    fixtures = normalize_api_tennis_fixture_rounds([_fixture()])
    ingest_api_tennis_source_a_fixtures(db, fixtures, received_at=received_at)

    blob = db.execute(
        "SELECT to_jsonb(provider_fixture_metadata)::text FROM provider_fixture_metadata"
    ).fetchone()[0]
    assert "APIkey" not in blob
    assert "api_key" not in blob
    assert "apikey" not in blob.lower()


def test_0019_snapshot_and_manifest():
    def normalise(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"--.*", "", text)).strip().lower()

    ups = sorted((REPO / "db" / "migrations").glob("*.up.sql"))
    concat = "\n".join(
        path.read_text(encoding="utf-8") for path in ups if path.name[:4] <= "0019"
    )
    assert normalise(concat) == normalise(SNAP.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = next(e for e in manifest["migrations"] if e["version"] == "0019")
    assert entry == {
        "version": "0019",
        "name": "provider_fixture_metadata",
        "up": "db/migrations/0019_provider_fixture_metadata.up.sql",
        "down": "db/migrations/0019_provider_fixture_metadata.down.sql",
        "schema_snapshot": "db/schema/0019_provider_fixture_metadata.sql",
    }
    assert UP.is_file() and DOWN.is_file()
