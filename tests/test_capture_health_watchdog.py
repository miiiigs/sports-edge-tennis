"""Regression tests for the independent capture-health watchdog."""
from datetime import UTC, datetime, timedelta

from ops.validation import capture_health_watchdog as watchdog
from sports_edge_tennis.validation.fixture_lifecycle import (
    NOTIFY_INCIDENT,
    NOTIFY_NONE,
    NOTIFY_RECOVERY,
    decide_monitoring_notification,
    detect_capture_gaps,
    expected_execution_slots,
    reconcile_expected_executions,
)

# The real 12-16 November 2026 outage boundaries, read from data_health_checks.
LAST_BEFORE_GAP = datetime(2026, 11, 12, 23, 1, 16, tzinfo=UTC)
FIRST_AFTER_GAP = datetime(2026, 11, 16, 23, 5, 34, tzinfo=UTC)
RECOVERY_HEALTHY = datetime(2026, 11, 17, 0, 3, 28, tzinfo=UTC)
WINDOW_START = datetime(2026, 11, 12, 23, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)


def test_expected_slots_are_derived_from_the_schedule_not_from_runs():
    slots = expected_execution_slots(WINDOW_START, WINDOW_END, timedelta(hours=1))
    assert len(slots) == 98  # 12 Nov 23:00 through 17 Nov 00:00 inclusive
    assert slots[0] == WINDOW_START
    assert slots[-1] == datetime(2026, 11, 17, 0, 0, tzinfo=UTC)


def test_production_gap_is_95_missed_hourly_executions():
    reconciliation = reconcile_expected_executions(
        [LAST_BEFORE_GAP, FIRST_AFTER_GAP, RECOVERY_HEALTHY],
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        expected_interval=timedelta(hours=1),
    )
    assert reconciliation.expected_slot_count == 98
    assert reconciliation.matched_slot_count == 3
    assert reconciliation.missing_count == 95
    assert reconciliation.missing_slots[0] == datetime(
        2026, 11, 13, 0, 0, tzinfo=UTC
    )
    assert reconciliation.missing_slots[-1] == datetime(
        2026, 11, 16, 22, 0, tzinfo=UTC
    )
    assert reconciliation.duplicate_count == 0


def test_duplicate_execution_in_one_slot_is_flagged_not_counted_as_two_runs():
    first = datetime(2026, 11, 12, 23, 1, tzinfo=UTC)
    second = datetime(2026, 11, 12, 23, 40, tzinfo=UTC)
    reconciliation = reconcile_expected_executions(
        [first, second],
        window_start=WINDOW_START,
        window_end=WINDOW_START + timedelta(hours=1),
        expected_interval=timedelta(hours=1),
    )
    assert reconciliation.matched_slot_count == 1
    assert reconciliation.missing_count == 1
    assert reconciliation.duplicate_count == 1
    assert reconciliation.duplicate_slots[0]["execution_count"] == 2


def test_execution_outside_the_window_is_reported_not_silently_dropped():
    outside = datetime(2026, 11, 17, 1, 3, 28, tzinfo=UTC)
    reconciliation = reconcile_expected_executions(
        [outside],
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        expected_interval=timedelta(hours=1),
    )
    assert reconciliation.unexpected_executions == (outside,)
    assert reconciliation.missing_count == 98


def test_silent_runner_is_detected_without_any_runner_written_rows():
    health = detect_capture_gaps(
        [],
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        expected_interval=timedelta(hours=1),
    )
    assert health.status == "NO_HEARTBEAT"
    assert health.heartbeat_count == 0


def _failure_report(*datasets):
    return {
        "status": "FAILED",
        "failed_datasets": list(datasets),
    }


def test_incident_notification_is_sent_once_and_recovery_is_sent_once():
    now = datetime(2026, 11, 16, 23, 5, tzinfo=UTC)
    first = decide_monitoring_notification(
        _failure_report("live_batch_health"), None, observed_at=now
    )
    assert first.action == NOTIFY_INCIDENT
    assert first.detail == "INCIDENT_STARTED"

    duplicate = decide_monitoring_notification(
        _failure_report("live_batch_health"), first.state, observed_at=now
    )
    assert duplicate.action == NOTIFY_NONE
    assert duplicate.detail == "DUPLICATE_INCIDENT_SUPPRESSED"

    changed = decide_monitoring_notification(
        _failure_report("live_batch_health", "fixture_ingestion"),
        first.state,
        observed_at=now,
    )
    assert changed.action == NOTIFY_INCIDENT
    assert changed.detail == "INCIDENT_CHANGED"

    recovered = decide_monitoring_notification(
        {"status": "HEALTHY", "failed_datasets": []},
        changed.state,
        observed_at=now,
    )
    assert recovered.action == NOTIFY_RECOVERY
    assert recovered.state["active_fingerprint"] is None

    quiet = decide_monitoring_notification(
        {"status": "HEALTHY", "failed_datasets": []},
        recovered.state,
        observed_at=now,
    )
    assert quiet.action == NOTIFY_NONE
    assert quiet.detail == "HEALTHY_NO_CHANGE"


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows_by_dataset):
        self._rows = rows_by_dataset

    def execute(self, sql, params=None):
        for dataset, rows in self._rows.items():
            if dataset in sql:
                return _FakeResult([(row,) for row in rows])
        return _FakeResult([])


def test_build_report_reconciles_missing_slots_and_preserves_datasets():
    conn = _FakeConnection(
        {
            "api_tennis_scheduled_fixtures": [
                LAST_BEFORE_GAP,
                FIRST_AFTER_GAP,
                RECOVERY_HEALTHY,
            ],
            "LIVE_BATCH_RUNTIME": [
                LAST_BEFORE_GAP,
                FIRST_AFTER_GAP,
                RECOVERY_HEALTHY,
            ],
            "prediction_runs": [
                LAST_BEFORE_GAP,
                FIRST_AFTER_GAP,
                RECOVERY_HEALTHY,
            ],
        }
    )
    report = watchdog.build_report(
        conn,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        expected_interval=timedelta(hours=1),
    )
    assert report["status"] == "FAILED"
    assert report["total_missing_execution_slots"] == 3 * 95
    dataset = report["datasets"]["live_batch_health"]
    assert dataset["missing_execution_count"] == 95
    assert dataset["matched_execution_count"] == 3
    assert dataset["missing_execution_slots"][0].startswith("2026-11-13T00:00")
    assert dataset["missing_execution_slots_truncated"] is True


def test_state_round_trip_suppresses_duplicate_notifications(tmp_path):
    state_path = tmp_path / "watchdog-state.json"
    now = datetime(2026, 11, 16, 23, 5, tzinfo=UTC)
    decision = decide_monitoring_notification(
        _failure_report("live_batch_health"), None, observed_at=now
    )
    watchdog._write_state(str(state_path), decision.state)
    assert watchdog._load_state(str(state_path))["active_fingerprint"] == (
        "live_batch_health"
    )
    assert watchdog._load_state(str(tmp_path / "absent.json")) is None
