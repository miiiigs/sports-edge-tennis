"""The observability diagnostics endpoints must be covered by dashboard owner auth."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from sports_edge_tennis.dashboard.auth import AUTH_SCHEMA, AuthSettings, AuthStore
import sports_edge_tennis.dashboard.owner_access as owner_access
import sports_edge_tennis.dashboard.production_app  # noqa: F401  registers the routes

API_PATHS = (
    "/api/dashboard/agent-abstention",
    "/api/dashboard/result-coverage",
    "/api/dashboard/pending-results",
    "/api/dashboard/monitoring-summary",
    "/api/dashboard/calibration-health",
)
HTML_PATHS = ("/diagnostics/calibration",)
NEW_PATHS = API_PATHS + HTML_PATHS


@pytest.fixture()
def auth_store(migrated_postgres: str, monkeypatch):
    dsn = migrated_postgres
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {AUTH_SCHEMA} CASCADE")
    monkeypatch.setenv("TENNIS_DASHBOARD_AUTH_ALLOWED_EMAILS", "owner@example.com")
    store = AuthStore(dsn)
    store.initialize()
    try:
        yield store
    finally:
        owner_access.templates.env.globals["tennis_auth_enabled"] = False
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {AUTH_SCHEMA} CASCADE")


def test_new_observability_apis_require_owner_auth_when_enabled(
    auth_store: AuthStore, monkeypatch
):
    monkeypatch.setattr(
        owner_access,
        "SETTINGS",
        AuthSettings(enabled=True, allow_signup=False, cookie_secure=False),
    )
    monkeypatch.setattr(owner_access, "_store", lambda: auth_store)
    owner_access.templates.env.globals["tennis_auth_enabled"] = True

    with TestClient(owner_access.app, follow_redirects=False) as client:
        for path in API_PATHS:
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.json()["detail"] == "Authentication required"
        for path in HTML_PATHS:
            response = client.get(path)
            assert response.status_code == 303, path
            assert response.headers["location"].startswith("/login?next=")


def test_new_observability_apis_are_not_public_paths():
    for path in NEW_PATHS:
        assert path not in owner_access._PUBLIC_PATHS
