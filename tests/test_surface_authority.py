from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.live.surface_authority import (
    SURFACE_SOURCE_MATCH_VERSION,
    SURFACE_SOURCE_TOURNAMENT_EDITION,
    SURFACE_SOURCE_UNKNOWN,
    build_surface_authority_report,
    resolve_authoritative_surface,
)

INGESTED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
MATCH_START = datetime(2026, 9, 9, 18, 30, tzinfo=UTC)
AS_OF = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
FUTURE = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)


def _run(db: psycopg.Connection) -> int:
    return int(
        db.execute(
            """
            INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
            VALUES ('SEED', 'surface_authority_test', %s, 'RUNNING')
            RETURNING ingest_run_id
            """,
            (INGESTED_AT,),
        ).fetchone()[0]
    )


def _seed_match(
    db: psycopg.Connection,
    *,
    edition_surface: str | None,
    version_surface: str | None,
    source_match_id: str = "td-surface-1",
) -> int:
    repository = HistoricalRepository(db, _run(db))
    p1 = repository.create_player_with_source_id(
        source_system="TENNIS_DATA",
        source_player_id="sp1",
        canonical_name="Surface Player One",
        observed_at=INGESTED_AT,
    )
    p2 = repository.create_player_with_source_id(
        source_system="TENNIS_DATA",
        source_player_id="sp2",
        canonical_name="Surface Player Two",
        observed_at=INGESTED_AT,
    )
    tournament_id = repository.get_or_create_tournament(
        source_system="TENNIS_DATA",
        source_tournament_id=f"st-{source_match_id}",
        canonical_name=f"Surface Event {source_match_id}",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
    )
    edition_id = repository.get_or_create_tournament_edition(
        tournament_id=tournament_id,
        season_year=MATCH_START.year,
        edition_name=f"Surface Event {source_match_id} {MATCH_START.year}",
        surface=edition_surface,
        start_date=MATCH_START.date(),
        end_date=None,
    )
    locator = repository.upsert_match_version(
        tournament_edition_id=edition_id,
        source_system="TENNIS_DATA",
        source_match_id=source_match_id,
        source_record_hash=f"hash:{source_match_id}",
        source_record_locator=f"tennis_data:{source_match_id}",
        source_file_name="test",
        source_version_hash=f"vhash:{source_match_id}",
        player_1_id=p1,
        player_2_id=p2,
        match_start_at=MATCH_START,
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        surface=version_surface,
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        source_observed_at=INGESTED_AT,
        as_of=INGESTED_AT,
    )
    db.execute(
        """
        UPDATE tournament_editions
        SET updated_at = %s
        WHERE tournament_edition_id = %s
        """,
        (INGESTED_AT, edition_id),
    )
    return locator.match_id


def test_edition_surface_propagates_when_match_version_unknown(db: psycopg.Connection):
    match_id = _seed_match(db, edition_surface="HARD", version_surface=None)
    resolution = resolve_authoritative_surface(db, match_id=match_id, as_of=AS_OF)
    assert resolution.resolved_surface == "HARD"
    assert resolution.source == SURFACE_SOURCE_TOURNAMENT_EDITION
    assert resolution.conflicts == ()


def test_match_version_surface_overrides_conflicting_edition(db: psycopg.Connection):
    match_id = _seed_match(db, edition_surface="HARD", version_surface="CLAY")
    resolution = resolve_authoritative_surface(db, match_id=match_id, as_of=AS_OF)
    assert resolution.resolved_surface == "CLAY"
    assert resolution.source == SURFACE_SOURCE_MATCH_VERSION
    assert len(resolution.conflicts) == 1
    conflict = resolution.conflicts[0]
    assert conflict.stronger_source == SURFACE_SOURCE_MATCH_VERSION
    assert conflict.weaker_source == SURFACE_SOURCE_TOURNAMENT_EDITION
    assert conflict.stronger_surface == "CLAY"
    assert conflict.weaker_surface == "HARD"


def test_true_unknown_remains_fail_closed(db: psycopg.Connection):
    match_id = _seed_match(db, edition_surface=None, version_surface=None)
    resolution = resolve_authoritative_surface(db, match_id=match_id, as_of=AS_OF)
    assert resolution.resolved_surface is None
    assert resolution.source == SURFACE_SOURCE_UNKNOWN
    assert resolution.is_known is False


def test_future_edition_metadata_cannot_leak_into_the_past(db: psycopg.Connection):
    match_id = _seed_match(db, edition_surface="GRASS", version_surface=None)
    db.execute(
        """
        UPDATE tournament_editions
        SET updated_at = %s
        WHERE tournament_edition_id = (
            SELECT tournament_edition_id FROM matches WHERE match_id = %s
        )
        """,
        (FUTURE, match_id),
    )
    historical = resolve_authoritative_surface(db, match_id=match_id, as_of=AS_OF)
    assert historical.resolved_surface is None
    assert historical.source == SURFACE_SOURCE_UNKNOWN

    later = resolve_authoritative_surface(db, match_id=match_id, as_of=FUTURE)
    assert later.resolved_surface == "GRASS"
    assert later.source == SURFACE_SOURCE_TOURNAMENT_EDITION


def test_surface_authority_report_counts_sources(db: psycopg.Connection):
    known = _seed_match(
        db, edition_surface="HARD", version_surface=None, source_match_id="td-a"
    )
    overridden = _seed_match(
        db, edition_surface="HARD", version_surface="CLAY", source_match_id="td-b"
    )
    unknown = _seed_match(
        db, edition_surface=None, version_surface=None, source_match_id="td-c"
    )
    report = build_surface_authority_report(
        db, match_ids=[known, overridden, unknown], as_of=AS_OF
    )
    assert report["matches"] == 3
    assert report["known"] == 2
    assert report["unknown"] == 1
    assert report["source_counts"][SURFACE_SOURCE_TOURNAMENT_EDITION] == 1
    assert report["source_counts"][SURFACE_SOURCE_MATCH_VERSION] == 1
    assert report["source_counts"][SURFACE_SOURCE_UNKNOWN] == 1
    assert report["conflicts"] == 1
