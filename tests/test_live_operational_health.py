from datetime import UTC, datetime

from sports_edge_tennis.live.operational_health import (
    DATASET_NAME,
    SOURCE_SYSTEM,
    evaluate_live_operational_health,
    persist_live_operational_health,
)


NOW = datetime(2026, 9, 18, 16, 5, tzinfo=UTC)
CYCLE_START = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "WITH cycle_runs AS" in sql:
            return _Cursor(self.row)
        return _Cursor(None)


def test_live_failure_is_error_without_cycle_query():
    conn = _Conn()

    health = evaluate_live_operational_health(
        conn,
        cycle_started_at=CYCLE_START,
        live_status=2,
    )

    assert health.status == "ERROR"
    assert health.freshness_state == "UNKNOWN"
    assert health.error_count == 1
    assert health.completed_runs == 0
    assert health.target_count == 0
    assert conn.calls == []


def test_successful_nonempty_cycle_is_healthy():
    finalized = datetime(2026, 9, 18, 16, 3, tzinfo=UTC)
    conn = _Conn((2, 22, finalized))

    health = evaluate_live_operational_health(
        conn,
        cycle_started_at=CYCLE_START,
        live_status=0,
    )

    assert health.status == "HEALTHY"
    assert health.freshness_state == "FRESH"
    assert health.completed_runs == 2
    assert health.target_count == 22
    assert health.latest_finalized_at == finalized
    assert health.error_count == 0
    assert "zero_volume=0" in health.notes


def test_partial_successful_horizon_is_warning_with_failed_slate_count():
    finalized = datetime(2026, 9, 19, 2, 3, tzinfo=UTC)
    conn = _Conn((2, 90, finalized))

    health = evaluate_live_operational_health(
        conn,
        cycle_started_at=datetime(2026, 9, 19, 2, 0, tzinfo=UTC),
        live_status=0,
        failed_slates=1,
    )

    assert health.status == "WARNING"
    assert health.freshness_state == "FRESH"
    assert health.completed_runs == 2
    assert health.target_count == 90
    assert health.error_count == 1
    assert "failed_slates=1" in health.notes
    assert "partial_slate_failure=1" in health.notes


def test_successful_zero_target_cycle_is_warning_not_failure():
    finalized = datetime(2026, 9, 18, 16, 3, tzinfo=UTC)
    conn = _Conn((2, 0, finalized))

    health = evaluate_live_operational_health(
        conn,
        cycle_started_at=CYCLE_START,
        live_status=0,
    )

    assert health.status == "WARNING"
    assert health.freshness_state == "FRESH"
    assert health.completed_runs == 2
    assert health.target_count == 0
    assert health.error_count == 0
    assert "zero_volume=1" in health.notes


def test_success_without_completed_run_is_warning():
    conn = _Conn((0, 0, None))

    health = evaluate_live_operational_health(
        conn,
        cycle_started_at=CYCLE_START,
        live_status=0,
    )

    assert health.status == "WARNING"
    assert health.freshness_state == "UNKNOWN"
    assert health.latest_finalized_at is None
    assert "no completed production run" in health.notes


def test_persist_live_operational_health_appends_health_row():
    finalized = datetime(2026, 9, 18, 16, 3, tzinfo=UTC)
    conn = _Conn()
    health = evaluate_live_operational_health(
        _Conn((2, 22, finalized)),
        cycle_started_at=CYCLE_START,
        live_status=0,
    )

    persist_live_operational_health(
        conn,
        health=health,
        checked_at=NOW,
    )

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "INSERT INTO data_health_checks" in sql
    assert params[0] == SOURCE_SYSTEM
    assert params[1] == DATASET_NAME
    assert params[2] == NOW
    assert params[3] == finalized
    assert params[4] == "FRESH"
    assert params[5] == 0
    assert params[6] == "HEALTHY"


def test_one_unknown_status_slate_degrades_healthy_other_slate():
    conn = _Conn((2, 50, NOW))
    health = evaluate_live_operational_health(
        conn,
        cycle_started_at=CYCLE_START,
        live_status=0,
        fixture_warning_slates=1,
        fixture_unknown_status_rows=51,
        fixture_unverified_rejection_rows=51,
        fixture_out_of_scope_empty_slates=0,
    )
    assert health.status == "WARNING"
    assert health.freshness_state == "FRESH"
    assert health.target_count == 50  # another date did process
    assert health.error_count == 0  # a data warning, not a failed worker
    assert "partial_fixture_coverage=1" in health.notes
    assert "fixture_warning_slates=1" in health.notes
    assert "unknown_status_rows=51" in health.notes


def test_only_out_of_scope_empty_slate_can_be_normal_when_other_date_processed():
    conn = _Conn((1, 50, NOW))
    health = evaluate_live_operational_health(
        conn, cycle_started_at=CYCLE_START, live_status=0,
        fixture_warning_slates=0,
        fixture_out_of_scope_empty_slates=1,
    )
    assert health.status == "HEALTHY"
    assert "out_of_scope_empty_slates=1" in health.notes


def test_real_failed_slate_plus_warning_counts_are_both_retained():
    conn = _Conn((1, 10, NOW))
    health = evaluate_live_operational_health(
        conn, cycle_started_at=CYCLE_START, live_status=0,
        failed_slates=1,
        fixture_warning_slates=1,
        fixture_unverified_rejection_rows=3,
    )
    assert health.status == "WARNING"
    assert health.error_count == 1
    assert "partial_slate_failure=1" in health.notes
    assert "partial_fixture_coverage=1" in health.notes


def test_empty_cycle_preserves_per_slate_coverage_evidence():
    conn = _Conn((0, 0, None))
    health = evaluate_live_operational_health(
        conn, cycle_started_at=CYCLE_START, live_status=0,
        fixture_warning_slates=2,
        fixture_unknown_status_rows=17,
    )
    assert health.status == "WARNING"
    assert "unknown_status_rows=17" in health.notes
