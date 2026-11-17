"""Seasonal coverage is read-only, scope-safe, unique, and explicitly provisional."""
from __future__ import annotations
from datetime import UTC, date, datetime
import psycopg
from fastapi.testclient import TestClient
from sports_edge_tennis.dashboard.calendar_coverage import (
    HISTORY_SQL, WEEKLY_SQL, load_seasonal_coverage
)
from sports_edge_tennis.dashboard.production_app import app


def test_weekly_and_history_sql_execute_on_migrated_database(migrated_postgres):
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        report = load_seasonal_coverage(
            conn, start_date=date(2026,11,16), end_date=date(2027,1,4),
            as_of=datetime(2026,11,17,tzinfo=UTC)
        )
    assert len(report["weeks"]) == 8
    assert report["weeks"][0]["week_start"] == "2026-11-16"
    assert report["weeks"][-1]["requested_end"] == "2027-01-04"
    assert all(not w["provider_full_week_verified"] for w in report["weeks"])
    assert all(w["fixtures"] == 0 for w in report["weeks"])
    assert "not yet fetched" in report["limitation"]


def test_coverage_route_registered_and_bounded():
    assert "/api/dashboard/calendar-coverage" in {route.path for route in app.routes}
    r = TestClient(app).get(
        "/api/dashboard/calendar-coverage?start_date=2026-11-16&end_date=2027-03-31"
    )
    assert r.status_code == 422


def test_exact_unique_match_and_no_hindsight_guards():
    assert "DISTINCT ON (mv.match_id)" in WEEKLY_SQL
    assert "msi.source_system = 'API_TENNIS'" in WEEKLY_SQL
    assert "pr.experiment_id IS NULL" in WEEKLY_SQL
    assert "prediction_run_targets" in WEEKLY_SQL
    assert "mro.outcome_status = 'COMPLETED'" in HISTORY_SQL
