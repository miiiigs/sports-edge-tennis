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

from sports_edge_tennis.clv import ClvSummary, load_clv_summary
from sports_edge_tennis.dashboard import presentation as pres
from sports_edge_tennis.dashboard.demo import build_demo_market_decisions, build_demo_state
from sports_edge_tennis.dashboard.market_overlay import get_market_decisions_for_run
from sports_edge_tennis.dashboard.picks import (
    build_today_picks,
    BLOCKED,
    MODEL_PICK,
    VALUE_BET,
)
from sports_edge_tennis.dashboard.read_model import (
    LiveHealthView,
    RunState,
    get_history,
    get_latest_activity,
    get_latest_live_operational_health,
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


def _load_market_overlay(prediction_run_id: int | None):
    if DEMO_MODE:
        return build_demo_market_decisions()
    if prediction_run_id is None:
        return {}
    conn = _connect()
    if conn is None:
        return {}
    try:
        return get_market_decisions_for_run(conn, prediction_run_id=prediction_run_id)
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


def _load_live_health() -> LiveHealthView | None:
    if DEMO_MODE:
        return None
    conn = _connect()
    if conn is None:
        return None
    try:
        return get_latest_live_operational_health(conn)
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


def _load_clv_performance() -> ClvSummary:
    if DEMO_MODE:
        return ClvSummary(initialized=False)
    conn = _connect()
    if conn is None:
        return ClvSummary(initialized=False)
    try:
        return load_clv_summary(conn)
    finally:
        conn.close()


def _ratio_text(value) -> str | None:
    if value is None:
        return None
    return f"{float(value) * 100:.1f}%"


def _clv_text(value) -> str | None:
    if value is None:
        return None
    return f"{float(value) * 100:+.2f}%"


def _pick_payload(pick) -> dict:
    """Client-facing pick payload. No internal identifiers are exposed."""
    return {
        "kind": pick.kind,
        "kind_label": pick.kind_label,
        "classification": pick.classification,
        "player": pick.player,
        "opponent": pick.opponent,
        "win_probability": pick.probability_text,
        "tournament": pick.tournament,
        "competition_tier": pick.tier_text,
        "scheduled_start": (
            pick.scheduled_start.isoformat() if pick.scheduled_start else None
        ),
        "is_actionable_bet": pick.is_actionable_bet,
        "offered_price": str(pick.offered_price) if pick.offered_price else None,
        "bookmaker": pick.bookmaker,
        "edge_pct": pick.edge_text,
        "stake": pick.stake_text,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return RedirectResponse(url="/today")


@app.get("/today", response_class=HTMLResponse)
def today(request: Request, date: str | None = Query(default=None)):
    """Today's client-visible picks only.

    Every evaluated target is loaded, but only records that reached a Manager
    consensus are rendered by default. Manager NO_BET rows are available only
    through an optional collapsed disclosure on the same page.
    """
    requested_date = _parse_date(date)
    if requested_date is None:
        return HTMLResponse("Invalid date", status_code=400)

    state = _load_state(requested_date)
    market_by_match = _load_market_overlay(state.prediction_run_id)
    today_picks = build_today_picks(state.matches, market_by_match)
    no_bet_matches = [m for m in state.matches if m.manager_status == "NO_BET"]
    return templates.TemplateResponse(
        request,
        "today.html",
        {
            "state": state,
            "pres": pres,
            "selected_date": requested_date,
            "latest_activity": _latest_activity(),
            "operational_health": _load_live_health(),
            "today_picks": today_picks,
            "no_bet_matches": no_bet_matches,
            "demo_mode": DEMO_MODE,
            "prev_date": requested_date.fromordinal(requested_date.toordinal() - 1),
            "next_date": requested_date.fromordinal(requested_date.toordinal() + 1),
        },
    )


@app.get("/value", response_class=HTMLResponse)
def value(request: Request, date: str | None = Query(default=None)):
    """Market/value detail, kept off the default morning screen."""
    requested_date = _parse_date(date)
    if requested_date is None:
        return HTMLResponse("Invalid date", status_code=400)

    state = _load_state(requested_date)
    market_by_match = _load_market_overlay(state.prediction_run_id)
    today_picks = build_today_picks(state.matches, market_by_match)
    return templates.TemplateResponse(
        request,
        "value.html",
        {
            "state": state,
            "pres": pres,
            "selected_date": requested_date,
            "latest_activity": _latest_activity(),
            "demo_mode": DEMO_MODE,
            "value_picks": [p for p in today_picks.picks if p.kind == VALUE_BET],
            "blocked_picks": [p for p in today_picks.picks if p.kind == BLOCKED],
            "model_only_picks": [p for p in today_picks.picks if p.kind == MODEL_PICK],
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
            "clv": _load_clv_performance(),
            "ratio_text": _ratio_text,
            "clv_text": _clv_text,
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
    market_by_match = _load_market_overlay(state.prediction_run_id)
    upcoming = _load_upcoming()
    operational_health = _load_live_health()
    today_picks = build_today_picks(state.matches, market_by_match)
    payload = {
        "date": state.run_date.isoformat() if state.run_date else None,
        "picks": [_pick_payload(pick) for pick in today_picks.picks],
        "pick_counts": {
            "total": today_picks.total,
            "value_bets": today_picks.value_bet_count,
            "model_picks": today_picks.model_pick_count,
            "blocked": today_picks.blocked_count,
            "evaluated": today_picks.evaluated_count,
        },
        "operational_health": (
            {
                "status": operational_health.status,
                "freshness_state": operational_health.freshness_state,
                "checked_at": operational_health.checked_at.isoformat(),
                "latest_observation_at": (
                    operational_health.latest_observation_at.isoformat()
                    if operational_health.latest_observation_at
                    else None
                ),
                "error_count": operational_health.error_count,
            }
            if operational_health
            else None
        ),
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
                "client_state": pres.client_state(
                    m.manager_status,
                    m.sharp_available,
                    market_by_match.get(m.match_id).decision_status if market_by_match.get(m.match_id) else None,
                ),
                "client_state_label": pres.state_label(
                    pres.client_state(
                        m.manager_status,
                        m.sharp_available,
                        market_by_match.get(m.match_id).decision_status if market_by_match.get(m.match_id) else None,
                    )
                ),
                "player_1_price": str(m.player_1_price) if m.player_1_price else None,
                "player_2_price": str(m.player_2_price) if m.player_2_price else None,
                "offered_price": str(market_by_match[m.match_id].market_decimal_price)
                if m.match_id in market_by_match and market_by_match[m.match_id].market_decimal_price is not None
                else None,
                "offered_bookmaker": market_by_match[m.match_id].price_bookmaker_code
                if m.match_id in market_by_match
                else None,
                "edge_pct": pres.precise_percent(market_by_match[m.match_id].edge_pct)
                if m.match_id in market_by_match
                else "—",
                "market_reason": pres.market_reason_explanation(market_by_match[m.match_id].reason_code)
                if m.match_id in market_by_match
                else None,
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
                "market_state": m.market_state,
                "market_state_label": pres.state_label(m.market_state),
                "offered_price": str(m.market_decimal_price) if m.market_decimal_price is not None else None,
                "offered_bookmaker": m.price_bookmaker_code,
                "edge_pct": pres.precise_percent(m.edge_pct),
                "min_acceptable_odds": str(m.min_acceptable_odds) if m.min_acceptable_odds is not None else None,
                "market_reason": pres.market_reason_explanation(m.market_reason_code),
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


@app.get("/api/dashboard/value")
def api_value(date: str | None = Query(default=None)):
    requested_date = _parse_date(date)
    if requested_date is None:
        return JSONResponse({"error": "invalid date"}, status_code=400)
    state = _load_state(requested_date)
    market_by_match = _load_market_overlay(state.prediction_run_id)
    today_picks = build_today_picks(state.matches, market_by_match)
    return JSONResponse(
        {
            "date": state.run_date.isoformat() if state.run_date else None,
            "value_bets": [
                _pick_payload(p) for p in today_picks.picks if p.kind == VALUE_BET
            ],
            "blocked": [
                _pick_payload(p) for p in today_picks.picks if p.kind == BLOCKED
            ],
            "model_picks_without_value": [
                _pick_payload(p) for p in today_picks.picks if p.kind == MODEL_PICK
            ],
        }
    )


@app.get("/api/dashboard/clv")
def api_clv():
    summary = _load_clv_performance()
    return JSONResponse(
        {
            "initialized": summary.initialized,
            "activation_at": summary.activation_at.isoformat() if summary.activation_at else None,
            "close_records": summary.close_records,
            "complete_closes": summary.complete_closes,
            "partial_closes": summary.partial_closes,
            "missing_closes": summary.missing_closes,
            "close_coverage": _ratio_text(summary.close_coverage),
            "no_bet_close_records": summary.no_bet_close_records,
            "no_bet_complete_closes": summary.no_bet_complete_closes,
            "priced_decisions": summary.priced_decisions,
            "clv_records": summary.clv_records,
            "priced_clv_coverage": _ratio_text(summary.priced_clv_coverage),
            "mean_clv": _clv_text(summary.mean_clv),
            "median_clv": _clv_text(summary.median_clv),
            "interpretation": "Prospective closing-line evidence only; not proof of profitability.",
        }
    )
