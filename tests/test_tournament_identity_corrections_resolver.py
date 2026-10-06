"""Resolver precedence tests for the tournament identity correction layer."""

from __future__ import annotations

import psycopg
import pytest

from sports_edge_tennis.data.repository import HistoricalRepository


def _repo(conn: psycopg.Connection) -> HistoricalRepository:
    return HistoricalRepository(conn, ingest_run_id=None)


def _tournament(conn, name, scope, tier) -> int:
    return conn.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES (%s, %s, %s) RETURNING tournament_id",
        (name, scope, tier),
    ).fetchone()[0]


def _map(conn, tid, source, key) -> None:
    conn.execute(
        "INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id) "
        "VALUES (%s, %s, %s)",
        (tid, source, key),
    )


def _correct(conn, source, key, canonical_id, reason="REVIEWED") -> None:
    conn.execute(
        "INSERT INTO tournament_identity_corrections "
        "(source_system, source_tournament_id, canonical_tournament_id, reason) "
        "VALUES (%s, %s, %s, %s)",
        (source, key, canonical_id, reason),
    )


def test_correction_takes_precedence_and_preserves_provenance(db: psycopg.Connection):
    canonical = _tournament(db, "Shanghai Masters", "ATP_MAIN", "MASTERS_1000")
    duplicate = _tournament(db, "Shanghai", "ATP_MAIN", "ATP_TOUR")
    _map(db, duplicate, "API_TENNIS", "1667")
    _correct(db, "API_TENNIS", "1667", canonical)

    resolved = _repo(db).get_or_create_tournament(
        source_system="API_TENNIS",
        source_tournament_id="1667",
        canonical_name="Shanghai",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
    )
    assert resolved == canonical

    # Historical provenance row is preserved intact.
    historical = db.execute(
        "SELECT tournament_id FROM tournament_source_ids "
        "WHERE source_system='API_TENNIS' AND source_tournament_id='1667'"
    ).fetchone()
    assert historical[0] == duplicate

    # No extra tournament was created.
    assert db.execute(
        "SELECT count(*) FROM tournaments WHERE canonical_name IN ('Shanghai','Shanghai Masters')"
    ).fetchone()[0] == 2


def test_no_correction_keeps_existing_mapping_behavior(db: psycopg.Connection):
    duplicate = _tournament(db, "Shanghai", "ATP_MAIN", "ATP_TOUR")
    _map(db, duplicate, "API_TENNIS", "1667")

    resolved = _repo(db).get_or_create_tournament(
        source_system="API_TENNIS",
        source_tournament_id="1667",
        canonical_name="Shanghai",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
    )
    assert resolved == duplicate


def test_scope_mismatch_fails_closed(db: psycopg.Connection):
    wrong_scope = _tournament(db, "Shanghai", "CHALLENGER", "CHALLENGER")
    _correct(db, "API_TENNIS", "1667", wrong_scope)

    with pytest.raises(ValueError, match="scope mismatch"):
        _repo(db).get_or_create_tournament(
            source_system="API_TENNIS",
            source_tournament_id="1667",
            canonical_name="Shanghai",
            tour_scope="ATP_MAIN",
            competition_tier="ATP_TOUR",
        )


def test_unrelated_provider_key_unaffected(db: psycopg.Connection):
    canonical = _tournament(db, "Shanghai Masters", "ATP_MAIN", "MASTERS_1000")
    _correct(db, "API_TENNIS", "1667", canonical)

    other = _repo(db).get_or_create_tournament(
        source_system="API_TENNIS",
        source_tournament_id="9999",
        canonical_name="New Event",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
    )
    created = db.execute(
        "SELECT canonical_name FROM tournaments WHERE tournament_id=%s", (other,)
    ).fetchone()
    assert created[0] == "New Event"
