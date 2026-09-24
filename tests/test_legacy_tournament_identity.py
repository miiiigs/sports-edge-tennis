from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.live.legacy_tournament_identity import (
    bridge_legacy_tournament_identities,
    derive_edition_evidence,
)

T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def _player(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO players (canonical_name, status) VALUES (%s, 'ACTIVE') RETURNING player_id",
        (name,),
    ).fetchone()[0]


def _tournament(conn: psycopg.Connection, *, name: str, sr_competition: str | None = None) -> int:
    tournament_id = conn.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES (%s, 'CHALLENGER', 'CHALLENGER') RETURNING tournament_id",
        (name,),
    ).fetchone()[0]
    if sr_competition is not None:
        conn.execute(
            "INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id) "
            "VALUES (%s, 'SPORTRADAR', %s)",
            (tournament_id, sr_competition),
        )
    return tournament_id


def _edition(conn: psycopg.Connection, *, tournament_id: int, name: str, season: int) -> int:
    return conn.execute(
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, surface, start_date, end_date) "
        "VALUES (%s, %s, %s, NULL, '2026-01-01', '2026-01-14') RETURNING tournament_edition_id",
        (tournament_id, season, name),
    ).fetchone()[0]


def _match(
    conn: psycopg.Connection,
    *,
    edition_id: int,
    p1: int,
    p2: int,
    start_at: datetime,
    surface: str | None = None,
    sr_event: str | None = None,
) -> int:
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
        (match_id, p1, p2, start_at, surface, start_at, start_at),
    )
    if sr_event is not None:
        conn.execute(
            "INSERT INTO match_source_ids (match_id, source_system, source_match_id) VALUES (%s, 'SPORTRADAR', %s)",
            (match_id, sr_event),
        )
    return match_id


def _legacy_with_sr_pair(conn: psycopg.Connection):
    p1 = _player(conn, "Legacy One")
    p2 = _player(conn, "Legacy Two")
    legacy_tid = _tournament(conn, name="Legacy Open")
    legacy_edition = _edition(conn, tournament_id=legacy_tid, name="Legacy Open 2026", season=2026)
    _match(conn, edition_id=legacy_edition, p1=p1, p2=p2, start_at=T0)
    return p1, p2, legacy_tid, legacy_edition


def _add_sr_match(conn, *, competition_id, p1, p2, offset_minutes=5, year=2026):
    sr_tid = _tournament(conn, name=f"SR {competition_id}", sr_competition=competition_id)
    sr_edition = _edition(conn, tournament_id=sr_tid, name=f"SR {competition_id} {year}", season=year)
    start = T0.replace(year=year)
    from datetime import timedelta

    _match(
        conn,
        edition_id=sr_edition,
        p1=p1,
        p2=p2,
        start_at=start + timedelta(minutes=offset_minutes),
        sr_event=f"sr:sport_event:{competition_id}:{offset_minutes}:{year}",
    )
    return sr_tid, sr_edition


def test_event_derived_competition_identity_succeeds(db: psycopg.Connection) -> None:
    p1, p2, legacy_tid, legacy_edition = _legacy_with_sr_pair(db)
    _add_sr_match(db, competition_id="sr:competition:1", p1=p1, p2=p2)

    evidence = derive_edition_evidence(db, player_ids=[p1, p2])
    assert evidence.get(legacy_edition) == {"sr:competition:1"}

    outcome = bridge_legacy_tournament_identities(db, player_ids=[p1, p2])
    assert outcome.editions_deterministic == 1
    assert outcome.tournaments_already_linked == 1
    assert outcome.tournaments_bridged == 0
    # The competition identity is owned by the corresponding Sportradar duplicate
    # tournament and cannot be independently re-attached (append-only unique).
    assert db.execute(
        "SELECT count(*) FROM tournament_source_ids WHERE tournament_id = %s AND source_system='SPORTRADAR'",
        (legacy_tid,),
    ).fetchone()[0] == 0


def test_multiple_events_agreeing_succeeds(db: psycopg.Connection) -> None:
    p1, p2, legacy_tid, legacy_edition = _legacy_with_sr_pair(db)
    _sr_tid, sr_edition = _add_sr_match(db, competition_id="sr:competition:2", p1=p1, p2=p2, offset_minutes=3)
    # A second independent event in the same Sportradar competition, near the same time.
    _match(
        db,
        edition_id=sr_edition,
        p1=p1,
        p2=p2,
        start_at=T0 + __import__("datetime").timedelta(minutes=7),
        sr_event="sr:sport_event:agreement-extra",
    )

    evidence = derive_edition_evidence(db, player_ids=[p1, p2])
    assert evidence.get(legacy_edition) == {"sr:competition:2"}
    outcome = bridge_legacy_tournament_identities(db, player_ids=[p1, p2])
    assert outcome.editions_deterministic == 1
    assert outcome.tournaments_already_linked == 1


def test_conflicting_competitions_fail_closed(db: psycopg.Connection) -> None:
    p1, p2, legacy_tid, _legacy_edition = _legacy_with_sr_pair(db)
    _add_sr_match(db, competition_id="sr:competition:10", p1=p1, p2=p2, offset_minutes=3)
    _add_sr_match(db, competition_id="sr:competition:11", p1=p1, p2=p2, offset_minutes=6)

    outcome = bridge_legacy_tournament_identities(db, player_ids=[p1, p2])
    assert outcome.tournaments_bridged == 0
    assert outcome.editions_ambiguous >= 1
    assert db.execute(
        "SELECT count(*) FROM tournament_source_ids WHERE tournament_id = %s AND source_system='SPORTRADAR'",
        (legacy_tid,),
    ).fetchone()[0] == 0


def test_wrong_season_year_rejected(db: psycopg.Connection) -> None:
    p1, p2, legacy_tid, _legacy_edition = _legacy_with_sr_pair(db)
    _add_sr_match(db, competition_id="sr:competition:20", p1=p1, p2=p2, year=2025)

    outcome = bridge_legacy_tournament_identities(db, player_ids=[p1, p2])
    assert outcome.tournaments_bridged == 0
    assert db.execute(
        "SELECT count(*) FROM tournament_source_ids WHERE tournament_id = %s AND source_system='SPORTRADAR'",
        (legacy_tid,),
    ).fetchone()[0] == 0


def test_duplicate_replay_idempotent(db: psycopg.Connection) -> None:
    p1, p2, legacy_tid, _legacy_edition = _legacy_with_sr_pair(db)
    _add_sr_match(db, competition_id="sr:competition:30", p1=p1, p2=p2)

    first = bridge_legacy_tournament_identities(db, player_ids=[p1, p2])
    second = bridge_legacy_tournament_identities(db, player_ids=[p1, p2])

    assert first.tournaments_bridged == 0
    assert second.tournaments_bridged == 0
    assert first.tournaments_already_linked == 1
    assert second.tournaments_already_linked == 1
    assert db.execute(
        "SELECT count(*) FROM tournament_source_ids WHERE tournament_id = %s AND source_system='SPORTRADAR'",
        (legacy_tid,),
    ).fetchone()[0] == 0


def test_name_only_link_rejected(db: psycopg.Connection) -> None:
    p1 = _player(db, "Name One")
    p2 = _player(db, "Name Two")
    legacy_tid = _tournament(db, name="Shared Name Open")
    legacy_edition = _edition(db, tournament_id=legacy_tid, name="Shared Name Open 2026", season=2026)
    _match(db, edition_id=legacy_edition, p1=p1, p2=p2, start_at=T0)
    # A Sportradar tournament with the same name but no matching event.
    _tournament(db, name="Shared Name Open", sr_competition="sr:competition:99")

    outcome = bridge_legacy_tournament_identities(db, player_ids=[p1, p2])
    assert outcome.tournaments_bridged == 0
    assert db.execute(
        "SELECT count(*) FROM tournament_source_ids WHERE tournament_id = %s AND source_system='SPORTRADAR'",
        (legacy_tid,),
    ).fetchone()[0] == 0


def test_tournament_level_competition_disagreement_fails_closed(db: psycopg.Connection) -> None:
    p1 = _player(db, "Multi One")
    p2 = _player(db, "Multi Two")
    legacy_tid = _tournament(db, name="Multi Edition Open")
    edition_a = _edition(db, tournament_id=legacy_tid, name="Multi Edition Open 2026 A", season=2026)
    _match(db, edition_id=edition_a, p1=p1, p2=p2, start_at=T0)
    _add_sr_match(db, competition_id="sr:competition:40", p1=p1, p2=p2, offset_minutes=5)
    edition_b = _edition(db, tournament_id=legacy_tid, name="Multi Edition Open 2026 B", season=2026)
    _match(
        db,
        edition_id=edition_b,
        p1=p1,
        p2=p2,
        start_at=T0 + __import__("datetime").timedelta(hours=2),
    )
    _add_sr_match(db, competition_id="sr:competition:41", p1=p1, p2=p2, offset_minutes=125)

    outcome = bridge_legacy_tournament_identities(db, player_ids=[p1, p2])

    assert outcome.tournaments_bridged == 0
    assert db.execute(
        "SELECT count(*) FROM tournament_source_ids WHERE tournament_id = %s AND source_system='SPORTRADAR'",
        (legacy_tid,),
    ).fetchone()[0] == 0
