"""Surface PIT regression tests: mutable edition metadata is never historical proof."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from sports_edge_tennis.replay.surface_pit import (
    SURFACE_CONFLICT_AT_T,
    SURFACE_KNOWN_AT_T,
    SURFACE_NOT_AVAILABLE_PIT,
    resolve_surface_pit,
)

UTC = timezone.utc
CUTOFF = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
BEFORE = CUTOFF - timedelta(days=30)
AFTER = CUTOFF + timedelta(days=10)


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _player(conn, name):
    return _fetchval(
        conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,)
    )


def _tournament(conn, name, *, tour_scope="ATP_MAIN", tier="ATP_250"):
    return _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES (%s, %s, %s) RETURNING tournament_id",
        (name, tour_scope, tier),
    )


def _edition(conn, tournament_id, season, surface):
    return _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name, surface)
        VALUES (%s, %s, %s, %s) RETURNING tournament_edition_id
        """,
        (tournament_id, season, f"{season} edition", surface),
    )


def _match(conn, edition_id, p1, p2, *, surface, observed_at, status="SCHEDULED"):
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    _fetchval(
        conn,
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, 'ATP_MAIN', 'ATP_250', %s, 'R32', 3, FALSE, %s, %s, %s)
        RETURNING match_version_id
        """,
        (match_id, p1, p2, surface, status, observed_at, observed_at),
    )
    return match_id


def _resolve(conn, match_id, season_year=2026, name="Surface Open"):
    return resolve_surface_pit(
        conn,
        match_id=match_id,
        as_of=CUTOFF,
        canonical_name=name,
        tour_scope="ATP_MAIN",
        season_year=season_year,
    )


def test_surface_known_from_match_version_before_cutoff(db: psycopg.Connection):
    tid = _tournament(db, "Surface Open")
    edition = _edition(db, tid, 2026, None)
    match_id = _match(db, edition, _player(db, "A"), _player(db, "B"), surface="HARD", observed_at=BEFORE)

    resolution = _resolve(db, match_id)
    assert resolution.status == SURFACE_KNOWN_AT_T
    assert resolution.surface == "HARD"
    assert resolution.authority == "MATCH_VERSION_PIT"


def test_surface_added_after_cutoff_is_blocked(db: psycopg.Connection):
    tid = _tournament(db, "Surface Open")
    edition = _edition(db, tid, 2026, None)
    match_id = _match(db, edition, _player(db, "A"), _player(db, "B"), surface="HARD", observed_at=AFTER)

    resolution = _resolve(db, match_id)
    assert resolution.status == SURFACE_NOT_AVAILABLE_PIT
    assert resolution.surface is None


def test_current_edition_surface_alone_is_not_enough(db: psycopg.Connection):
    tid = _tournament(db, "Surface Open")
    edition = _edition(db, tid, 2026, "HARD")  # mutable master data
    match_id = _match(db, edition, _player(db, "A"), _player(db, "B"), surface=None, observed_at=BEFORE)

    resolution = _resolve(db, match_id)
    assert resolution.status == SURFACE_NOT_AVAILABLE_PIT
    assert resolution.surface is None


def test_match_version_evidence_overrides_edition_metadata(db: psycopg.Connection):
    tid = _tournament(db, "Surface Open")
    edition = _edition(db, tid, 2026, "HARD")  # current mutable metadata
    match_id = _match(db, edition, _player(db, "A"), _player(db, "B"), surface="CLAY", observed_at=BEFORE)

    resolution = _resolve(db, match_id)
    assert resolution.status == SURFACE_KNOWN_AT_T
    assert resolution.surface == "CLAY"
    assert resolution.authority == "MATCH_VERSION_PIT"


def test_conflicting_historical_surface_fails_closed(db: psycopg.Connection):
    tid = _tournament(db, "Surface Open")
    edition_2026 = _edition(db, tid, 2026, None)
    match_id = _match(
        db, edition_2026, _player(db, "A"), _player(db, "B"), surface=None, observed_at=BEFORE
    )
    # Prior season has two PIT-visible surfaces for the same tournament => conflict.
    edition_2025 = _edition(db, tid, 2025, None)
    _match(db, edition_2025, _player(db, "C"), _player(db, "D"), surface="HARD", observed_at=BEFORE)
    _match(db, edition_2025, _player(db, "E"), _player(db, "F"), surface="CLAY", observed_at=BEFORE)

    resolution = _resolve(db, match_id)
    assert resolution.status == SURFACE_CONFLICT_AT_T
    assert resolution.surface is None


def test_no_future_surface_leakage(db: psycopg.Connection):
    tid = _tournament(db, "Surface Open")
    edition = _edition(db, tid, 2026, None)
    # No PIT-visible version at the cutoff; a later version has a surface.
    match_id = _match(db, edition, _player(db, "A"), _player(db, "B"), surface="HARD", observed_at=AFTER)

    resolution = _resolve(db, match_id)
    assert resolution.status == SURFACE_NOT_AVAILABLE_PIT
