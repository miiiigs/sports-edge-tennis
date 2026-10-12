"""Cold-import regression tests for the actual Railway Tennis dashboard entrypoint.

Run each scenario in a fresh process because production_app composes one shared
FastAPI instance at import time. No production PostgreSQL connection is used.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

BOOTSTRAP = """
import sys
from fastapi.testclient import TestClient

from sports_edge_tennis.dashboard.production_app import app

# Production must import owner_access itself. Importing it only in this test
# would mask a regression where production uses the unguarded base app.
assert "sports_edge_tennis.dashboard.owner_access" in sys.modules
from sports_edge_tennis.dashboard import owner_access
assert app is owner_access.app

paths = {getattr(route, "path", None) for route in app.routes}
for required in (
    "/login",
    "/signup",
    "/today",
    "/bet-history",
    "/model-pick-results",
    "/api/dashboard/bet-history",
    "/api/dashboard/model-pick-results",
    "/api/dashboard/result-coverage",
    "/diagnostics/calibration",
    "/healthz",
):
    assert required in paths, required
"""


def _run_fresh_process(monkeypatch, *, enabled: bool, script: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["TENNIS_DASHBOARD_AUTH_ENABLED"] = "true" if enabled else "false"
    env["TENNIS_DASHBOARD_AUTH_ALLOW_SIGNUP"] = "false"
    env["TENNIS_DASHBOARD_AUTH_COOKIE_SECURE"] = "true"
    # A test must never accidentally use a workstation/CI production DSN.
    env.pop("TENNIS_DATABASE_URL", None)
    env.pop("DATABASE_URL", None)
    env.pop("TEST_POSTGRES_DSN", None)

    result = subprocess.run(
        [sys.executable, "-c", BOOTSTRAP + textwrap.dedent(script)],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, (
        f"fresh production import failed (exit={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_production_composition_guards_pages_and_apis_without_losing_routes(monkeypatch):
    _run_fresh_process(
        monkeypatch,
        enabled=True,
        script="""
class GuestlessStore:
    def get_user_for_session(self, cookie):
        return None

owner_access._store = lambda: GuestlessStore()

with TestClient(app, follow_redirects=False) as client:
    for path in ("/today", "/model-pick-results", "/diagnostics/calibration"):
        response = client.get(path)
        assert response.status_code == 303, (path, response.status_code)
        assert response.headers["location"].startswith("/login?next="), path

    for path in ("/api/dashboard/bet-history", "/api/dashboard/model-pick-results", "/api/auth-probe"):
        response = client.get(path)
        assert response.status_code == 401, (path, response.status_code)

    assert client.get("/login").status_code == 200
    assert client.get("/signup").status_code == 303
    assert client.get("/healthz").status_code == 200
""",
    )


def test_production_composition_preserves_unauthenticated_mode(monkeypatch):
    _run_fresh_process(
        monkeypatch,
        enabled=False,
        script="""
with TestClient(app, follow_redirects=False) as client:
    # With auth explicitly off, unknown API routes reach routing (404 vs 401).
    assert client.get("/api/auth-probe").status_code == 404
    assert client.get("/login").status_code == 303
    assert client.get("/healthz").status_code == 200
""",
    )


def test_production_composition_fails_closed_when_auth_store_unavailable(monkeypatch):
    _run_fresh_process(
        monkeypatch,
        enabled=True,
        script="""
def unavailable():
    raise RuntimeError("synthetic auth database outage")

owner_access._store = unavailable

with TestClient(app, follow_redirects=False) as client:
    assert client.get("/today").status_code == 503
    assert client.get("/api/dashboard/bet-history").status_code == 503
    # Platform liveness remains available during an auth-DB outage.
    assert client.get("/healthz").status_code == 200
""",
    )
