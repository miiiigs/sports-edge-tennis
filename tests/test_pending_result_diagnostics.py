"""Match-level pending-result reasons: pure classification plus DB-backed loading."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.live.pending_result_diagnostics import (
    AGE_24_TO_72H,
    AGE_NOT_STARTED,
    AGE_OVER_72H,
    AGE_UNDER_6H,
    FLAG_OUTSIDE_RECOVERY_WINDOW,
    FLAG_RESCHEDULED,
    FLAG_TYPE_A_BET,
    REASON_AWAITING_RESULT,
    REASON_FIXTURE_NOT_REOBSERVED,
    REASON_NO_API_TENNIS_LINK,
    REASON_NOT_STARTED,
    REASON_RESULT_DELAYED,
    REASON_TERMINAL_FIXTURE_WITHOUT_RESULT,
    PendingPickEvidence,
    classify_pending_pick,
    load_pending_result_diagnostics,
    report_json,
)

NOW = datetime(2026, 10, 13, 15, 30, tzinfo=UTC)
WINDOW_START = date(2026, 10, 11)


def _evidence(**overrides) -> PendingPickEvidence:
    base = dict(
        match_id=1,
        pick_source="MANAGER_CONSENSUS",
        picked_at=NOW - timedelta(hours=4),
        selected_side=1,
        has_type_a_bet=False,
        pit_match_start_at=NOW - timedelta(hours=2),
        latest_match_start_at=NOW - timedelta(hours=2),
        latest_match_status="SCHEDULED",
        last_fixture_observed_at=NOW - timedelta(minutes=20),
        fixture_versions=5,
        source_systems=("API_TENNIS",),
        api_tennis_event_key="12170000",
        tournament="Test Open",
        competition_tier="CHALLENGER",
        player_1="A",
        player_2="B",
    )
    base.update(overrides)
    return PendingPickEvidence(**base)


def _classify(evidence: PendingPickEvidence):
    return classify_pending_pick(evidence, now=NOW, recovery_window_start=WINDOW_START)


def test_future_start_is_not_started():
    item = _classify(
        _evidence(
            latest_match_start_at=NOW + timedelta(hours=3),
            pit_match_start_at=NOW + timedelta(hours=3),
        )
    )
    assert item.reason == REASON_NOT_STARTED
    assert item.age_bucket == AGE_NOT_STARTED
    assert item.flags == ()


def test_recent_start_still_observed_is_awaiting_result():
    item = _classify(_evidence())
    assert item.reason == REASON_AWAITING_RESULT
    assert item.age_bucket == AGE_UNDER_6H


def test_jinan_placeholder_start_rescheduled_next_day_is_flagged():
    """Regression: 2026-10-12 02:00Z placeholder, actually played 2026-10-13."""
    item = classify_pending_pick(
        _evidence(
            pit_match_start_at=datetime(2026, 10, 12, 2, 0, tzinfo=UTC),
            latest_match_start_at=datetime(2026, 10, 13, 9, 20, tzinfo=UTC),
            last_fixture_observed_at=datetime(2026, 10, 13, 9, 3, tzinfo=UTC),
        ),
        now=datetime(2026, 10, 13, 10, 0, tzinfo=UTC),
        recovery_window_start=WINDOW_START,
    )
    assert item.reason == REASON_AWAITING_RESULT
    assert FLAG_RESCHEDULED in item.flags
    assert FLAG_OUTSIDE_RECOVERY_WINDOW not in item.flags


def test_small_start_drift_is_not_a_reschedule():
    item = _classify(
        _evidence(
            pit_match_start_at=NOW - timedelta(hours=2),
            latest_match_start_at=NOW - timedelta(hours=1, minutes=20),
        )
    )
    assert FLAG_RESCHEDULED not in item.flags


def test_terminal_fixture_without_result_wins_over_other_reasons():
    item = _classify(
        _evidence(
            latest_match_status="COMPLETED",
            api_tennis_event_key=None,
            last_fixture_observed_at=NOW - timedelta(days=10),
        )
    )
    assert item.reason == REASON_TERMINAL_FIXTURE_WITHOUT_RESULT


def test_missing_api_tennis_link():
    item = _classify(_evidence(api_tennis_event_key=None, source_systems=("PINNAPI",)))
    assert item.reason == REASON_NO_API_TENNIS_LINK


def test_withdrawn_provider_fixture_stranded_outside_recovery_window():
    """Regression: match 131712 last observed just before start, never again."""
    start = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)
    item = _classify(
        _evidence(
            pit_match_start_at=start,
            latest_match_start_at=start,
            last_fixture_observed_at=datetime(2026, 10, 5, 10, 1, tzinfo=UTC),
            has_type_a_bet=True,
        )
    )
    assert item.reason == REASON_FIXTURE_NOT_REOBSERVED
    assert item.age_bucket == AGE_OVER_72H
    assert set(item.flags) == {FLAG_OUTSIDE_RECOVERY_WINDOW, FLAG_TYPE_A_BET}


def test_long_running_still_observed_is_result_delayed():
    item = _classify(
        _evidence(
            pit_match_start_at=NOW - timedelta(hours=30),
            latest_match_start_at=NOW - timedelta(hours=30),
            last_fixture_observed_at=NOW - timedelta(minutes=30),
        )
    )
    assert item.reason == REASON_RESULT_DELAYED
    assert item.age_bucket == AGE_24_TO_72H


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        classify_pending_pick(
            _evidence(), now=NOW.replace(tzinfo=None), recovery_window_start=WINDOW_START
        )


def test_log_line_is_single_line_and_carries_identity():
    line = _classify(_evidence(match_id=131924)).log_line()
    assert "\n" not in line
    assert line.startswith("PENDING_RESULT_ITEM match_id=131924 reason=")
    assert "api_tennis_event=12170000" in line


# --- DB-backed -------------------------------------------------------------

from tests.test_result_coverage import _setup_aligned_pick  # noqa: E402


def test_loader_skips_pick_with_terminal_result(db: psycopg.Connection):
    _setup_aligned_pick(db, match_status="SCHEDULED", with_terminal_result=True)
    report = load_pending_result_diagnostics(
        db, now=datetime(2026, 10, 10, 12, tzinfo=UTC), lookback_days=60
    )
    assert report.total == 0


def test_loader_reports_pending_pick(db: psycopg.Connection):
    pending = _setup_aligned_pick(db, match_status="SCHEDULED")

    report = load_pending_result_diagnostics(
        db, now=datetime(2026, 10, 10, 12, tzinfo=UTC), lookback_days=60
    )

    assert [item.match_id for item in report.items] == [pending]
    item = report.items[0]
    # Seeded fixture has no source ids and was last observed at its start.
    assert item.reason == REASON_NO_API_TENNIS_LINK
    assert report.by_reason == {REASON_NO_API_TENNIS_LINK: 1}
    payload = report_json(report)
    assert payload["total"] == 1
    assert payload["items"][0]["match_id"] == pending


def test_loader_marks_terminal_fixture_without_result(db: psycopg.Connection):
    match_id = _setup_aligned_pick(db, match_status="COMPLETED")
    report = load_pending_result_diagnostics(
        db, now=datetime(2026, 10, 10, 12, tzinfo=UTC), lookback_days=60
    )
    assert [(i.match_id, i.reason) for i in report.items] == [
        (match_id, REASON_TERMINAL_FIXTURE_WITHOUT_RESULT)
    ]
