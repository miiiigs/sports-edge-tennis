"""Cold-import regression contract for the actual Railway Tennis dashboard entrypoint.

Runs in fresh subprocesses to avoid shared FastAPI state from other tests.
No production database connection or provider request is made.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PASSWORD = "test-only-not-for-production-secret-1234567"

BOOTSTRAP = """
import sys
from fastapi.testclient import TestClient

from sports_edge_tennis.dashboard.production_app import app

assert "sports_edge_tennis.dashboard.basic_access" in sys.modules
assert "sports_edge_tennis.dashboard.owner_access" not in sys.modules

paths = {getattr(route, "path", None) for route in app.routes}
for required in (
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
for obsolete in ("/login", "/signup", "/logout"):
    assert obsolete not in paths, obsolete
"""


def _run_fresh_process(*, mode: str, script: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    for name in (
        "TENNIS_DASHBOARD_BASIC_AUTH_ENABLED",
        "TENNIS_DASHBOARD_BASIC_AUTH_USERNAME",
        "TENNIS_DASHBOARD_BASIC_AUTH_PASSWORD",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_ENVIRONMENT_NAME",
        "TENNIS_DATABASE_URL",
        "DATABASE_URL",
        "TEST_POSTGRES_DSN",
    ):
        env.pop(name, None)
    if mode == "basic":
        env["TENNIS_DASHBOARD_BASIC_AUTH_ENABLED"] = "1"
        env["TENNIS_DASHBOARD_BASIC_AUTH_USERNAME"] = "brian"
        env["TENNIS_DASHBOARD_BASIC_AUTH_PASSWORD"] = PASSWORD
        env["RAILWAY_ENVIRONMENT_NAME"] = "production"
    elif mode == "minimum_password":
        env["TENNIS_DASHBOARD_BASIC_AUTH_ENABLED"] = "1"
        env["TENNIS_DASHBOARD_BASIC_AUTH_USERNAME"] = "brian"
        env["TENNIS_DASHBOARD_BASIC_AUTH_PASSWORD"] = "FifteenCharPwd!"
        env["RAILWAY_PROJECT_ID"] = "synthetic-railway-project"
    elif mode == "short_password":
        env["TENNIS_DASHBOARD_BASIC_AUTH_ENABLED"] = "1"
        env["TENNIS_DASHBOARD_BASIC_AUTH_PASSWORD"] = "FourteenChars!"
        env["RAILWAY_PROJECT_ID"] = "synthetic-railway-project"
    elif mode == "missing_password":
        env["TENNIS_DASHBOARD_BASIC_AUTH_ENABLED"] = "1"
        env["RAILWAY_PROJECT_ID"] = "synthetic-railway-project"
    elif mode == "weak_password":
        env["TENNIS_DASHBOARD_BASIC_AUTH_ENABLED"] = "1"
        env["TENNIS_DASHBOARD_BASIC_AUTH_PASSWORD"] = "admin123"
        env["RAILWAY_PROJECT_ID"] = "synthetic-railway-project"
    elif mode == "missing_enable":
        env["RAILWAY_PROJECT_ID"] = "synthetic-railway-project"
    elif mode != "local":
        raise ValueError(mode)

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
        f"production import failed ({mode}, exit={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_production_browser_basic_guards_every_page_and_api():
    _run_fresh_process(
        mode="basic",
        script="""
with TestClient(app, follow_redirects=False) as client:
    for path in ("/today", "/model-pick-results", "/diagnostics/calibration",
                 "/api/dashboard/bet-history", "/api/dashboard/model-pick-results",
                 "/static/styles.css", "/does-not-exist", "/login", "/signup"):
        response = client.get(path)
        assert response.status_code == 401, (path, response.status_code)
        assert response.headers.get("www-authenticate") == 'Basic realm="Sports Edge Tennis", charset="UTF-8"'
        assert response.headers["cache-control"] == "no-store"

    assert client.get("/healthz").status_code == 200
    assert client.get("/today", auth=("brian", "wrong-password")).status_code == 401
    assert client.get("/today", auth=("other", "test-only-not-for-production-secret-1234567")).status_code == 401

    good = ("brian", "test-only-not-for-production-secret-1234567")
    assert client.get("/today", auth=good).status_code == 200
    assert client.get("/login", auth=good).status_code == 404
    assert client.get("/signup", auth=good).status_code == 404
    assert client.get("/api/unknown", auth=good).status_code == 404
""",
    )


def test_production_browser_basic_accepts_minimum_length_with_named_user():
    _run_fresh_process(
        mode="minimum_password",
        script="""
with TestClient(app) as client:
    assert client.get("/today").status_code == 401
    assert client.get("/today", auth=("brian", "FifteenCharPwd!")).status_code == 200
    assert client.get("/today", auth=("", "FifteenCharPwd!")).status_code == 401
    assert client.get("/today", auth=("brian", "WrongPassword!")).status_code == 401
""",
    )


def test_production_browser_basic_rejects_malformed_authorization():
    _run_fresh_process(
        mode="basic",
        script="""
with TestClient(app) as client:
    for header in ("Basic !!!", "Bearer anything", "Basic abc", "Basic " + "A" * 5000):
        response = client.get("/today", headers={"Authorization": header})
        assert response.status_code == 401
    client.cookies.set("sports_edge_tennis_session", "old-session-cookie")
    assert client.get("/today").status_code == 401
""",
    )


def test_production_browser_basic_fails_closed_without_password():
    for mode in ("missing_password", "weak_password", "short_password", "missing_enable"):
        _run_fresh_process(
            mode=mode,
            script="""
with TestClient(app) as client:
    assert client.get("/today").status_code == 503
    assert client.get("/api/dashboard/bet-history").status_code == 503
    assert client.get("/healthz").status_code == 200
""",
        )


def test_local_development_without_railway_remains_usable():
    _run_fresh_process(
        mode="local",
        script="""
with TestClient(app) as client:
    assert client.get("/today").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 404
""",
    )
