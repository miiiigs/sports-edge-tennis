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


def test_unmatched_results_raise_amber_independently_of_stranding():
    alerts = evaluate_alerts(
        _healthy(
            pending={
                "by_flag": {},
                "by_reason": {"NO_API_TENNIS_LINK": 1, "TERMINAL_FIXTURE_WITHOUT_RESULT": 2},
            }
        ),
        now=NOW,
    )
    assert _codes(alerts) == ["PENDING_RESULTS_UNMATCHED"]
    assert alerts[0].detail.startswith("3 pending pick(s)")
    assert overall_status(alerts) == STATUS_AMBER


def test_postponed_and_awaiting_picks_do_not_alert():
    alerts = evaluate_alerts(
        _healthy(
            pending={
                "by_flag": {"RESCHEDULED": 2},
                "by_reason": {"FIXTURE_POSTPONED": 1, "AWAITING_RESULT": 3, "NOT_STARTED": 9},
            }
        ),
        now=NOW,
    )
    assert alerts == []


def test_cycles_that_predict_nothing_are_not_genuine():
    alerts = evaluate_alerts(
        _healthy(prediction_cycles_24h=24, prediction_cycles_with_predictions_24h=0),
        now=NOW,
    )
    assert _codes(alerts) == ["PREDICTION_CYCLES_EMPTY"]
    assert evaluate_alerts(
        _healthy(prediction_cycles_24h=24, prediction_cycles_with_predictions_24h=20),
        now=NOW,
    ) == []


def test_sharp_and_result_freshness_are_independent():
    sharp_only = evaluate_alerts(
        _healthy(latest_sharp_observed_at=NOW - timedelta(hours=7)), now=NOW
    )
    result_only = evaluate_alerts(
        _healthy(latest_result_refresh_at=NOW - timedelta(minutes=181)), now=NOW
    )
    assert _codes(sharp_only) == ["SHARP_REFERENCE_STALE"]
    assert _codes(result_only) == ["RESULT_REFRESH_STALE"]


def test_backfilled_sharp_odds_never_make_the_feed_look_fresh(db: psycopg.Connection):
    from tests.abstention_helpers import insert_match

    match_id = insert_match(db, match_start_at=NOW - timedelta(days=3))
    fresh = NOW - timedelta(minutes=5)
    for origin, key in (("HISTORICAL", "hist-1"), ("SYNTHETIC", "syn-1")):
        db.execute(
            """
            INSERT INTO odds_snapshots (
                match_id, source_system, bookmaker_code, market_type, selection_type,
                decimal_price, source_snapshot_id, source_observed_at, as_of, odds_origin
            )
            VALUES (%s, 'SHARP', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 1.80, %s, %s, %s, %s)
            """,
            (match_id, key, fresh, fresh, origin),
        )

    payload = load_monitoring_summary(db, now=NOW, include_calibration=False)

    assert payload["tennis"]["odds"] == []
    assert payload["tennis"]["latest_sharp_observed_at"] is None
    assert "SHARP_REFERENCE_STALE" in {alert["code"] for alert in payload["alerts"]}


def test_monitoring_endpoint_is_read_only(db: psycopg.Connection, migrated_postgres: str, monkeypatch):
    import pytest
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.observability_routes as routes
    from sports_edge_tennis.dashboard.production_app import app

    monkeypatch.setenv("TENNIS_DATABASE_URL", migrated_postgres)
    real = routes.load_monitoring_summary

    def probe(conn, **kwargs):
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TEMP TABLE monitoring_probe (id int)")
        return real(conn, **kwargs)

    monkeypatch.setattr(routes, "load_monitoring_summary", probe)
    response = TestClient(app).get("/api/dashboard/monitoring-summary?include_calibration=false")
    assert response.status_code == 200
    body = response.json()
    assert body["contract"] == CONTRACT_VERSION
    assert body["read_only"] is True


def test_monitoring_endpoint_returns_503_without_leaking_errors(monkeypatch):
    from fastapi.testclient import TestClient

    import sports_edge_tennis.dashboard.observability_routes as routes
    from sports_edge_tennis.dashboard.production_app import app

    class _Conn:
        def execute(self, *_args, **_kwargs):
            raise psycopg.OperationalError("password=secret")

        def close(self):
            pass

    monkeypatch.setattr(routes, "DEMO_MODE", False)
    monkeypatch.setattr(routes, "_connect", lambda: _Conn())
    response = TestClient(app).get("/api/dashboard/monitoring-summary")
    assert response.status_code == 503
    assert "secret" not in response.text
