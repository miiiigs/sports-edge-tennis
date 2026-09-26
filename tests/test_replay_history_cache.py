"""Replay history cache must be PIT-equivalent to per-cutoff baseline loading."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

import sports_edge_tennis.replay.history_cache as history_cache
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



def test_cache_bootstraps_once_then_advances_a2_a3_incrementally(
    db: psycopg.Connection,
    monkeypatch,
):
    _seed(db)
    calls = {
        "a2_build": 0,
        "a2_advance": 0,
        "a3_build": 0,
        "a3_advance": 0,
    }

    original_a2_build = history_cache.build_agent2_snapshot
    original_a2_advance = history_cache.advance_agent2_history_snapshot
    original_a3_build = history_cache.build_live_agent3_history_snapshot
    original_a3_advance = history_cache.advance_live_agent3_history_snapshot

    def a2_build(*args, **kwargs):
        calls["a2_build"] += 1
        return original_a2_build(*args, **kwargs)

    def a2_advance(*args, **kwargs):
        calls["a2_advance"] += 1
        return original_a2_advance(*args, **kwargs)

    def a3_build(*args, **kwargs):
        calls["a3_build"] += 1
        return original_a3_build(*args, **kwargs)

    def a3_advance(*args, **kwargs):
        calls["a3_advance"] += 1
        return original_a3_advance(*args, **kwargs)

    monkeypatch.setattr(history_cache, "build_agent2_snapshot", a2_build)
    monkeypatch.setattr(history_cache, "advance_agent2_history_snapshot", a2_advance)
    monkeypatch.setattr(history_cache, "build_live_agent3_history_snapshot", a3_build)
    monkeypatch.setattr(history_cache, "advance_live_agent3_history_snapshot", a3_advance)

    cache = ReplayHistoryCache(
        db,
        cutoffs=[T1, T2],
        a3_lookback_days=A3_CONFIG.lookback_days,
    )

    # Requesting T2 first must still build T1 as the chronological bootstrap,
    # then advance exactly once to T2.
    result = cache.history_for(T2)
    assert result.cutoff == T2
    assert calls == {
        "a2_build": 1,
        "a2_advance": 1,
        "a3_build": 1,
        "a3_advance": 1,
    }

    # Re-reading either cutoff must be a pure cache hit.
    cache.history_for(T1)
    cache.history_for(T2)
    assert calls == {
        "a2_build": 1,
        "a2_advance": 1,
        "a3_build": 1,
        "a3_advance": 1,
    }



def test_cache_guard_checks_entire_correction_chain_not_only_latest_two(
    db: psycopg.Connection,
):
    _seed(db)
    match_one = _fetchval(db, "SELECT min(match_id) FROM matches")
    correction_1 = T1 + timedelta(hours=1)
    correction_2 = T1 + timedelta(hours=2)

    for observed in (correction_1, correction_2):
        db.execute(
            """
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, source_observed_at, as_of
            )
            VALUES (%s, 'COMPLETED', 1, %s, %s)
            """,
            (match_one, observed, observed),
        )

    cache = ReplayHistoryCache(
        db,
        cutoffs=[T1, T2],
        a3_lookback_days=A3_CONFIG.lookback_days,
    )

    # The latest two corrections are both after T1, so a guard that inspects
    # only those two would miss the older R1 -> correction_1 transition that
    # changes what was knowable at T1.
    assert cache.mode == MODE_BASELINE_PER_CUTOFF



def test_cache_falls_back_when_complete_stat_cohort_changes_across_cutoff(
    db: psycopg.Connection,
):
    _seed(db)
    match_one = _fetchval(db, "SELECT min(match_id) FROM matches")
    p1, p2 = db.execute(
        """
        SELECT player_1_id, player_2_id
        FROM match_versions
        WHERE match_id = %s
        ORDER BY match_version_id DESC
        LIMIT 1
        """,
        (match_one,),
    ).fetchone()
    corrected_at = T1 + timedelta(hours=1)

    for player_id in (p1, p2):
        db.execute(
            """
            INSERT INTO match_stat_observations (
                match_id, player_id, source_system, source_record_id, observation_kind,
                first_serves_in, first_serve_points_won, second_serve_points_won,
                service_points_played, return_points_won, aces, double_faults,
                break_points_saved, break_points_faced, source_observed_at, as_of
            )
            VALUES (
                %s, %s, 'API_TENNIS', %s, 'RAW_SOURCE',
                11, 8, 3, 21, 9, 3, 1, 4, 6, %s, %s
            )
            """,
            (
                match_one,
                player_id,
                f"stat-correction-{match_one}-{player_id}",
                corrected_at,
                corrected_at,
            ),
        )

    cache = ReplayHistoryCache(
        db,
        cutoffs=[T1, T2],
        a3_lookback_days=A3_CONFIG.lookback_days,
    )
    assert cache.mode == MODE_BASELINE_PER_CUTOFF
