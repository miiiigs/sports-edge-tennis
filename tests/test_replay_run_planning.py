"""Run planning must place the blind cutoff before every same-day match start."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg

from sports_edge_tennis.replay.loader import (
    ReplayLoadRequest,
    ReplayRunSpec,
    derive_run_specs_from_match_cutoffs,
    derive_run_specs_from_match_days,
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

def test_v1_1_per_match_cutoffs_preserve_each_match_blind_time(db: psycopg.Connection):
    early_start = datetime(2026, 9, 12, 2, 0, tzinfo=UTC)
    later_start = datetime(2026, 9, 12, 18, 0, tzinfo=UTC)
    early = _insert_match(db, early_start)
    later = _insert_match(db, later_start)

    specs = derive_run_specs_from_match_cutoffs(db, match_ids=(early, later))

    assert len(specs) == 2
    by_match = {spec.match_ids[0]: spec for spec in specs}
    assert by_match[early].prediction_as_of == early_start - timedelta(hours=6)
    assert by_match[early].market_as_of == early_start - timedelta(hours=1)
    assert by_match[later].prediction_as_of == later_start - timedelta(hours=6)
    assert by_match[later].market_as_of == later_start - timedelta(hours=1)


def test_v1_1_later_match_can_be_visible_at_own_cutoff(db: psycopg.Connection):
    early_start = datetime(2026, 9, 12, 2, 0, tzinfo=UTC)
    later_start = datetime(2026, 9, 12, 18, 0, tzinfo=UTC)
    early = _insert_match(db, early_start)
    # This scheduled version is not visible at the old day cutoff (20:00 UTC on
    # the prior day), but it is safely visible before its own 12:00 UTC cutoff.
    later = _insert_match(
        db,
        later_start,
        observed=datetime(2026, 9, 12, 10, 0, tzinfo=UTC),
    )

    old_specs = derive_run_specs_from_match_days(db, match_ids=(early, later))
    new_specs = derive_run_specs_from_match_cutoffs(db, match_ids=(early, later))

    assert len(old_specs) == 1
    assert old_specs[0].prediction_as_of == datetime(2026, 9, 11, 20, 0, tzinfo=UTC)
    later_spec = next(spec for spec in new_specs if later in spec.match_ids)
    assert later_spec.prediction_as_of == datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    assert later_spec.prediction_as_of > datetime(2026, 9, 12, 10, 0, tzinfo=UTC)

