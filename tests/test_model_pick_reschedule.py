"""RESCHEDULED indicator on Model Picks, and grading of non-standard outcomes.

The indicator and the pending-result diagnostics share one rule
(``is_schedule_change``): the start a pick was issued against versus the latest
persisted provider start. Pending duration alone never marks a reschedule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.model_pick_history as model_pick_history
from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary,
    ModelPickResultsView,
    ModelPickRow,
    load_aligned_daily_model_pick_summaries,
    load_aligned_daily_model_picks,
)
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.live.pending_result_diagnostics import (
    FLAG_RESCHEDULED,
    REASON_FIXTURE_NOT_REOBSERVED,
    REASON_FIXTURE_POSTPONED,
    REASON_NOT_STARTED,
    PendingPickEvidence,
    classify_pending_pick,
    is_schedule_change,
    load_pending_result_diagnostics,
)
from sports_edge_tennis.manager.persistence import ensure_manager_policy_version_v1
from tests.abstention_helpers import (
    A1,
    A2,
    A3,
    insert_match,
    insert_run,
    insert_target,
    raw_insert_and_finalize,
    seed_agent_configs,
    utc,
)
from tests.test_result_coverage import _setup_aligned_pick

PIT_START = utc(day=1, hour=9)  # 2026-09-01 09:00Z, start the pick is issued against
PICKED_AT = utc(day=2, hour=1)  # pick as_of in _setup_aligned_pick
NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


# --- shared rule ----------------------------------------------------------


def test_schedule_change_requires_both_starts():
    assert not is_schedule_change(None, PIT_START)
    assert not is_schedule_change(PIT_START, None)


def test_order_of_play_drift_is_not_a_reschedule():
    """2026-10-12 production: same-day drift of up to ~4 h was common."""
    assert not is_schedule_change(PIT_START, PIT_START + timedelta(minutes=59))
    assert not is_schedule_change(PIT_START, PIT_START + timedelta(hours=3, minutes=50))


def test_move_of_six_hours_or_more_is_a_reschedule_either_direction():
    assert is_schedule_change(PIT_START, PIT_START + timedelta(hours=6))
    assert is_schedule_change(PIT_START, PIT_START - timedelta(hours=7))
    # Jinan placeholder: 2026-10-12 02:00Z listed, played 2026-10-13 09:20Z.
    assert is_schedule_change(
        datetime(2026, 10, 12, 2, tzinfo=UTC), datetime(2026, 10, 13, 9, 20, tzinfo=UTC)
    )


def test_move_across_local_midnight_counts_only_in_that_timezone():
    manila = ZoneInfo("Asia/Manila")
    listed = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)  # 23:30 Manila
    played = listed + timedelta(hours=1, minutes=30)  # 01:00 next Manila day
    assert is_schedule_change(listed, played, local_timezone=manila)
    assert not is_schedule_change(listed, played)  # same UTC day, under 6 h
    assert not is_schedule_change(
        listed, listed + timedelta(minutes=40), local_timezone=manila
    )


def _evidence(**overrides) -> PendingPickEvidence:
    base = dict(
        match_id=1,
        pick_source="MANAGER_CONSENSUS",
        picked_at=NOW - timedelta(days=3),
        selected_side=1,
        has_type_a_bet=False,
        pit_match_start_at=NOW - timedelta(days=2),
        latest_match_start_at=NOW - timedelta(days=2),
        latest_match_status="SCHEDULED",
        last_fixture_observed_at=NOW - timedelta(days=2),
        fixture_versions=3,
        source_systems=("API_TENNIS",),
        api_tennis_event_key="12170000",
        tournament="Test Open",
        competition_tier="CHALLENGER",
        player_1="A",
        player_2="B",
    )
    base.update(overrides)
    return PendingPickEvidence(**base)


def test_long_pending_pick_is_not_inferred_rescheduled():
    item = classify_pending_pick(
        _evidence(), now=NOW, recovery_window_start=date(2026, 9, 8)
    )
    assert item.reason == REASON_FIXTURE_NOT_REOBSERVED
    assert FLAG_RESCHEDULED not in item.flags


def test_postponed_fixture_without_new_start_is_its_own_reason():
    item = classify_pending_pick(
        _evidence(latest_match_status="POSTPONED"),
        now=NOW,
        recovery_window_start=date(2026, 9, 8),
    )
    assert item.reason == REASON_FIXTURE_POSTPONED
    assert FLAG_RESCHEDULED not in item.flags


def test_postponed_fixture_moved_to_future_is_not_started_and_rescheduled():
    item = classify_pending_pick(
        _evidence(
            latest_match_status="POSTPONED",
            latest_match_start_at=NOW + timedelta(days=1),
        ),
        now=NOW,
        recovery_window_start=date(2026, 9, 8),
    )
    assert item.reason == REASON_NOT_STARTED
    assert FLAG_RESCHEDULED in item.flags


# --- DB-backed: Model Picks rows ------------------------------------------


def _add_fixture_version(
    conn: psycopg.Connection, match_id: int, *, start: datetime, status: str, observed: datetime
) -> None:
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of
        )
        SELECT match_id, player_1_id, player_2_id, %s, tour_scope, competition_tier,
               surface, round_code, best_of_sets, qualifying_flag, %s, %s, %s
        FROM match_versions
        WHERE match_id = %s
        ORDER BY as_of DESC
        LIMIT 1
        """,
        (start, status, observed, observed, match_id),
    )


def _add_result(
    conn: psycopg.Connection, match_id: int, *, outcome: str, winner_side: int | None, at: datetime
) -> None:
    conn.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (match_id, outcome, winner_side, at, at),
    )


def _only_row(conn: psycopg.Connection, day: date, tz: str = "UTC") -> ModelPickRow:
    rows = load_aligned_daily_model_picks(conn, decision_date=day, timezone_name=tz)
    assert len(rows) == 1
    return rows[0]


def _summary(conn: psycopg.Connection, day: date, tz: str = "UTC") -> DailyModelPickSummary:
    # The summary loader only lists days from the aligned live start; read the
    # detail day directly when the seeded fixture predates it.
    rows = load_aligned_daily_model_pick_summaries(conn, timezone_name=tz)
    return next(row for row in rows if row.decision_date == day)


def test_rescheduled_then_completed_shows_both_starts_and_stays_on_original_day(
    db: psycopg.Connection,
):
    """Jinan pattern: listed at a placeholder start, played the next day, won."""
    match_id = _setup_aligned_pick(db, match_status="SCHEDULED", match_start_at=PIT_START)
    new_start = PIT_START + timedelta(hours=30)
    _add_fixture_version(
        db, match_id, start=new_start, status="SCHEDULED", observed=PICKED_AT + timedelta(hours=2)
    )
    _add_fixture_version(
        db, match_id, start=new_start, status="COMPLETED", observed=new_start + timedelta(hours=2)
    )
    _add_result(db, match_id, outcome="COMPLETED", winner_side=1, at=new_start + timedelta(hours=2))

    row = _only_row(db, PIT_START.date())
    assert row.result_status == "WON"
    assert row.match_start_at == PIT_START
    assert row.latest_match_start_at == new_start
    assert row.is_rescheduled is True
    assert row.is_postponed is False
    # Grouping is unchanged: the pick does not move to the new start's day.
    # Manila: 17:00 Sep 1 originally, 23:00 Sep 2 after the move.
    assert load_aligned_daily_model_picks(db, decision_date=new_start.date()) == ()
    manila = _only_row(db, date(2026, 9, 1), tz="Asia/Manila")
    assert manila.is_rescheduled is True
    assert load_aligned_daily_model_picks(
        db, decision_date=date(2026, 9, 2), timezone_name="Asia/Manila"
    ) == ()


def test_order_of_play_delay_does_not_show_rescheduled(db: psycopg.Connection):
    match_id = _setup_aligned_pick(db, match_status="SCHEDULED", match_start_at=PIT_START)
    _add_fixture_version(
        db,
        match_id,
        start=PIT_START + timedelta(hours=3),
        status="SCHEDULED",
        observed=PICKED_AT + timedelta(hours=1),
    )
    row = _only_row(db, PIT_START.date())
    assert row.is_rescheduled is False


def test_retired_result_is_void_not_graded(db: psycopg.Connection):
    match_id = _setup_aligned_pick(db, match_status="RETIRED", match_start_at=PIT_START)
    _add_result(db, match_id, outcome="RETIRED", winner_side=2, at=PIT_START + timedelta(hours=2))
    row = _only_row(db, PIT_START.date())
    assert row.result_status == "VOID"
    assert row.is_rescheduled is False


def test_abandoned_match_recorded_cancelled_is_void(db: psycopg.Connection):
    """Abandoned matches arrive as CANCELLED; there is no separate enum value."""
    match_id = _setup_aligned_pick(db, match_status="CANCELLED", match_start_at=PIT_START)
    _add_result(db, match_id, outcome="CANCELLED", winner_side=None, at=PIT_START + timedelta(hours=1))
    row = _only_row(db, PIT_START.date())
    assert row.result_status == "VOID"


def test_postponed_fixture_stays_pending_and_is_never_settled(db: psycopg.Connection):
    match_id = _setup_aligned_pick(db, match_status="SCHEDULED", match_start_at=PIT_START)
    _add_fixture_version(
        db, match_id, start=PIT_START, status="POSTPONED", observed=PIT_START + timedelta(hours=1)
    )

    row = _only_row(db, PIT_START.date())
    assert row.result_status == "PENDING"
    assert row.is_postponed is True
    assert row.is_rescheduled is False

    report = load_pending_result_diagnostics(db, now=NOW, lookback_days=60)
    assert [(item.match_id, item.reason) for item in report.items] == [
        (match_id, REASON_FIXTURE_POSTPONED)
    ]


def test_postponed_pick_counts_as_pending_in_daily_summary(db: psycopg.Connection):
    start = datetime(2026, 9, 20, 9, tzinfo=UTC)  # inside the aligned live window
    match_id = insert_match(db, status="SCHEDULED", match_start_at=start)
    picked_at = start - timedelta(hours=6)
    run_id = insert_run(db, as_of=picked_at)
    insert_target(db, run_id=run_id, match_id=match_id)
    raw_insert_and_finalize(
        db,
        run_id=run_id,
        match_id=match_id,
        configs=seed_agent_configs(db),
        specs=(
            (A1, "PREDICTED", 1, "0.700000", "agent1_raw_probability_v1", None),
            (A2, "ABSTAINED", None, None, None, "INSUFFICIENT_HISTORY"),
            (A3, "ABSTAINED", None, None, None, "WEAK_SIGNAL"),
        ),
        finalized_at=picked_at,
    )
    policy = ensure_manager_policy_version_v1(db)
    db.execute(
        """
        INSERT INTO manager_consensus_results (
            prediction_run_id, match_id, manager_policy_version_id, manager_status,
            selected_side, consensus_probability, active_agent_count,
            abstaining_agent_count, probability_spread, reason_code, as_of
        )
        VALUES (%s, %s, %s, 'CONSENSUS', 1, 0.700000, 2, 1, 0.050000, 'NONE', %s)
        """,
        (run_id, match_id, policy.manager_policy_version_id, picked_at),
    )
    _add_fixture_version(
        db, match_id, start=start, status="POSTPONED", observed=start + timedelta(hours=1)
    )
    summary = _summary(db, start.date())
    assert (summary.picks, summary.pending, summary.scored, summary.voids) == (1, 1, 0, 0)


def test_genuinely_missing_result_is_pending_without_reschedule(db: psycopg.Connection):
    match_id = _setup_aligned_pick(db, match_status="SCHEDULED", match_start_at=PIT_START)
    db.execute(
        "INSERT INTO match_source_ids (match_id, source_system, source_match_id) "
        "VALUES (%s, 'API_TENNIS', 'at-missing')",
        (match_id,),
    )
    row = _only_row(db, PIT_START.date())
    assert row.result_status == "PENDING"
    assert row.is_rescheduled is False
    assert row.is_postponed is False

    report = load_pending_result_diagnostics(db, now=NOW, lookback_days=60)
    item = report.items[0]
    assert item.reason == REASON_FIXTURE_NOT_REOBSERVED
    assert FLAG_RESCHEDULED not in item.flags


# --- page and API ---------------------------------------------------------


def _row(**overrides) -> ModelPickRow:
    base = dict(
        manager_consensus_result_id=1,
        picked_at=datetime(2026, 10, 11, 23, tzinfo=UTC),
        match_start_at=datetime(2026, 10, 12, 2, tzinfo=UTC),
        tournament="Jinan Challenger",
        player_1="J. Sweeny",
        player_2="B. Tomic",
        selection="B. Tomic",
        consensus_probability=Decimal("0.70"),
        result_status="PENDING",
        is_value_bet=False,
        manager_policy_version="v1",
        model_regime_key=None,
        match_id=131930,
        latest_match_start_at=datetime(2026, 10, 13, 6, 45, tzinfo=UTC),
        latest_fixture_status="SCHEDULED",
        schedule_changed=True,
    )
    base.update(overrides)
    return ModelPickRow(**base)


def _patch_view(monkeypatch, picks: tuple[ModelPickRow, ...]) -> None:
    day = date(2026, 10, 12)
    summary = DailyModelPickSummary(
        decision_date=day, picks=len(picks), scored=0, pending=len(picks),
        voids=0, wins=0, losses=0, hit_rate=None,
    )
    monkeypatch.setattr(
        model_pick_history,
        "_load_history",
        lambda requested, tz=None: ModelPickResultsView(
            all_summaries=(summary,),
            aligned_summaries=(summary,),
            selected_date=day,
            selected_summary=summary,
            picks=picks,
        ),
    )
    monkeypatch.setattr(model_pick_history, "_latest_activity", lambda: None)


def test_page_shows_rescheduled_badge_with_original_and_updated_start(monkeypatch):
    _patch_view(monkeypatch, (_row(),))
    page = TestClient(app).get("/model-pick-results?date=2026-10-12").text
    assert ">RESCHEDULED</span>" in page
    assert "Originally" in page and "→ now" in page
    assert "Awaiting rescheduled match" in page


def test_page_shows_no_badge_for_unchanged_start(monkeypatch):
    _patch_view(
        monkeypatch,
        (_row(
            latest_match_start_at=datetime(2026, 10, 12, 2, tzinfo=UTC),
            schedule_changed=False,
        ),),
    )
    page = TestClient(app).get("/model-pick-results?date=2026-10-12").text
    # Only the legend in the section note remains.
    assert page.count(">RESCHEDULED</span>") == 1
    assert "Originally" not in page


def test_page_marks_postponed_pick_as_not_graded(monkeypatch):
    _patch_view(
        monkeypatch,
        (_row(
            latest_match_start_at=datetime(2026, 10, 12, 2, tzinfo=UTC),
            latest_fixture_status="POSTPONED",
            schedule_changed=False,
        ),),
    )
    page = TestClient(app).get("/model-pick-results?date=2026-10-12").text
    assert "Postponed by provider · not graded" in page


def test_api_exposes_schedule_change_evidence(monkeypatch):
    _patch_view(monkeypatch, (_row(),))
    body = TestClient(app).get("/api/dashboard/model-pick-results?date=2026-10-12").json()
    pick = body["picks"][0]
    assert pick["match_start_at"].startswith("2026-10-12T02:00")
    assert pick["latest_match_start_at"].startswith("2026-10-13T06:45")
    assert pick["rescheduled"] is True
    assert pick["postponed"] is False
    assert pick["result"] == "PENDING"
