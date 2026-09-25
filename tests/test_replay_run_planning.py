"""Run planning must place the blind cutoff before every same-day match start."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from sports_edge_tennis.replay.loader import derive_run_specs_from_match_days

UTC = timezone.utc


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _insert_match(conn, start_at):
    p1 = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES ('P One') RETURNING player_id")
    p2 = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES ('P Two') RETURNING player_id")
    tournament_id = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES ('Plan Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
    )
    edition = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) VALUES (%s, 2026, 'Plan 2026') RETURNING tournament_edition_id",
        (tournament_id,),
    )
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition,),
    )
    observed = start_at - timedelta(days=1)
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'SCHEDULED', %s, %s)
        """,
        (match_id, p1, p2, start_at, observed, observed),
    )
    return match_id


def test_cutoff_precedes_every_same_day_match_start(db: psycopg.Connection):
    # One match starts at 02:00 UTC (before a naive noon-minus-6h cutoff).
    early = _insert_match(db, datetime(2026, 9, 12, 2, 0, tzinfo=UTC))
    later = _insert_match(db, datetime(2026, 9, 12, 18, 0, tzinfo=UTC))

    specs = derive_run_specs_from_match_days(db, match_ids=(early, later))
    assert len(specs) == 1
    spec = specs[0]
    assert set(spec.match_ids) == {early, later}
    assert spec.prediction_as_of <= datetime(2026, 9, 12, 2, 0, tzinfo=UTC)
    assert spec.market_as_of <= spec.prediction_as_of + timedelta(hours=5)
