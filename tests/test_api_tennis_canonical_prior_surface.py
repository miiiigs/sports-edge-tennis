"""Cross-source deterministic canonical prior-surface recovery (PIT-safe)."""

from __future__ import annotations

import psycopg

from sports_edge_tennis.live.api_tennis_fixture_compat import (
    _stored_canonical_prior_surface,
)


def _tournament(conn, name, scope, tier) -> int:
    return conn.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES (%s, %s, %s) RETURNING tournament_id",
        (name, scope, tier),
    ).fetchone()[0]


def _edition(conn, tournament_id, season, surface) -> None:
    conn.execute(
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, surface) "
        "VALUES (%s, %s, %s, %s::tennis_surface)",
        (tournament_id, season, f"edition-{tournament_id}-{season}", surface),
    )


def _map(conn, tournament_id, source_tournament_id) -> None:
    conn.execute(
        "INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id) "
        "VALUES (%s, 'API_TENNIS', %s)",
        (tournament_id, source_tournament_id),
    )


def test_recovers_stable_prior_surface_from_canonical_sibling(db: psycopg.Connection):
    current = _tournament(db, "Mouilleron-Le-Captif", "CHALLENGER", "CHALLENGER")
    _map(db, current, "2420")
    sibling = _tournament(db, "Mouilleron-Le-Captif CH", "CHALLENGER", "CHALLENGER")
    _edition(db, sibling, 2024, "HARD")
    _edition(db, sibling, 2025, "HARD")

    assert (
        _stored_canonical_prior_surface(db, tournament_key="2420", season_year=2026)
        == "Hard"
    )


def test_conflicting_prior_surfaces_fail_closed(db: psycopg.Connection):
    current = _tournament(db, "Columbus", "CHALLENGER", "CHALLENGER")
    _map(db, current, "2344")
    sibling = _tournament(db, "Columbus CH", "CHALLENGER", "CHALLENGER")
    _edition(db, sibling, 2024, "HARD")
    _edition(db, sibling, 2025, "CLAY")

    assert (
        _stored_canonical_prior_surface(db, tournament_key="2344", season_year=2026)
        is None
    )


def test_no_prior_edition_returns_none(db: psycopg.Connection):
    current = _tournament(db, "Newport", "CHALLENGER", "CHALLENGER")
    _map(db, current, "1111")
    _edition(db, current, 2026, "HARD")  # only current season

    assert (
        _stored_canonical_prior_surface(db, tournament_key="1111", season_year=2026)
        is None
    )


def test_future_edition_is_excluded(db: psycopg.Connection):
    current = _tournament(db, "Futureville", "CHALLENGER", "CHALLENGER")
    _map(db, current, "2222")
    _edition(db, current, 2027, "CLAY")  # future relative to 2026

    assert (
        _stored_canonical_prior_surface(db, tournament_key="2222", season_year=2026)
        is None
    )


def test_different_scope_never_links(db: psycopg.Connection):
    current = _tournament(db, "Twin City", "CHALLENGER", "CHALLENGER")
    _map(db, current, "3333")
    main = _tournament(db, "Twin City CH", "ATP_MAIN", "ATP_TOUR")
    _edition(db, main, 2024, "HARD")

    assert (
        _stored_canonical_prior_surface(db, tournament_key="3333", season_year=2026)
        is None
    )


def test_unknown_provider_id_returns_none(db: psycopg.Connection):
    assert (
        _stored_canonical_prior_surface(db, tournament_key="nope", season_year=2026)
        is None
    )
