"""Browser-native HTTP Basic gate for the production Tennis dashboard.

Runs before routing so client HTML, static files, evidence APIs and unknown
paths share the same boundary. The health check is the only public exception.
Passwords live exclusively in Railway environment variables, never in code,
URLs, cookies, template output or PostgreSQL.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

ENABLED_VAR = "TENNIS_DASHBOARD_BASIC_AUTH_ENABLED"
USERNAME_VAR = "TENNIS_DASHBOARD_BASIC_AUTH_USERNAME"
PASSWORD_VAR = "TENNIS_DASHBOARD_BASIC_AUTH_PASSWORD"
_MIN_PASSWORD_BYTES = 15
_MAX_AUTH_HEADER = 4096
_CHALLENGE = 'Basic realm="Sports Edge Tennis", charset="UTF-8"'
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _is_railway() -> bool:
    return bool(os.environ.get("RAILWAY_PROJECT_ID")) or os.getenv(
        "RAILWAY_ENVIRONMENT_NAME", ""
    ).lower() == "production"


def _authorized(header: str, username: str, password: str) -> bool:
    if not header.startswith("Basic ") or len(header) > _MAX_AUTH_HEADER:
        return False
    try:
        raw = base64.b64decode(header[6:], validate=True)
    except (binascii.Error, ValueError):
        return False
    supplied_name, sep, supplied_password = raw.partition(b":")
    if not sep:
        return False
    # Do not shortcut either comparison to avoid username enumeration.
    name_ok = hmac.compare_digest(supplied_name, username.encode("utf-8"))
    password_ok = hmac.compare_digest(supplied_password, password.encode("utf-8"))
    return name_ok and password_ok


def install_production_basic_auth(app: FastAPI) -> None:
    """Attach the guard only to the production composition, not the base app."""

    @app.middleware("http")
    async def browser_basic_auth(request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)

        enabled = os.getenv(ENABLED_VAR, "").strip().lower() in _TRUTHY
        if not enabled:
            # A missing flag must never turn a Railway production service public.
            if _is_railway():
                return PlainTextResponse(
                    "Dashboard access is not configured.",
                    status_code=503,
                    headers={"Cache-Control": "no-store"},
                )
            # Keep local/CI dashboard development and existing test harnesses.
            return await call_next(request)

        username = os.getenv(USERNAME_VAR, "brian").strip()
        password = os.getenv(PASSWORD_VAR, "")
        if not username or ":" in username or len(password.encode("utf-8")) < _MIN_PASSWORD_BYTES:
            return PlainTextResponse(
                "Dashboard authentication is not configured.",
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )

        if not _authorized(request.headers.get("authorization", ""), username, password):
            return PlainTextResponse(
                "Authentication required.",
                status_code=401,
                headers={"WWW-Authenticate": _CHALLENGE, "Cache-Control": "no-store"},
            )

        return await call_next(request)


__all__ = ["install_production_basic_auth"]
