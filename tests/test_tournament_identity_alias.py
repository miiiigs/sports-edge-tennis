"""Deterministic canonical tournament alias resolution + repository reuse."""

from __future__ import annotations

import psycopg
import pytest

from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.data.tournament_identity import (
    normalize_tournament_name,
    resolve_canonical_tournament_name,
)


def test_normalize_and_alias_lookup():
    assert normalize_tournament_name("  Shanghai  ") == "SHANGHAI"
    assert (
        resolve_canonical_tournament_name("API_TENNIS", "Shanghai", "ATP_MAIN")
        == "Shanghai Masters"
    )
    assert (
        resolve_canonical_tournament_name("API_TENNIS", "Rome", "ATP_MAIN")
        == "Rome Masters"
    )
    # Unlisted / wrong scope never maps.
    assert (
        resolve_canonical_tournament_name("API_TENNIS", "Atlantis", "ATP_MAIN")
        == "Atlantis"
    )
    assert (
        resolve_canonical_tournament_name("API_TENNIS", "Shanghai", "CHALLENGER")
        == "Shanghai"
    )
    assert (
        resolve_canonical_tournament_name("SPORTRADAR", "Shanghai", "ATP_MAIN")
        == "Shanghai"
    )


def _repo(conn: psycopg.Connection) -> HistoricalRepository:
    return HistoricalRepository(conn, ingest_run_id=None)


def _insert_tournament(conn, name, scope, tier) -> int:
    return conn.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES (%s, %s, %s) RETURNING tournament_id",
        (name, scope, tier),
    ).fetchone()[0]


def test_provider_name_reuses_canonical_masters(db: psycopg.Connection):
    canonical = _insert_tournament(db, "Shanghai Masters", "ATP_MAIN", "MASTERS_1000")
    repo = _repo(db)

    tournament_id = repo.get_or_create_tournament(
        source_system="API_TENNIS",
        source_tournament_id="999999",
        canonical_name="Shanghai",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
    )

    assert tournament_id == canonical
    count = db.execute(
        "SELECT count(*) FROM tournaments WHERE canonical_name = 'Shanghai'"
    ).fetchone()[0]
    assert count == 0
    # Provider id is registered against the canonical row.
    mapped = db.execute(
        "SELECT tournament_id FROM tournament_source_ids "
        "WHERE source_system='API_TENNIS' AND source_tournament_id='999999'"
    ).fetchone()
    assert mapped is not None and mapped[0] == canonical


def test_challenger_namesake_is_not_merged(db: psycopg.Connection):
    _insert_tournament(db, "Shanghai Masters", "ATP_MAIN", "MASTERS_1000")
    repo = _repo(db)

    # A Challenger "Shanghai" is a different scope and must not reuse the Masters row.
    tournament_id = repo.get_or_create_tournament(
        source_system="API_TENNIS",
        source_tournament_id="888888",
        canonical_name="Shanghai",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
    )
    new_row = db.execute(
        "SELECT canonical_name, tour_scope::text, competition_tier::text "
        "FROM tournaments WHERE tournament_id = %s",
        (tournament_id,),
    ).fetchone()
    assert new_row == ("Shanghai", "CHALLENGER", "CHALLENGER")


def test_ambiguous_alias_fails_closed(db: psycopg.Connection):
    _insert_tournament(db, "Shanghai Masters", "ATP_MAIN", "MASTERS_1000")
    _insert_tournament(db, "Shanghai Masters", "ATP_MAIN", "MASTERS_1000")
    repo = _repo(db)
    with pytest.raises(ValueError, match="ambiguous canonical tournament alias"):
        repo.get_or_create_tournament(
            source_system="API_TENNIS",
            source_tournament_id="777777",
            canonical_name="Shanghai",
            tour_scope="ATP_MAIN",
            competition_tier="ATP_TOUR",
        )


def test_unlisted_provider_name_still_creates(db: psycopg.Connection):
    repo = _repo(db)
    tournament_id = repo.get_or_create_tournament(
        source_system="API_TENNIS",
        source_tournament_id="666666",
        canonical_name="Some New Open",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
    )
    row = db.execute(
        "SELECT canonical_name, competition_tier::text FROM tournaments WHERE tournament_id=%s",
        (tournament_id,),
    ).fetchone()
    assert row == ("Some New Open", "ATP_TOUR")
