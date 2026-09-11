from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from sports_edge_tennis.dashboard.upcoming import get_upcoming_matches


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_match(conn, *, p1_name, p2_name, tournament, start, status="SCHEDULED"):
    p1 = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (p1_name,))
    p2 = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (p2_name,))
    tid = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES (%s, 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
        (tournament,),
    )
    eid = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, start_date) VALUES (%s, 2026, %s, '2026-09-07') RETURNING tournament_edition_id",
        (tid, tournament),
    )
    mid = _fetchval(conn, "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (eid,))
    observed = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'R32', 3, FALSE, %s, %s, %s)
        """,
        (mid, p1, p2, start, status, observed, observed),
    )
    return mid


def test_upcoming_returns_only_future_scheduled_matches(db: psycopg.Connection):
    now = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)
    future = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    later = datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc)
    past = datetime(2026, 9, 7, 11, 0, tzinfo=timezone.utc)

    _seed_match(db, p1_name="Future One", p2_name="Future Two", tournament="Test ATP", start=future)
    _seed_match(db, p1_name="Later One", p2_name="Later Two", tournament="Test ATP 2", start=later)
    _seed_match(db, p1_name="Past One", p2_name="Past Two", tournament="Past ATP", start=past)
    _seed_match(
        db,
        p1_name="Done One",
        p2_name="Done Two",
        tournament="Done ATP",
        start=future,
        status="COMPLETED",
    )

    rows = get_upcoming_matches(db, now=now, days=3, limit=24)

    assert [(row.player_1, row.player_2) for row in rows] == [
        ("Future One", "Future Two"),
        ("Later One", "Later Two"),
    ]
    assert rows[0].scheduled_start == future
    assert rows[0].manager_status is None
