"""Replay history cache must be PIT-equivalent to per-cutoff baseline loading."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as A1_CONFIG,
    replay_history as replay_agent1_history,
)
from sports_edge_tennis.agents.agent3_intransitivity import DEFAULT_CONFIG as A3_CONFIG
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.agent2_history import (
    build_history_snapshot as build_agent2_snapshot,
    load_agent2_history,
)
from sports_edge_tennis.data.agent3_history import load_agent3_history
from sports_edge_tennis.replay.history_cache import (
    MODE_BASELINE_PER_CUTOFF,
    MODE_CACHE_LOAD_ONCE,
    ReplayHistoryCache,
)
from sports_edge_tennis.runner.agent3_live_snapshot import (
    build_live_agent3_history_snapshot,
)

UTC = timezone.utc
T1 = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)
R1 = T1 - timedelta(days=2)
R2 = T1 + timedelta(days=1)  # visible only at T2


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _player(conn, name):
    return _fetchval(
        conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,)
    )


def _completed_match(conn, edition_id, p1, p2, *, observed, winner=1):
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'COMPLETED', %s, %s)
        """,
        (match_id, p1, p2, observed, observed),
    )
    conn.execute(
        """
        INSERT INTO match_result_observations (match_id, outcome_status, winner_side, source_observed_at, as_of)
        VALUES (%s, 'COMPLETED', %s, %s, %s)
        """,
        (match_id, winner, observed, observed),
    )
    for player_id in (p1, p2):
        conn.execute(
            """
            INSERT INTO match_stat_observations (
                match_id, player_id, source_system, source_record_id, observation_kind,
                first_serves_in, first_serve_points_won, second_serve_points_won,
                service_points_played, return_points_won, aces, double_faults,
                break_points_saved, break_points_faced, source_observed_at, as_of
            )
            VALUES (%s, %s, 'API_TENNIS', %s, 'RAW_SOURCE', 10, 7, 3, 20, 8, 2, 1, 3, 5, %s, %s)
            """,
            (match_id, player_id, f"stat-{match_id}-{player_id}", observed, observed),
        )
    return match_id


def _seed(conn):
    p1, p2, p3, p4 = (_player(conn, name) for name in ("A One", "B Two", "C Three", "D Four"))
    tournament_id = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES ('Cache Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
    )
    edition = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) VALUES (%s, 2026, 'Cache 2026') RETURNING tournament_edition_id",
        (tournament_id,),
    )
    _completed_match(conn, edition, p1, p2, observed=R1)
    _completed_match(conn, edition, p3, p4, observed=R2)


def _a2_projection(snapshot):
    return {
        player_id: (
            state.prior_usable_matches,
            state.overall_serve_points_played,
            state.overall_return_points_played,
        )
        for player_id, state in snapshot.players.items()
    }


def _a3_projection(snapshot):
    return (
        sorted(snapshot.players),
        sorted(snapshot.pairs),
        sorted((key, tuple(sorted(value))) for key, value in snapshot.adjacency.items()),
    )


def test_cache_mode_is_load_once_without_straddle(db: psycopg.Connection):
    _seed(db)
    cache = ReplayHistoryCache(db, cutoffs=[T1, T2], a3_lookback_days=A3_CONFIG.lookback_days)
    assert cache.mode == MODE_CACHE_LOAD_ONCE


def test_cache_output_equals_baseline_per_cutoff(db: psycopg.Connection):
    _seed(db)
    cache = ReplayHistoryCache(db, cutoffs=[T1, T2], a3_lookback_days=A3_CONFIG.lookback_days)

    for cutoff in (T1, T2):
        cached = cache.history_for(cutoff)

        baseline_a1 = load_agent1_history(db, cutoff=cutoff)
        baseline_replay1 = replay_agent1_history(baseline_a1, cutoff, A1_CONFIG)
        assert cached.agent1_replay.player_states == baseline_replay1.player_states

        baseline_a2 = load_agent2_history(db, cutoff=cutoff)
        baseline_snap2 = build_agent2_snapshot(baseline_a2, cutoff=cutoff)
        assert _a2_projection(cached.agent2_snapshot) == _a2_projection(baseline_snap2)

        a3_days = (A3_CONFIG.lookback_days * 2) + 2
        baseline_a3 = load_agent3_history(
            db, cutoff=cutoff, date_start=(cutoff - timedelta(days=a3_days)).date()
        )
        baseline_snap3 = build_live_agent3_history_snapshot(baseline_a3, cutoff=cutoff)
        assert _a3_projection(cached.agent3_snapshot) == _a3_projection(baseline_snap3)


def test_cache_falls_back_to_baseline_when_observation_straddles_cutoff(db: psycopg.Connection):
    _seed(db)
    # Add a later re-observation of match 1's result between T1 and T2.
    match_one = _fetchval(db, "SELECT min(match_id) FROM matches")
    db.execute(
        """
        INSERT INTO match_result_observations (match_id, outcome_status, winner_side, source_observed_at, as_of)
        VALUES (%s, 'COMPLETED', 1, %s, %s)
        """,
        (match_one, R2, R2),
    )

    cache = ReplayHistoryCache(db, cutoffs=[T1, T2], a3_lookback_days=A3_CONFIG.lookback_days)
    assert cache.mode == MODE_BASELINE_PER_CUTOFF

    # Fallback path must still equal baseline at every cutoff.
    for cutoff in (T1, T2):
        cached = cache.history_for(cutoff)
        baseline_a2 = load_agent2_history(db, cutoff=cutoff)
        baseline_snap2 = build_agent2_snapshot(baseline_a2, cutoff=cutoff)
        assert _a2_projection(cached.agent2_snapshot) == _a2_projection(baseline_snap2)
