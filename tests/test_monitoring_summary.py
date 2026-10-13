"""Read-only monitoring summary: alert rules and DB-backed contract shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.performance.monitoring_summary import (
    CONTRACT_VERSION,
    STATUS_AMBER,
    STATUS_GREEN,
    STATUS_RED,
    evaluate_alerts,
    load_monitoring_summary,
    overall_status,
)

NOW = datetime(2026, 10, 13, 15, 30, tzinfo=UTC)


def _healthy(**overrides):
    snapshot = {
        "latest_prediction_cycle_at": NOW - timedelta(minutes=20),
        "latest_result_refresh_at": NOW - timedelta(minutes=20),
        "latest_sharp_observed_at": NOW - timedelta(minutes=30),
        "latest_offered_observed_at": NOW - timedelta(minutes=30),
        "provider_feeds": [
            {"source_system": "API_TENNIS", "source_dataset": "x", "runs_window": 24, "failed_runs_window": 0}
        ],
        "pending": {"by_flag": {}, "by_reason": {"NOT_STARTED": 4}},
        "calibration_flags": [],
    }
    snapshot.update(overrides)
    return snapshot


def _codes(alerts):
    return [alert.code for alert in alerts]


def test_healthy_snapshot_is_green():
    alerts = evaluate_alerts(_healthy(), now=NOW)
    assert alerts == []
    assert overall_status(alerts) == STATUS_GREEN


def test_sharp_reference_outage_is_red_like_pinnapi_403_since_oct_1():
    alerts = evaluate_alerts(
        _healthy(latest_sharp_observed_at=datetime(2026, 10, 1, 19, 5, tzinfo=UTC)),
        now=NOW,
    )
    assert _codes(alerts) == ["SHARP_REFERENCE_STALE"]
    assert "Type A bets cannot qualify" in alerts[0].detail
    assert overall_status(alerts) == STATUS_RED


def test_missing_evidence_is_treated_as_stale():
    alerts = evaluate_alerts(
        _healthy(
            latest_prediction_cycle_at=None,
            latest_result_refresh_at=None,
            latest_sharp_observed_at=None,
            latest_offered_observed_at=None,
        ),
        now=NOW,
    )
    assert _codes(alerts) == [
        "PREDICTION_CYCLE_STALE",
        "RESULT_REFRESH_STALE",
        "SHARP_REFERENCE_STALE",
        "OFFERED_ODDS_STALE",
    ]


def test_amber_conditions_do_not_escalate_to_red():
    alerts = evaluate_alerts(
        _healthy(
            latest_offered_observed_at=NOW - timedelta(hours=7),
            provider_feeds=[
                {"source_system": "API_TENNIS", "source_dataset": "LIVE_ODDS", "runs_window": 24, "failed_runs_window": 3}
            ],
            pending={"by_flag": {"OUTSIDE_RECOVERY_WINDOW": 13}, "by_reason": {"RESULT_DELAYED": 2}},
            calibration_flags=["HIGH_CONFIDENCE_UNCALIBRATED"],
        ),
        now=NOW,
    )
    assert _codes(alerts) == [
        "OFFERED_ODDS_STALE",
        "PROVIDER_FEED_FAILURES",
        "PENDING_RESULTS_STRANDED",
        "PENDING_RESULTS_DELAYED",
        "CALIBRATION_HIGH_CONFIDENCE_UNCALIBRATED",
    ]
    assert overall_status(alerts) == STATUS_AMBER


def test_prediction_cycle_threshold_boundary():
    assert evaluate_alerts(
        _healthy(latest_prediction_cycle_at=NOW - timedelta(minutes=90)), now=NOW
    ) == []
    assert _codes(
        evaluate_alerts(
            _healthy(latest_prediction_cycle_at=NOW - timedelta(minutes=91)), now=NOW
        )
    ) == ["PREDICTION_CYCLE_STALE"]


def test_summary_contract_shape_on_empty_database(db: psycopg.Connection):
    payload = load_monitoring_summary(db, now=NOW, include_calibration=False)
    assert payload["contract"] == CONTRACT_VERSION
    assert payload["sport"] == "TENNIS"
    assert payload["read_only"] is True
    assert payload["status"] == STATUS_RED
    assert {"PREDICTION_CYCLE_STALE", "SHARP_REFERENCE_STALE"} <= {
        alert["code"] for alert in payload["alerts"]
    }
    tennis = payload["tennis"]
    assert tennis["pending"]["total"] == 0
    assert tennis["oddspapi_usage"]["requests_month_to_date"] == 0
    assert tennis["provider_feeds"] == [] and tennis["odds"] == []
