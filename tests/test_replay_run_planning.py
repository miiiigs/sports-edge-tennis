"""Run planning must place the blind cutoff before every same-day match start."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from sports_edge_tennis.replay.loader import (
    ReplayLoadRequest,
    ReplayRunSpec,
    derive_run_specs_from_match_days,
    derive_run_specs_pit_safe,
    load_replay_inputs,
)

UTC = timezone.utc


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _insert_match(conn, start_at, *, observed=None):
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
    observed = observed or (start_at - timedelta(days=1))
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


def test_unresolvable_match_is_excluded_not_fatal(db: psycopg.Connection):
    from sports_edge_tennis.manager.persistence import ensure_manager_policy_version_v1
    from sports_edge_tennis.market.persistence import ensure_market_policy_version_v1

    ensure_manager_policy_version_v1(db)
    ensure_market_policy_version_v1(db)

    # A match whose only version is observed after the cutoff has no PIT-visible
    # scheduled version; the loader must record an exclusion and continue.
    match_id = _insert_match(
        db,
        datetime(2026, 9, 12, 18, 0, tzinfo=UTC),
        observed=datetime(2026, 9, 12, 19, 0, tzinfo=UTC),
    )
    request = ReplayLoadRequest(
        run_specs=(
            ReplayRunSpec(
                run_id="plan-test",
                run_sequence=1,
                prediction_as_of=datetime(2026, 9, 12, 12, 0, tzinfo=UTC),
                market_as_of=datetime(2026, 9, 12, 17, 0, tzinfo=UTC),
                match_ids=(match_id,),
            ),
        ),
        result_as_of=datetime(2026, 9, 20, tzinfo=UTC),
    )
    loaded = load_replay_inputs(db, request)
    assert loaded.runs[0].matches == ()
    reasons = {exclusion.reason for exclusion in loaded.runs[0].loader_exclusions}
    assert "NO_SCHEDULED_PIT_VERSION" in reasons


def test_pit_safe_planner_recovers_later_same_day_fixture(db: psycopg.Connection):
    # Early match visible well before the day's earliest start.
    early = _insert_match(
        db, datetime(2026, 9, 12, 2, 0, tzinfo=UTC),
        observed=datetime(2026, 9, 11, 20, 0, tzinfo=UTC),
    )
    # Later same-day match whose fixture only became visible mid-morning.
    late = _insert_match(
        db, datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
        observed=datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
    )

    # Legacy daily planner: one cutoff before 02:00, so the late fixture is invisible.
    legacy = derive_run_specs_from_match_days(db, match_ids=(early, late))
    assert len(legacy) == 1
    legacy_cutoff = legacy[0].prediction_as_of
    assert legacy_cutoff < datetime(2026, 9, 12, 10, 0, tzinfo=UTC)

    # PIT-safe planner: each match gets a cutoff at/after its visibility, before start.
    safe = derive_run_specs_pit_safe(db, match_ids=(early, late))
    assigned = {mid: spec for spec in safe for mid in spec.match_ids}
    assert set(assigned) == {early, late}
    assert assigned[early].prediction_as_of >= datetime(2026, 9, 11, 20, 0, tzinfo=UTC)
    assert assigned[early].prediction_as_of < datetime(2026, 9, 12, 2, 0, tzinfo=UTC)
    assert assigned[late].prediction_as_of >= datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    assert assigned[late].prediction_as_of < datetime(2026, 9, 12, 20, 0, tzinfo=UTC)


def test_pit_safe_planner_invariants_and_no_duplicates(db: psycopg.Connection):
    starts = [2, 6, 13, 20]
    ids = [
        _insert_match(db, datetime(2026, 9, 12, h, 0, tzinfo=UTC),
                      observed=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
        for h in starts
    ]
    safe = derive_run_specs_pit_safe(db, match_ids=tuple(ids))

    seen: list[int] = []
    for spec in safe:
        assert spec.market_as_of >= spec.prediction_as_of
        for match_id in spec.match_ids:
            seen.append(match_id)
            # cutoff precedes every assigned match's start
            assert spec.prediction_as_of < datetime(2026, 9, 12, 23, 59, tzinfo=UTC)
    assert sorted(seen) == sorted(ids)
    assert len(seen) == len(set(seen))  # no duplicate sporting matches across runs
    assert [s.run_sequence for s in safe] == sorted(s.run_sequence for s in safe)


def test_pit_safe_planner_keeps_after_start_matches_visible_to_loader(db: psycopg.Connection):
    # First observed after its own start: not recoverable, must still be attempted
    # (and fail closed) rather than silently dropped.
    late_fixture = _insert_match(
        db, datetime(2026, 9, 12, 2, 0, tzinfo=UTC),
        observed=datetime(2026, 9, 12, 5, 0, tzinfo=UTC),
    )
    normal = _insert_match(
        db, datetime(2026, 9, 12, 18, 0, tzinfo=UTC),
        observed=datetime(2026, 9, 12, 8, 0, tzinfo=UTC),
    )
    safe = derive_run_specs_pit_safe(db, match_ids=(late_fixture, normal))
    seen = {mid for spec in safe for mid in spec.match_ids}
    assert seen == {late_fixture, normal}


def test_pit_safe_planner_market_cutoff_after_prediction_before_start(db: psycopg.Connection):
    match_id = _insert_match(
        db, datetime(2026, 9, 12, 20, 0, tzinfo=UTC),
        observed=datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
    )
    spec = derive_run_specs_pit_safe(db, match_ids=(match_id,), market_delay_seconds=90)[0]
    assert spec.prediction_as_of == datetime(2026, 9, 12, 10, 0, tzinfo=UTC)
    assert spec.market_as_of == spec.prediction_as_of + timedelta(seconds=90)
    assert spec.market_as_of < datetime(2026, 9, 12, 20, 0, tzinfo=UTC)


def test_pit_safe_planner_market_cutoff_capped_before_start(db: psycopg.Connection):
    # Start only 30 seconds after the hourly boundary: market delay must be capped.
    match_id = _insert_match(
        db, datetime(2026, 9, 12, 10, 0, 30, tzinfo=UTC),
        observed=datetime(2026, 9, 12, 4, 0, tzinfo=UTC),
    )
    spec = derive_run_specs_pit_safe(db, match_ids=(match_id,), market_delay_seconds=600)[0]
    assert spec.prediction_as_of == datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    assert spec.market_as_of < datetime(2026, 9, 12, 10, 0, 30, tzinfo=UTC)
    assert spec.market_as_of >= spec.prediction_as_of
