"""Sports Edge Tennis - Client Dashboard MVP (read-only FastAPI application)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sports_edge_tennis.clv import ClvSummary, load_clv_summary
from sports_edge_tennis.dashboard import presentation as pres
from sports_edge_tennis.dashboard.demo import (
    build_demo_market_decisions,
    build_demo_guide_odds,
    build_demo_state,
)
from sports_edge_tennis.dashboard.market_overlay import get_market_decisions_for_run
from sports_edge_tennis.dashboard.guide_odds import get_issued_guide_odds
from sports_edge_tennis.dashboard.pagination import (
    DEFAULT_PAGE_SIZE,
    build_page_url,
    paginate,
)
from sports_edge_tennis.dashboard.picks import (
    BLOCKED,
    MODEL_PICK,
    VALUE_BET,
    build_today_picks,
)
from sports_edge_tennis.dashboard.read_model import (
    LiveHealthView,
    RunState,
    get_history,
    get_issued_day_state,
    get_issued_market_decisions,
    get_latest_activity,
    get_latest_live_operational_health,
)
from sports_edge_tennis.dashboard.timezone import (
    DEFAULT_TIMEZONE,
    SUPPORTED_TIMEZONES,
    TIMEZONE_COOKIE_NAME,
    DashboardTimezone,
    dashboard_today,
    format_dashboard_date,
    format_dashboard_datetime,
    format_dashboard_time,
    is_supported_key,
    resolve_dashboard_timezone,
)
from sports_edge_tennis.dashboard.upcoming import get_upcoming_matches
from sports_edge_tennis.performance.regimes import (
    load_regime_cohorts,
    resolve_current_regime,
)

_BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

_TIMEZONE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60


class DashboardTemplates(Jinja2Templates):
    """Jinja2 templates that always know the selected dashboard time zone.

    The selector, the header clock, and every localized timestamp are driven
    from here so no route has to remember to pass them, and no template has to
    hard-code a zone.
    """

    def TemplateResponse(self, request, name, context=None, *args, **kwargs):
        tz = _request_timezone(request)
        merged = dict(context or {})
        merged.setdefault("dashboard_timezone", tz)
        merged.setdefault("dashboard_timezone_options", SUPPORTED_TIMEZONES)
        merged.setdefault("tz_time", lambda value, fmt="%H:%M": format_dashboard_time(value, tz, fmt))
        merged.setdefault(
            "tz_datetime",
            lambda value, fmt="%d %b %H:%M": format_dashboard_datetime(value, tz, fmt),
        )
        merged.setdefault(
            "tz_date",
            lambda value, fmt="%a %d %b %Y": format_dashboard_date(value, tz, fmt),
        )
        return super().TemplateResponse(request, name, merged, *args, **kwargs)


app = FastAPI(title="Sports Edge Tennis", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")
templates = DashboardTemplates(directory=str(_BASE_DIR / "templates"))


def _request_timezone(request: Request) -> DashboardTimezone:
    """The time zone selected for this request (cookie or URL override)."""
    resolved = getattr(request.state, "dashboard_timezone", None)
    if isinstance(resolved, DashboardTimezone):
        return resolved
    return DEFAULT_TIMEZONE


@app.middleware("http")
async def dashboard_timezone_middleware(request: Request, call_next):
    """Resolve the selected time zone and persist an explicit URL override.

    ``?tz=<key>`` is validated against the fixed key set. A valid override is
    written to the preference cookie and then removed from the URL; an invalid
    one is ignored. Arbitrary zone names are never accepted.
    """
    raw_override = request.query_params.get("tz")
    request.state.dashboard_timezone = resolve_dashboard_timezone(
        raw_override if is_supported_key(raw_override) else request.cookies.get(TIMEZONE_COOKIE_NAME)
    )
    if raw_override is not None and is_supported_key(raw_override):
        response = RedirectResponse(
            url=str(request.url.remove_query_params("tz")),
            status_code=303,
        )
        response.set_cookie(
            TIMEZONE_COOKIE_NAME,
            request.state.dashboard_timezone.key,
            max_age=_TIMEZONE_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response
    return await call_next(request)

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
    return datetime.now(UTC)


def _parse_date(raw: str | None, tz: DashboardTimezone | None = None) -> date | None:
    """Parse an explicit date, else default to today in the selected zone."""
    if raw is None:
        return dashboard_today(tz or DEFAULT_TIMEZONE)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _load_state(requested_date: date, tz: DashboardTimezone | None = None) -> RunState:
    if DEMO_MODE:
        return build_demo_state()
    conn = _connect()
    if conn is None:
        return RunState(kind="DB_ERROR", run_date=requested_date, as_of=None, prediction_run_id=None, run_status=None)
    try:
        return get_issued_day_state(
            conn, requested_date, timezone_name=(tz or DEFAULT_TIMEZONE).iana
        )
    finally:
        conn.close()


def _load_issued_market_overlay(
    requested_date: date, tz: DashboardTimezone | None = None
):
    """First issued BET decision per sporting match for the day (Value Bet ledger)."""
    if DEMO_MODE:
        return build_demo_market_decisions()
    conn = _connect()
    if conn is None:
        return {}
    try:
        return get_issued_market_decisions(
            conn, requested_date, timezone_name=(tz or DEFAULT_TIMEZONE).iana
        )
    finally:
        conn.close()


def _load_guide_odds(
    requested_date: date, tz: DashboardTimezone | None = None
):
    """Independent read-only offered-price lookup, not Value Bet qualification."""
    if DEMO_MODE:
        return build_demo_guide_odds()
    conn = _connect()
    if conn is None:
        return {}
    try:
        return get_issued_guide_odds(
            conn, decision_date=requested_date, timezone_name=(tz or DEFAULT_TIMEZONE).iana
        )
    except psycopg.Error:
        # Missing quote evidence must not prevent Model Picks from rendering.
        logger.exception("Guide odds lookup unavailable; displaying missing prices")
        return {}
    finally:
        conn.close()


def _load_issued_model_pick_results(
    requested_date: date, tz: DashboardTimezone | None = None
) -> dict[int, str]:
    """Scored outcomes from the same aligned, first-issued Model Picks ledger."""
    if DEMO_MODE:
        return {}
    conn = _connect()
    if conn is None:
        return {}
    try:
        # Imported lazily: model_pick_history's route module also imports app.
        from sports_edge_tennis.dashboard.model_pick_history import (
            load_aligned_daily_model_picks,
        )
        rows = load_aligned_daily_model_picks(
            conn, decision_date=requested_date, timezone_name=(tz or DEFAULT_TIMEZONE).iana
        )
        return {
            int(row.match_id): row.result_status
            for row in rows
            if row.match_id is not None
        }
    except psycopg.Error:
        logger.exception("Model Pick result lookup unavailable")
        return {}
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


def _load_history(tz: DashboardTimezone | None = None):
    if DEMO_MODE:
        state = build_demo_state()
        return [state]
    conn = _connect()
    if conn is None:
        return []
    try:
        return get_history(conn, timezone_name=(tz or DEFAULT_TIMEZONE).iana)
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


def _load_regime_context(tz: DashboardTimezone | None = None) -> dict:
    """Current production regime, resolved from persisted lineage.

    Returns empty context rather than guessing when the database is
    unreachable or no production run has produced a Manager result yet.
    """
    if DEMO_MODE:
        return {"current_regime": None, "has_multiple_regimes": False}
    conn = _connect()
    if conn is None:
        return {"current_regime": None, "has_multiple_regimes": False}
    try:
        return {
            "current_regime": resolve_current_regime(conn),
            "has_multiple_regimes": len(
                load_regime_cohorts(conn, timezone_name=(tz or DEFAULT_TIMEZONE).iana)
            ) > 1,
        }
    except Exception:
        return {"current_regime": None, "has_multiple_regimes": False}
    finally:
        conn.close()


def _pick_payload(pick) -> dict:
    """Client-facing pick payload. No internal identifiers are exposed."""
    return {
        "kind": pick.kind,
        "kind_label": pick.kind_label,
        "classification": pick.classification,
        "player": pick.player,
        "opponent": pick.opponent,
        "win_probability": pick.probability_text,
        "tracking_probability": (
            pick.primary_average_probability_text if pick.is_wide_spread else None
        ),
        "probability_basis": (
            "A1_A2_TRACKING_AVERAGE" if pick.is_wide_spread else "MANAGER_CONSENSUS"
        ),
        "tournament": pick.tournament,
        "competition_tier": pick.tier_text,
        "scheduled_start": (
            pick.scheduled_start.isoformat() if pick.scheduled_start else None
        ),
        "is_actionable_bet": pick.is_actionable_bet,
        "offered_price": str(pick.offered_price) if pick.offered_price else None,
        "guide_odds": (
            str(pick.guide_odds_price) if pick.guide_odds_price is not None else None
        ),
        "guide_odds_fractional": pick.guide_odds_fractional_text if pick.guide_odds_price is not None else None,
        "guide_market_implied_probability": pick.guide_market_implied_probability_text,
        "guide_bookmaker": pick.guide_bookmaker,
        "guide_quote_observed_at": (
            pick.guide_quote_observed_at.isoformat()
            if pick.guide_quote_observed_at else None
        ),
        "guide_quote_available_at": (
            pick.guide_quote_available_at.isoformat()
            if pick.guide_quote_available_at else None
        ),
        "guide_snapshot_id": pick.guide_snapshot_id,
        "guide_odds_source": pick.guide_odds_source,
        "guide_quote_after_issue": pick.guide_quote_after_issue,
        "result_status": pick.result_status,
        "bookmaker": pick.bookmaker,
        "edge_pct": pick.edge_text,
        "stake": pick.stake_text,
        "is_weak": pick.is_weak,
        "weak_reason": pick.weak_reason,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return RedirectResponse(url="/today")


@app.get("/today", response_class=HTMLResponse)
def today(request: Request, date: str | None = Query(default=None)):
    """Today's client-visible issued picks only.

    The page is an issued-pick ledger, not a complete fixture browser. It keeps
    the first Manager-qualified pick per sporting match for the selected UTC
    match date, plus the latest A1/A2 same-side wide-spread tracking cases.
    """
    tz = _request_timezone(request)
    requested_date = _parse_date(date, tz)
    if requested_date is None:
        return HTMLResponse("Invalid date", status_code=400)

    state = _load_state(requested_date, tz)
    market_by_match = _load_issued_market_overlay(requested_date, tz)
    today_picks = build_today_picks(
        state.matches,
        market_by_match,
        _load_guide_odds(requested_date, tz),
        _load_issued_model_pick_results(requested_date, tz),
    )
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
def value(
    request: Request,
    date: str | None = Query(default=None),
    model_page: int = Query(default=1, ge=1),
):
    """Market/value detail, kept off the default morning screen."""
    tz = _request_timezone(request)
    requested_date = _parse_date(date, tz)
    if requested_date is None:
        return HTMLResponse("Invalid date", status_code=400)

    state = _load_state(requested_date, tz)
    market_by_match = _load_issued_market_overlay(requested_date, tz)
    today_picks = build_today_picks(state.matches, market_by_match)
    # Wide-Spread tracking picks are shown on Today/Model Picks, never in the
    # Value page's model-only table (they are not a market-value comparison).
    model_only_picks = tuple(
        p for p in today_picks.picks if p.kind == MODEL_PICK and not p.is_wide_spread
    )
    model_rows, model_pagination = paginate(
        model_only_picks, page=model_page, page_size=DEFAULT_PAGE_SIZE
    )
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
            "model_only_picks": model_rows,
            "model_pagination": model_pagination,
            "page_url": lambda param, page: build_page_url(request, param, page),
        },
    )


@app.get("/performance", response_class=HTMLResponse)
def performance(request: Request):
    return templates.TemplateResponse(
        request,
        "performance.html",
        {
            "models": MODEL_PERFORMANCE,
            **_load_regime_context(_request_timezone(request)),
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
            "history": _load_history(_request_timezone(request)),
            "pres": pres,
            "demo_mode": DEMO_MODE,
            "latest_activity": _latest_activity(),
        },
    )


@app.get("/healthz")
def healthz():
    return JSONResponse({"status": "ok"})


@app.get("/api/dashboard/today")
def api_today(request: Request, date: str | None = Query(default=None)):
    tz = _request_timezone(request)
    requested_date = _parse_date(date, tz)
    if requested_date is None:
        return JSONResponse({"error": "invalid date"}, status_code=400)
    state = _load_state(requested_date, tz)
    market_by_match = _load_issued_market_overlay(requested_date, tz)
    upcoming = _load_upcoming()
    operational_health = _load_live_health()
    today_picks = build_today_picks(
        state.matches,
        market_by_match,
        _load_guide_odds(requested_date, tz),
        _load_issued_model_pick_results(requested_date, tz),
    )
    payload = {
        "date": state.run_date.isoformat() if state.run_date else None,
        # Canonical timestamps stay timezone-aware ISO-8601 UTC. This block only
        # tells a client how the calendar dates were resolved for display.
        "display_timezone": {
            "key": tz.key,
            "iana": tz.iana,
            "label": tz.display_label,
        },
        "picks": [_pick_payload(pick) for pick in today_picks.picks],
        "pick_counts": {
            "total": today_picks.total,
            "value_bets": today_picks.value_bet_count,
            "model_picks": today_picks.model_pick_count,
            "blocked": today_picks.blocked_count,
            "evaluated": today_picks.evaluated_count,
            # Primary-card total: Model Picks + Value Bets + Veto-blocked.
            # qualified_model_picks excludes Veto-blocked; blocked is the
            # difference between the two definitions.
            "manager_qualified_selections": (
                today_picks.manager_qualified_selection_count
            ),
            "qualified_model_picks": today_picks.qualified_model_pick_count,
            "wide_spread_model_picks": today_picks.wide_spread_model_pick_count,
            "total_model_picks": today_picks.total_model_pick_count,
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
def api_history(request: Request):
    tz = _request_timezone(request)
    rows = _load_history(tz)
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
def api_value(request: Request, date: str | None = Query(default=None)):
    tz = _request_timezone(request)
    requested_date = _parse_date(date, tz)
    if requested_date is None:
        return JSONResponse({"error": "invalid date"}, status_code=400)
    state = _load_state(requested_date, tz)
    market_by_match = _load_issued_market_overlay(requested_date, tz)
    today_picks = build_today_picks(state.matches, market_by_match)
    return JSONResponse(
        {
            "date": state.run_date.isoformat() if state.run_date else None,
            "display_timezone": {
                "key": tz.key,
                "iana": tz.iana,
                "label": tz.display_label,
            },
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
