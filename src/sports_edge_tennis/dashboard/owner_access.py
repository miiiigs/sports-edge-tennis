"""Owner-only access wrapper for the Sports Edge Tennis dashboard."""

from __future__ import annotations

import hmac
import os
from functools import lru_cache
from urllib.parse import parse_qs, quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from sports_edge_tennis.dashboard.app import _db_dsn, app, templates
from sports_edge_tennis.dashboard.auth import (
    AuthSettings,
    AuthStore,
    SESSION_COOKIE_NAME,
    SESSION_DAYS,
)

SETTINGS = AuthSettings.from_env()
BOOTSTRAP_CODE_ENV = "TENNIS_DASHBOARD_AUTH_BOOTSTRAP_CODE"
# A known email address is not proof of ownership. Signup requires both the
# email allowlist and a temporary, out-of-band bootstrap secret.
MIN_BOOTSTRAP_CODE_BYTES = 32
_PUBLIC_PATHS = {"/healthz", "/login", "/signup"}

# Exposed to the shared base template without changing every route context.
templates.env.globals["tennis_auth_enabled"] = SETTINGS.enabled


def _bootstrap_code() -> str:
    return os.getenv(BOOTSTRAP_CODE_ENV, "")


def _signup_available() -> bool:
    return (
        SETTINGS.enabled
        and SETTINGS.allow_signup
        and len(_bootstrap_code().encode("utf-8")) >= MIN_BOOTSTRAP_CODE_BYTES
    )


def _valid_bootstrap_code(candidate: str) -> bool:
    expected = _bootstrap_code()
    if len(expected.encode("utf-8")) < MIN_BOOTSTRAP_CODE_BYTES:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/today"
    return value


@lru_cache(maxsize=1)
def _store() -> AuthStore:
    dsn = _db_dsn()
    if not dsn:
        raise RuntimeError("Tennis dashboard authentication requires TENNIS_DATABASE_URL or DATABASE_URL")
    store = AuthStore(dsn)
    store.initialize()
    return store


async def _form_fields(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _session_user(request: Request):
    try:
        return _store().get_user_for_session(request.cookies.get(SESSION_COOKIE_NAME))
    except Exception:
        return None


def _set_session_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=SETTINGS.cookie_secure,
        samesite="lax",
        path="/",
    )


@app.middleware("http")
async def owner_access_guard(request: Request, call_next):
    if not SETTINGS.enabled:
        return await call_next(request)

    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    try:
        user = _store().get_user_for_session(request.cookies.get(SESSION_COOKIE_NAME))
    except Exception:
        return JSONResponse(
            {"detail": "Dashboard authentication store is unavailable"},
            status_code=503,
        )

    if user is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        next_path = path
        if request.url.query:
            next_path += "?" + request.url.query
        return RedirectResponse(
            f"/login?next={quote(_safe_next(next_path), safe='/?:=&')}",
            status_code=303,
        )

    request.state.auth_user = user
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None):
    if not SETTINGS.enabled:
        return RedirectResponse("/today", status_code=303)
    user = _session_user(request)
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(
        request,
        "auth.html",
        {
            "mode": "login",
            "error": None,
            "next": _safe_next(next),
            "signup_allowed": _signup_available(),
        },
    )


@app.post("/login")
async def login_submit(request: Request):
    if not SETTINGS.enabled:
        return RedirectResponse("/today", status_code=303)
    fields = await _form_fields(request)
    next_path = _safe_next(fields.get("next"))
    try:
        store = _store()
        user = store.authenticate(fields.get("email", ""), fields.get("password", ""))
    except Exception:
        user = None
    if user is None:
        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "mode": "login",
                "error": "Invalid email or password.",
                "next": next_path,
                "signup_allowed": _signup_available(),
            },
            status_code=400,
        )
    response = RedirectResponse(next_path, status_code=303)
    _set_session_cookie(response, store.create_session(user["id"]))
    return response


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, next: str | None = None):
    if not _signup_available():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "auth.html",
        {
            "mode": "signup",
            "error": None,
            "next": _safe_next(next),
            "signup_allowed": True,
        },
    )


@app.post("/signup")
async def signup_submit(request: Request):
    if not _signup_available():
        return JSONResponse({"detail": "Account creation is disabled"}, status_code=403)
    fields = await _form_fields(request)
    if not _valid_bootstrap_code(fields.get("bootstrap_code", "")):
        return JSONResponse({"detail": "Invalid owner setup code"}, status_code=403)
    next_path = _safe_next(fields.get("next"))
    password = fields.get("password", "")
    if password != fields.get("confirm_password", ""):
        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "mode": "signup",
                "error": "Passwords do not match.",
                "next": next_path,
                "signup_allowed": True,
            },
            status_code=400,
        )
    try:
        store = _store()
        user = store.create_user(fields.get("email", ""), password)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "mode": "signup",
                "error": str(exc),
                "next": next_path,
                "signup_allowed": True,
            },
            status_code=400,
        )
    except Exception:
        return templates.TemplateResponse(
            request,
            "auth.html",
            {
                "mode": "signup",
                "error": "Authentication store is unavailable.",
                "next": next_path,
                "signup_allowed": True,
            },
            status_code=503,
        )

    response = RedirectResponse(next_path, status_code=303)
    _set_session_cookie(response, store.create_session(user["id"]))
    return response


@app.post("/logout")
def logout(request: Request):
    if SETTINGS.enabled:
        try:
            _store().delete_session(request.cookies.get(SESSION_COOKIE_NAME))
        except Exception:
            pass
    response = RedirectResponse("/login" if SETTINGS.enabled else "/today", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response
