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
    FLAG_WALKOVER_TERMINAL,
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


def test_explicit_walkover_terminal_is_flagged_but_not_graded():
    item = _classify(
        _evidence(
            latest_match_status="WALKOVER",
            api_tennis_event_key="12171502",
            last_fixture_observed_at=NOW - timedelta(days=3),
        )
    )
    assert item.reason == REASON_TERMINAL_FIXTURE_WITHOUT_RESULT
    assert FLAG_WALKOVER_TERMINAL in item.flags


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


# --- endpoint -------------------------------------------------------------

from sports_edge_tennis.live.pending_result_refresh import (  # noqa: E402
    load_pending_model_pick_targets,
)


def test_diagnostics_use_the_same_population_as_recovery_targets(db: psycopg.Connection):
    """Inside the recovery window both readers see exactly the same pending picks."""
    pending = _setup_aligned_pick(db, match_status="SCHEDULED")
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)

    targets = load_pending_model_pick_targets(
        db, as_of=now, window_start=date(2026, 8, 31), window_end=date(2026, 9, 2)
    )
    report = load_pending_result_diagnostics(db, now=now, lookback_days=30)

    assert [t.match_id for t in targets] == [pending]
    assert [item.match_id for item in report.items] == [pending]


def test_pending_results_endpoint_is_read_only(
    db: psycopg.Connection, migrated_postgres: str, monkeypatch
):
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.observability_routes as routes
    from sports_edge_tennis.dashboard.production_app import app

    pending = _setup_aligned_pick(db, match_status="SCHEDULED")
    monkeypatch.setenv("TENNIS_DATABASE_URL", migrated_postgres)
    captured: list[psycopg.Connection] = []
    real_connect = routes._connect

    def spy_connect():
        conn = real_connect()
        captured.append(conn)
        return conn

    def probe_write(conn, **kwargs):
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TEMP TABLE pending_probe (id int)")
        return load_pending_result_diagnostics(conn, **kwargs)

    monkeypatch.setattr(routes, "_connect", spy_connect)
    monkeypatch.setattr(routes, "load_pending_result_diagnostics", probe_write)

    response = TestClient(app).get("/api/dashboard/pending-results")

    assert response.status_code == 200
    body = response.json()
    assert body["initialized"] is True
    assert [item["match_id"] for item in body["items"]] == [pending]
    assert captured and captured[0].closed


def test_pending_results_endpoint_returns_503_without_leaking_errors(monkeypatch):
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.observability_routes as routes
    from sports_edge_tennis.dashboard.production_app import app

    class _Conn:
        closed = False

        def execute(self, *_args, **_kwargs):
            raise psycopg.OperationalError("password=secret host=internal")

        def close(self):
            self.closed = True

    monkeypatch.setattr(routes, "DEMO_MODE", False)
    monkeypatch.setattr(routes, "_connect", lambda: _Conn())

    response = TestClient(app).get("/api/dashboard/pending-results")

    assert response.status_code == 503
    assert "secret" not in response.text
    assert response.json()["initialized"] is False
