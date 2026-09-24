from __future__ import annotations

from datetime import UTC, date, datetime

import psycopg

from sports_edge_tennis.live.current_slate_bootstrap import (
    PlayerReadiness,
    is_placeholder_name,
    load_concrete_targets,
    run_current_slate_bootstrap,
    select_bootstrap_dates,
)


def _utc(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


def _player(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO players (canonical_name, status) VALUES (%s, 'ACTIVE') RETURNING player_id",
        (name,),
    ).fetchone()[0]


def _match(
    conn: psycopg.Connection,
    *,
    name: str,
    season: int,
    p1: int,
    p2: int,
    start_at: datetime,
    status: str = "COMPLETED",
    with_result: bool = True,
    with_stats: bool = False,
) -> int:
    tournament_id = conn.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES (%s, 'CHALLENGER', 'CHALLENGER') RETURNING tournament_id",
        (name,),
    ).fetchone()[0]
    edition_id = conn.execute(
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, surface, start_date, end_date) "
        "VALUES (%s, %s, %s, 'CLAY', %s, %s) RETURNING tournament_edition_id",
        (tournament_id, season, f"{name} {season}", start_at.date(), start_at.date()),
    ).fetchone()[0]
    match_id = conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', 'CLAY', 'R32', 3, FALSE, %s, %s, %s)
        """,
        (match_id, p1, p2, start_at, status, start_at, start_at),
    )
    if with_result:
        conn.execute(
            """
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, source_observed_at, as_of
            ) VALUES (%s, 'COMPLETED', 1, %s, %s)
            """,
            (match_id, start_at, start_at),
        )
    if with_stats:
        for pid, rec in ((p1, "s1"), (p2, "s2")):
            conn.execute(
                """
                INSERT INTO match_stat_observations (
                    match_id, player_id, source_system, source_record_id, observation_kind,
                    service_points_played, first_serve_points_won, second_serve_points_won,
                    return_points_won, source_observed_at, as_of
                ) VALUES (%s, %s, 'SACKMANN', %s, 'RAW_SOURCE', 60, 30, 15, 20, %s, %s)
                """,
                (match_id, pid, rec, start_at, start_at),
            )
    return match_id


def _run_with_target(conn: psycopg.Connection, match_ids: list[int], as_of: datetime) -> int:
    run_id = conn.execute(
        """
        INSERT INTO prediction_runs (
            run_status, as_of, started_at, prediction_contract_version, created_at
        ) VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1', %s) RETURNING prediction_run_id
        """,
        (as_of, as_of, as_of),
    ).fetchone()[0]
    for match_id in match_ids:
        conn.execute(
            "INSERT INTO prediction_run_targets (prediction_run_id, match_id, created_at) VALUES (%s, %s, %s)",
            (run_id, match_id, as_of),
        )
    return run_id


def test_placeholder_detection() -> None:
    assert is_placeholder_name("R16P9")
    assert is_placeholder_name("Qf5")
    assert is_placeholder_name("WQF3")
    assert not is_placeholder_name("M. Petkovic")
    assert not is_placeholder_name("Dalla Valle, Enrico")


def test_load_concrete_targets_excludes_placeholders(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    p3 = _player(db, "R16P9")
    p4 = _player(db, "Gamma, Four")
    cutoff = _utc(2026, 9, 24)
    match_a = _match(db, name="Concrete Open", season=2026, p1=p1, p2=p2, start_at=_utc(2026, 9, 1, 10))
    match_b = _match(db, name="Placeholder Open", season=2026, p1=p3, p2=p4, start_at=_utc(2026, 9, 1, 11))
    run_id = _run_with_target(db, [match_a, match_b], cutoff)

    targets = load_concrete_targets(db, run_id=run_id, cutoff=cutoff)

    assert targets == [(match_a, p1, p2)]


def test_select_bootstrap_dates_ranks_by_players_helped(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    # Date D1: both players have a statless completed match.
    _match(db, name="D1 Open", season=2026, p1=p1, p2=p2, start_at=_utc(2026, 9, 10))
    # Date D2: only p1.
    _match(db, name="D2 Open", season=2026, p1=p1, p2=_player(db, "Other, Player"), start_at=_utc(2026, 9, 11))
    # Date D3: p1 has stats already -> not a candidate.
    _match(db, name="D3 Open", season=2026, p1=p1, p2=p2, start_at=_utc(2026, 9, 12), with_stats=True)

    cutoff = _utc(2026, 9, 24)
    ranked = select_bootstrap_dates(db, player_ids=[p1, p2], cutoff=cutoff, lookback_days=60, max_dates=10)

    dates = [d for d, _count in ranked]
    assert date(2026, 9, 10) in dates
    assert date(2026, 9, 12) not in dates
    # D1 (both players) must rank before D2 (one player).
    assert dates.index(date(2026, 9, 10)) < dates.index(date(2026, 9, 11))


def test_run_bootstrap_respects_cap_and_early_stop(db: psycopg.Connection) -> None:
    p1 = _player(db, "Alpha, One")
    p2 = _player(db, "Beta, Two")
    cutoff = _utc(2026, 9, 24)
    match_id = _match(db, name="Cap Open", season=2026, p1=p1, p2=p2, start_at=_utc(2026, 9, 10))
    _run_with_target(db, [match_id], cutoff)

    calls: list[date] = []

    def fake_fetch_day(conn, *, api_key, target_date):
        calls.append(target_date)

        class _Outcome:
            observations_inserted = 1
            stat_rows_inserted = 2

        return _Outcome()

    def fake_readiness(conn, *, player_ids, cutoff):
        # Not ready until the first fetch has supplied history.
        ready = bool(calls)
        return {
            int(pid): PlayerReadiness(
                int(pid),
                5 if ready else 0,
                300 if ready else 0,
                300 if ready else 0,
            )
            for pid in player_ids
        }

    outcome = run_current_slate_bootstrap(
        db,
        api_key="k",
        max_requests=5,
        run_id=None,
        cutoff=cutoff,
        fetch_day=fake_fetch_day,
        readiness_fn=fake_readiness,
    )

    assert outcome.requests_consumed == 1
    assert outcome.stopped_reason == "all_target_players_ready"
    assert calls == [date(2026, 9, 10)]
    assert outcome.ready_before == 0
    assert outcome.ready_after == 2
