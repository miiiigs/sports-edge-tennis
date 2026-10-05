from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

from sports_edge_tennis.dashboard.auth import (
    AUTH_SCHEMA,
    AuthSettings,
    AuthStore,
    SESSION_COOKIE_NAME,
)
import sports_edge_tennis.dashboard.owner_access as owner_access


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


def _enable_owner_auth(monkeypatch, auth_store: AuthStore, *, signup: bool = False) -> None:
    monkeypatch.setattr(
        owner_access,
        "SETTINGS",
        AuthSettings(enabled=True, allow_signup=signup, cookie_secure=False),
    )
    monkeypatch.setattr(owner_access, "_store", lambda: auth_store)
    owner_access.templates.env.globals["tennis_auth_enabled"] = True


def test_auth_store_isolated_schema_and_allowlist(auth_store: AuthStore):
    owner = auth_store.create_user(" OWNER@example.com ", "strong-pass-123")
    assert owner["email"] == "owner@example.com"
    assert auth_store.authenticate("owner@example.com", "strong-pass-123")["id"] == owner["id"]
    assert auth_store.authenticate("owner@example.com", "wrong-password") is None

    with pytest.raises(ValueError, match="not authorized"):
        auth_store.create_user("other@example.com", "strong-pass-123")

    with auth_store._connect() as conn:
        assert conn.execute(
            "SELECT to_regclass('sports_edge_auth.users')"
        ).fetchone()[0] == "sports_edge_auth.users"
        assert conn.execute(
            "SELECT to_regclass('public.users')"
        ).fetchone()[0] is None


def test_session_is_opaque_and_expiry_invalidates_access(auth_store: AuthStore):
    owner = auth_store.create_user("owner@example.com", "strong-pass-123")
    token = auth_store.create_session(owner["id"])
    assert token
    assert "owner@example.com" not in token
    assert auth_store.get_user_for_session(token)["email"] == "owner@example.com"

    with auth_store._connect() as conn:
        conn.execute(
            f"UPDATE {AUTH_SCHEMA}.sessions SET expires_at = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=1),),
        )

    assert auth_store.get_user_for_session(token) is None


def test_owner_auth_blocks_pages_and_apis_but_keeps_health_public(
    auth_store: AuthStore,
    monkeypatch,
):
    _enable_owner_auth(monkeypatch, auth_store)

    with TestClient(owner_access.app, follow_redirects=False) as client:
        page = client.get("/today")
        assert page.status_code == 303
        assert page.headers["location"].startswith("/login?next=")

        api = client.get("/api/dashboard/history")
        assert api.status_code == 401
        assert api.json()["detail"] == "Authentication required"

        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"


def test_signup_is_off_by_default_in_owner_mode(auth_store: AuthStore, monkeypatch):
    _enable_owner_auth(monkeypatch, auth_store, signup=False)

    with TestClient(owner_access.app, follow_redirects=False) as client:
        signup_page = client.get("/signup")
        assert signup_page.status_code == 303
        assert signup_page.headers["location"] == "/login"

        signup = client.post(
            "/signup",
            data={
                "email": "owner@example.com",
                "password": "strong-pass-123",
                "confirm_password": "strong-pass-123",
                "next": "/today",
            },
        )
        assert signup.status_code == 403
        assert signup.json()["detail"] == "Account creation is disabled"


def test_authorized_bootstrap_login_logout_flow(auth_store: AuthStore, monkeypatch):
    _enable_owner_auth(monkeypatch, auth_store, signup=True)

    with TestClient(owner_access.app, follow_redirects=False) as client:
        disallowed = client.post(
            "/signup",
            data={
                "email": "other@example.com",
                "password": "strong-pass-123",
                "confirm_password": "strong-pass-123",
                "next": "/today",
            },
        )
        assert disallowed.status_code == 400
        assert "not authorized" in disallowed.text

        signup = client.post(
            "/signup",
            data={
                "email": "owner@example.com",
                "password": "strong-pass-123",
                "confirm_password": "strong-pass-123",
                "next": "/today",
            },
        )
        assert signup.status_code == 303
        assert signup.headers["location"] == "/today"
        assert f"{SESSION_COOKIE_NAME}=" in signup.headers["set-cookie"]

        protected = client.get("/today")
        assert protected.status_code == 200

        logout = client.post("/logout")
        assert logout.status_code == 303
        assert logout.headers["location"] == "/login"

        protected_again = client.get("/today")
        assert protected_again.status_code == 303

        login = client.post(
            "/login",
            data={
                "email": "owner@example.com",
                "password": "strong-pass-123",
                "next": "/history",
            },
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/history"
        assert client.get("/history").status_code == 200


def test_unified_root_and_football_routes_are_owner_protected(monkeypatch):
    """The unified / -> /overview redirect and Football pages inherit owner auth.

    This is the compatibility guarantee only: production protection depends on the
    deployed start command being the owner_access wrapper. The repo Procfile starts
    the plainly-configured app, so this must not be read as production security.
    """

    class _AnonymousStore:
        def get_user_for_session(self, token):
            return None

    monkeypatch.setattr(
        owner_access,
        "SETTINGS",
        AuthSettings(enabled=True, allow_signup=False, cookie_secure=False),
    )
    monkeypatch.setattr(owner_access, "_store", lambda: _AnonymousStore())
    owner_access.templates.env.globals["tennis_auth_enabled"] = True
    try:
        with TestClient(owner_access.app, follow_redirects=False) as client:
            root = client.get("/")
            overview = client.get("/overview")
            football = client.get("/football")
        for response in (root, overview, football):
            assert response.status_code == 303
            assert response.headers["location"].startswith("/login?next=")
    finally:
        owner_access.templates.env.globals["tennis_auth_enabled"] = False


def test_auth_disabled_preserves_existing_public_behavior(auth_store: AuthStore, monkeypatch):
    monkeypatch.setattr(
        owner_access,
        "SETTINGS",
        AuthSettings(enabled=False, allow_signup=False, cookie_secure=False),
    )
    monkeypatch.setattr(owner_access, "_store", lambda: auth_store)
    owner_access.templates.env.globals["tennis_auth_enabled"] = False

    with TestClient(owner_access.app, follow_redirects=False) as client:
        assert client.get("/today").status_code == 200
        login = client.get("/login")
        assert login.status_code == 303
        assert login.headers["location"] == "/today"
