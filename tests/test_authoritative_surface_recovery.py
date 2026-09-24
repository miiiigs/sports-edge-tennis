from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.live.authoritative_surface_recovery import (
    load_unresolved_editions,
    recover_authoritative_surfaces,
)

RECEIVED = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def _player(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO players (canonical_name, status) VALUES (%s, 'ACTIVE') RETURNING player_id",
        (name,),
    ).fetchone()[0]


def _edition(
    conn: psycopg.Connection,
    *,
    name: str,
    season: int,
    competition_id: str,
    p1: int,
    p2: int,
    version_surface: str | None = None,
    tournament_id: int | None = None,
) -> tuple[int, int, int]:
    if tournament_id is None:
        tournament_id = conn.execute(
            "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
            "VALUES (%s, 'CHALLENGER', 'CHALLENGER') RETURNING tournament_id",
            (name,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id) "
            "VALUES (%s, 'SPORTRADAR', %s)",
            (tournament_id, competition_id),
        )
    edition_id = conn.execute(
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, surface, start_date, end_date) "
        "VALUES (%s, %s, %s, NULL, '2026-01-01', '2026-01-07') RETURNING tournament_edition_id",
        (tournament_id, season, f"{name} {season}"),
    ).fetchone()[0]
    match_id = conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status, source_observed_at, as_of
        ) VALUES (%s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', %s, 'R32', 3, FALSE, 'COMPLETED', %s, %s)
        """,
        (match_id, p1, p2, RECEIVED, version_surface, RECEIVED, RECEIVED),
    )
    return tournament_id, edition_id, match_id


def _providers(surface: str):
    calls = {"seasons": 0, "info": 0}

    def fetch_seasons(api_key, competition_id, **kwargs):
        calls["seasons"] += 1
        return RECEIVED, {"seasons": [{"id": "sr:season:1", "year": "2026"}]}

    def fetch_info(api_key, season_id, **kwargs):
        calls["info"] += 1
        return RECEIVED, {"season": {"info": {"surface": surface}}}

    return fetch_seasons, fetch_info, calls


def test_recover_fills_edition_surface_from_authoritative_metadata(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    _tid, edition_id, _match_id = _edition(
        db, name="Recovery Open", season=2026, competition_id="sr:competition:1", p1=p1, p2=p2
    )
    fetch_seasons, fetch_info, calls = _providers("Clay")

    outcome = recover_authoritative_surfaces(
        db, api_key="k", fetch_seasons=fetch_seasons, fetch_info=fetch_info
    )

    surface = db.execute(
        "SELECT surface::text FROM tournament_editions WHERE tournament_edition_id = %s", (edition_id,)
    ).fetchone()[0]
    audit = db.execute(
        "SELECT count(*) FROM data_health_checks WHERE dataset_name = 'AUTHORITATIVE_SURFACE_RECOVERY'"
    ).fetchone()[0]
    assert surface == "CLAY"
    assert calls == {"seasons": 1, "info": 1}
    assert outcome.provider_requests == 2
    assert outcome.editions_recovered == 1
    assert audit == 1


def test_recover_dedupes_requests_across_same_competition_and_season(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    tournament_id, edition_a, _m1 = _edition(
        db, name="Dedup Open", season=2026, competition_id="sr:competition:9", p1=p1, p2=p2
    )
    _t2, edition_b, _m2 = _edition(
        db,
        name="Dedup Open B",
        season=2026,
        competition_id="sr:competition:9",
        p1=p1,
        p2=p2,
        tournament_id=tournament_id,
    )
    fetch_seasons, fetch_info, calls = _providers("Hard")

    outcome = recover_authoritative_surfaces(
        db, api_key="k", fetch_seasons=fetch_seasons, fetch_info=fetch_info
    )

    surfaces = [
        r[0]
        for r in db.execute(
            "SELECT surface::text FROM tournament_editions WHERE tournament_edition_id IN (%s, %s) ORDER BY tournament_edition_id",
            (edition_a, edition_b),
        ).fetchall()
    ]
    assert surfaces == ["HARD", "HARD"]
    assert calls == {"seasons": 1, "info": 1}
    assert outcome.editions_recovered == 2


def test_recover_fails_closed_when_provider_surface_is_absent(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    _tid, edition_id, _match_id = _edition(
        db, name="Unknown Open", season=2026, competition_id="sr:competition:2", p1=p1, p2=p2
    )
    fetch_seasons, fetch_info, _calls = _providers("")

    outcome = recover_authoritative_surfaces(
        db, api_key="k", fetch_seasons=fetch_seasons, fetch_info=fetch_info
    )

    surface = db.execute(
        "SELECT surface FROM tournament_editions WHERE tournament_edition_id = %s", (edition_id,)
    ).fetchone()[0]
    assert surface is None
    assert outcome.editions_recovered == 0
    assert outcome.editions_unresolved == 1


def test_recover_refuses_conflicting_existing_version_surface(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    _tid, edition_id, _match_id = _edition(
        db,
        name="Conflict Open",
        season=2026,
        competition_id="sr:competition:3",
        p1=p1,
        p2=p2,
        version_surface="HARD",
    )
    fetch_seasons, fetch_info, _calls = _providers("Clay")

    outcome = recover_authoritative_surfaces(
        db, api_key="k", fetch_seasons=fetch_seasons, fetch_info=fetch_info
    )

    surface = db.execute(
        "SELECT surface FROM tournament_editions WHERE tournament_edition_id = %s", (edition_id,)
    ).fetchone()[0]
    assert surface is None
    assert outcome.editions_recovered == 0


def test_load_unresolved_editions_filters_by_edition_ids(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    _t1, edition_a, _m1 = _edition(
        db, name="Target Open", season=2026, competition_id="sr:competition:7", p1=p1, p2=p2
    )
    _t2, edition_b, _m2 = _edition(
        db, name="Other Open", season=2026, competition_id="sr:competition:8", p1=p1, p2=p2
    )

    editions = load_unresolved_editions(db, edition_ids=[edition_a])
    ids = {e.tournament_edition_id for e in editions}

    assert ids == {edition_a}
    assert edition_b not in ids


def test_recover_by_edition_ids_only_targets_listed(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    _t1, edition_a, _m1 = _edition(
        db, name="Target Open", season=2026, competition_id="sr:competition:7", p1=p1, p2=p2
    )
    _t2, edition_b, _m2 = _edition(
        db, name="Other Open", season=2026, competition_id="sr:competition:8", p1=p1, p2=p2
    )
    fetch_seasons, fetch_info, calls = _providers("Grass")

    outcome = recover_authoritative_surfaces(
        db, api_key="k", edition_ids=[edition_a], fetch_seasons=fetch_seasons, fetch_info=fetch_info
    )

    surfaces = {
        r[0]
        for r in db.execute(
            "SELECT surface::text FROM tournament_editions WHERE tournament_edition_id IN (%s, %s)",
            (edition_a, edition_b),
        ).fetchall()
    }
    assert surfaces == {"GRASS", None}
    assert outcome.editions_recovered == 1
    assert calls == {"seasons": 1, "info": 1}


def test_load_unresolved_editions_requires_null_version_surface(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    _t1, null_edition, _m1 = _edition(
        db, name="Null Open", season=2026, competition_id="sr:competition:4", p1=p1, p2=p2
    )
    _t2, known_edition, _m2 = _edition(
        db,
        name="Known Open",
        season=2026,
        competition_id="sr:competition:5",
        p1=p1,
        p2=p2,
        version_surface="CLAY",
    )

    editions = load_unresolved_editions(db)
    ids = {e.tournament_edition_id for e in editions}

    assert null_edition in ids
    assert known_edition not in ids
