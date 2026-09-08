"""Sports Edge Tennis - Client Dashboard MVP (read-only FastAPI application)."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sports_edge_tennis.dashboard import presentation as pres
from sports_edge_tennis.dashboard.demo import build_demo_state
from sports_edge_tennis.dashboard.read_model import (
    RunState,
    get_history,
    get_latest_activity,
    get_run_for_date,
)
from sports_edge_tennis.dashboard.upcoming import get_upcoming_matches

_BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Sports Edge Tennis", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

DEMO_MODE = os.environ.get("SPORTS_EDGE_DASHBOARD_DEMO", "0") == "1"

MODEL_PERFORMANCE = (
    {
        "name": "Agent 1 · Surface Elo",
        "sample": "77,458",
        "accuracy": "64.27%",
        "log_loss": "0.6317",
        "brier": "0.2208",
        "ece": "0.0252",
        "note": "Broad historical coverage with surface-adjusted Elo probabilities.",
    },
    {
        "name": "Agent 2 · Hierarchical Markov",
        "sample": "73,986",
        "accuracy": "60.11%",
        "log_loss": "0.6674",
        "brier": "0.2363",
        "ece": "0.0341",
        "note": "Point-level serve/return model with empirical-Bayes priors.",
    },
    {
        "name": "Agent 3 · Intransitivity Filter",
        "sample": "543",
        "accuracy": "70.34%",
        "log_loss": "0.6606",
        "brier": "0.2338",
        "ece": "0.1568",
        "note": "Highly selective filter; it intentionally abstains on most matches.",
    },
)


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _connect() -> psycopg.Connection | None:
    dsn = _db_dsn()
    if not dsn:
        return None
    try:
        return psycopg.connect(dsn, autocommit=True)
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_utc_date() -> date:
    return _now_utc().date()


def _parse_date(raw: str | None) -> date | None:
    if raw is None:
        return _now_utc_date()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _load_state(requested_date: date) -> RunState:
    if DEMO_MODE:
        return build_demo_state()
    conn = _connect()
    if conn is None:
        return RunState(kind="DB_ERROR", run_date=requested_date, as_of=None, prediction_run_id=None, run_status=None)
    try:
        return get_run_for_date(conn, requested_date)
    finally:
        conn.close()


def _load_history():
    if DEMO_MODE:
        state = build_demo_state()
        return [state]
    conn = _connect()
    if conn is None:
        return []
    try:
        return get_history(conn)
    finally:
        conn.close()


def _latest_activity():
    if DEMO_MODE:
        return build_demo_state().as_of
    conn = _connect()
    if conn is None:
        return None
    try:
        return get_latest_activity(conn)
    finally:
        conn.close()


def _load_upcoming():
    if DEMO_MODE:
        return []
    conn = _connect()
    if conn is None:
        return []
    try:
        return get_upcoming_matches(conn, now=_now_utc(), days=3, limit=24)
    finally:
        conn.close()


@app.get("/", response_class=HTMLResponse)
def index():
    return RedirectResponse(url="/today")


@app.get("/today", response_class=HTMLResponse)
def today(request: Request, date: str | None = Query(default=None)):
    requested_date = _parse_date(date)
    if requested_date is None:
        return HTMLResponse("Invalid date", status_code=400)

    state = _load_state(requested_date)
    return templates.TemplateResponse(
        request,
        "today.html",
        {
            "state": state,
            "pres": pres,
            "selected_date": requested_date,
            "latest_activity": _latest_activity(),
            "upcoming": _load_upcoming(),
            "demo_mode": DEMO_MODE,
            "prev_date": requested_date.fromordinal(requested_date.toordinal() - 1),
            "next_date": requested_date.fromordinal(requested_date.toordinal() + 1),
        },
    )


@app.get("/performance", response_class=HTMLResponse)
def performance(request: Request):
    return templates.TemplateResponse(
        request,
        "performance.html",
        {
            "models": MODEL_PERFORMANCE,
            "pres": pres,
            "demo_mode": DEMO_MODE,
            "latest_activity": _latest_activity(),
        },
    )


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "history": _load_history(),
            "pres": pres,
            "demo_mode": DEMO_MODE,
            "latest_activity": _latest_activity(),
        },
    )


@app.get("/healthz")
def healthz():
    return JSONResponse({"status": "ok"})


@app.get("/api/dashboard/today")
def api_today(date: str | None = Query(default=None)):
    requested_date = _parse_date(date)
    if requested_date is None:
        return JSONResponse({"error": "invalid date"}, status_code=400)
    state = _load_state(requested_date)
    upcoming = _load_upcoming()
    payload = {
        "date": state.run_date.isoformat() if state.run_date else None,
        "kind": state.kind,
        "run_status": state.run_status,
        "as_of": state.as_of.isoformat() if state.as_of else None,
        "matches": [
            {
                "player_1": m.player_1,
                "player_2": m.player_2,
                "tournament": m.tournament,
                "competition_tier": pres.tier_label(m.competition_tier),
                "scheduled_start": m.scheduled_start.isoformat() if m.scheduled_start else None,
                "model_pick": m.selected_player,
                "consensus_probability": pres.percent(m.consensus_probability),
                "manager_status": m.manager_status,
                "client_state": m.client_state,
                "client_state_label": pres.state_label(m.client_state),
                "player_1_price": str(m.player_1_price) if m.player_1_price else None,
                "player_2_price": str(m.player_2_price) if m.player_2_price else None,
            }
            for m in state.matches
        ],
        "upcoming": [
            {
                "player_1": m.player_1,
                "player_2": m.player_2,
                "tournament": m.tournament,
                "competition_tier": pres.tier_label(m.competition_tier),
                "scheduled_start": m.scheduled_start.isoformat(),
                "model_pick": m.selected_player,
                "consensus_probability": pres.percent(m.consensus_probability),
                "manager_status": m.manager_status,
            }
            for m in upcoming
        ],
    }
    return JSONResponse(payload)


@app.get("/api/dashboard/history")
def api_history():
    rows = _load_history()
    return JSONResponse(
        [
            {
                "date": r.run_date.isoformat() if r.run_date else None,
                "kind": r.kind,
                "run_status": r.run_status,
                "match_count": r.match_count,
                "consensus_count": r.consensus_count,
                "no_bet_count": r.no_bet_count,
                "sharp_count": r.sharp_count,
            }
            for r in rows
        ]
    )
