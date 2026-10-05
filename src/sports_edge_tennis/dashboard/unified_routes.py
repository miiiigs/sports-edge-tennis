"""Unified Sports Edge UI routes.

The existing Tennis dashboard remains the visual and functional baseline. This
module adds the shared Sports Edge shell routes without rewriting any Tennis
functionality:

* ``/overview``   - a launchpad with one independent card per sport
* ``/tennis*``    - aliases onto the existing Tennis pages
* ``/football*``  - native Football pages rendered through the shared shell,
                    backed by a server-side read-only Football client

Existing Tennis URLs (``/today``, ``/value``, ``/bet-history``,
``/model-pick-results`` ...) are untouched and keep working. No Tennis
prediction, Manager, settlement, betting, provider or database behaviour is
modified here.

The new routes are registered onto the single shared ``app`` object so that
whichever entrypoint production uses (``app``, ``owner_access`` or
``production_app``) they are present and remain covered by owner auth.
"""

from __future__ import annotations

from fastapi import Query, Request
from fastapi.responses import HTMLResponse

from sports_edge_tennis.dashboard import football_client as fb
from sports_edge_tennis.dashboard.app import (
    DEMO_MODE,
    _latest_activity,
    _load_issued_market_overlay,
    _load_live_health,
    _load_state,
    _request_timezone,
    app,
    build_today_picks,
    performance,
    templates,
    today,
)
from sports_edge_tennis.dashboard.pagination import (
    DEFAULT_PAGE_SIZE,
    build_page_url,
    paginate,
)
from sports_edge_tennis.dashboard.timezone import dashboard_today

# --------------------------------------------------------------- shared shell


def _sport_status(response: fb.FootballResponse) -> tuple[str, str]:
    if response.ok:
        return ("live", "Football")
    if response.state == "NOT_CONFIGURED":
        return ("no-data", "Football not configured")
    return ("stale", "Football unavailable")


# ------------------------------------------------------------------- overview


def _tennis_overview(tz) -> dict:
    """A compact, honest Tennis summary. Never raises into the overview page."""
    if DEMO_MODE:
        return {
            "available": True,
            "latest_activity": None,
            "pick_count": None,
            "value_bet_count": None,
            "model_pick_count": None,
            "system_state": "demo",
            "failed": False,
        }
    try:
        requested = dashboard_today(tz)
        state = _load_state(requested, tz)
        market = _load_issued_market_overlay(requested, tz)
        picks = build_today_picks(state.matches, market)
        health = _load_live_health()
        return {
            "available": True,
            "latest_activity": _latest_activity(),
            "pick_count": picks.total,
            "value_bet_count": picks.value_bet_count,
            "model_pick_count": picks.model_pick_count,
            "system_state": health.status if health else None,
            "failed": False,
        }
    except Exception:  # noqa: BLE001 - overview must never 500 on a data error
        return {
            "available": False,
            "latest_activity": None,
            "pick_count": None,
            "value_bet_count": None,
            "model_pick_count": None,
            "system_state": None,
            "failed": True,
        }


@app.get("/overview", response_class=HTMLResponse, name="sports_edge_overview")
def overview(request: Request):
    tz = _request_timezone(request)
    tennis = _tennis_overview(tz)
    football_response = fb.load_candidates()
    football = fb.build_overview_football(football_response)
    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            "active_sport": "overview",
            "active_nav": "overview",
            "demo_mode": DEMO_MODE,
            "latest_activity": tennis.get("latest_activity"),
            "tennis": tennis,
            "football": football,
            "football_state": football_response.state,
        },
    )


# -------------------------------------------------------------- tennis routes


@app.get("/tennis", response_class=HTMLResponse, name="tennis_today")
def tennis_today(request: Request, date: str | None = Query(default=None)):
    return today(request, date)


@app.get("/tennis/performance", response_class=HTMLResponse, name="tennis_performance")
def tennis_performance(request: Request):
    return performance(request)


@app.get("/tennis/history", response_class=HTMLResponse, name="tennis_history")
def tennis_history(
    request: Request,
    date: str | None = Query(default=None),
    history_page: int = Query(default=1, ge=1),
    detail_page: int = Query(default=1, ge=1),
):
    # Imported lazily: daily_history imports app, and app imports this module.
    from sports_edge_tennis.dashboard.daily_history import bet_history

    return bet_history(
        request,
        date=date,
        history_page=history_page,
        detail_page=detail_page,
    )


@app.get("/tennis/system", response_class=HTMLResponse, name="tennis_system")
def tennis_system(request: Request):
    health = None
    try:
        health = _load_live_health()
    except Exception:  # noqa: BLE001 - degrade rather than 500 on a health read
        health = None
    return templates.TemplateResponse(
        request,
        "tennis_system.html",
        {
            "active_sport": "tennis",
            "active_nav": "system",
            "demo_mode": DEMO_MODE,
            "latest_activity": _latest_activity(),
            "operational_health": health,
        },
    )


# ------------------------------------------------------------ football routes


@app.get("/football", response_class=HTMLResponse, name="football_picks")
def football_picks(request: Request):
    response = fb.load_candidates()
    view = fb.build_picks_view(response)
    return templates.TemplateResponse(
        request,
        "football_picks.html",
        {
            "active_sport": "football",
            "active_nav": "football_picks",
            "demo_mode": DEMO_MODE,
            "sport_status": _sport_status(response),
            "view": view,
        },
    )


@app.get(
    "/football/performance",
    response_class=HTMLResponse,
    name="football_performance",
)
def football_performance(request: Request):
    response = fb.load_performance()
    view = fb.build_performance_view(response)
    return templates.TemplateResponse(
        request,
        "football_performance.html",
        {
            "active_sport": "football",
            "active_nav": "football_performance",
            "demo_mode": DEMO_MODE,
            "sport_status": _sport_status(response),
            "view": view,
        },
    )


@app.get("/football/history", response_class=HTMLResponse, name="football_history")
def football_history(request: Request, page: int = Query(default=1, ge=1)):
    response = fb.load_performance()
    view = fb.build_history_view(response)
    rows, pagination = paginate(view.rows, page=page, page_size=DEFAULT_PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "football_history.html",
        {
            "active_sport": "football",
            "active_nav": "football_history",
            "demo_mode": DEMO_MODE,
            "sport_status": _sport_status(response),
            "view": view,
            "rows": rows,
            "pagination": pagination,
            "page_url": lambda param, page: build_page_url(request, param, page),
        },
    )


@app.get("/football/system", response_class=HTMLResponse, name="football_system")
def football_system(request: Request):
    status = fb.load_system_status()
    health = fb.load_health()
    view = fb.build_system_view(status, health)
    sport_status = ("live", "Football") if (status.ok or health.ok) else _sport_status(status)
    return templates.TemplateResponse(
        request,
        "football_system.html",
        {
            "active_sport": "football",
            "active_nav": "football_system",
            "demo_mode": DEMO_MODE,
            "sport_status": sport_status,
            "view": view,
        },
    )


__all__ = ["football_history", "football_performance", "football_picks", "overview"]
