"""Dashboard routes for read-only daily Tennis bet history."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect, _latest_activity, templates
from sports_edge_tennis.dashboard.daily_bets import (
    DailyBetSummary,
    PeriodBetSummary,
    build_performance_windows,
    load_daily_bet_summaries,
    load_daily_bets,
)
from sports_edge_tennis.dashboard import presentation as pres

router = APIRouter()


def _parse_optional_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError("invalid date")


def _pct(value: float | Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def _units(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}u"


def _period_json(summary: PeriodBetSummary) -> dict[str, object]:
    return {
        "bets": summary.bets,
        "settled": summary.settled,
        "pending": summary.pending,
        "wins": summary.wins,
        "losses": summary.losses,
        "voids": summary.voids,
        "cancelled": summary.cancelled,
        "profit_units": str(summary.one_unit_profit),
        "roi": str(summary.roi) if summary.roi is not None else None,
        "hit_rate": summary.hit_rate,
    }


def _load_history(requested_date: date | None):
    if DEMO_MODE:
        return (), requested_date, None, (), False
    conn = _connect()
    if conn is None:
        return (), requested_date, None, (), True
    try:
        summaries = load_daily_bet_summaries(conn)
        selected_date = requested_date or (summaries[0].decision_date if summaries else None)
        selected_summary: DailyBetSummary | None = None
        bets = ()
        if selected_date is not None:
            selected_summary = next(
                (row for row in summaries if row.decision_date == selected_date),
                None,
            )
            bets = load_daily_bets(conn, decision_date=selected_date)
        return summaries, selected_date, selected_summary, bets, False
    except Exception:
        return (), requested_date, None, (), True
    finally:
        conn.close()


@router.get("/bet-history", response_class=HTMLResponse)
def bet_history(request: Request, date: str | None = Query(default=None)):
    try:
        requested_date = _parse_optional_date(date)
    except ValueError:
        return HTMLResponse("Invalid date", status_code=400)

    summaries, selected_date, selected_summary, bets, failed = _load_history(requested_date)
    as_of_date = datetime.now(UTC).date()
    performance_windows = build_performance_windows(summaries, as_of_date=as_of_date)
    return templates.TemplateResponse(
        request,
        "bet_history.html",
        {
            "pres": pres,
            "demo_mode": DEMO_MODE,
            "latest_activity": _latest_activity(),
            "daily_summaries": summaries,
            "selected_date": selected_date,
            "selected_summary": selected_summary,
            "daily_bets": bets,
            "performance_windows": performance_windows,
            "performance_as_of": as_of_date,
            "bet_history_error": failed,
            "pct": _pct,
            "units": _units,
        },
    )


@router.get("/api/dashboard/bet-history")
def api_bet_history(date: str | None = Query(default=None)):
    try:
        requested_date = _parse_optional_date(date)
    except ValueError:
        return JSONResponse({"error": "invalid date"}, status_code=400)

    summaries, selected_date, selected_summary, bets, failed = _load_history(requested_date)
    if failed:
        return JSONResponse({"error": "bet history unavailable"}, status_code=503)

    as_of_date = datetime.now(UTC).date()
    performance_windows = build_performance_windows(summaries, as_of_date=as_of_date)

    return JSONResponse(
        {
            "as_of_date": as_of_date.isoformat(),
            "performance_windows": {
                key: _period_json(value)
                for key, value in performance_windows.items()
            },
            "selected_date": selected_date.isoformat() if selected_date else None,
            "selected_summary": None
            if selected_summary is None
            else {
                "bets": selected_summary.bets,
                "settled": selected_summary.settled,
                "pending": selected_summary.pending,
                "wins": selected_summary.wins,
                "losses": selected_summary.losses,
                "voids": selected_summary.voids,
                "cancelled": selected_summary.cancelled,
                "profit_units": str(selected_summary.one_unit_profit),
                "roi": str(selected_summary.roi) if selected_summary.roi is not None else None,
                "hit_rate": selected_summary.hit_rate,
            },
            "days": [
                {
                    "date": row.decision_date.isoformat(),
                    "bets": row.bets,
                    "wins": row.wins,
                    "losses": row.losses,
                    "voids": row.voids,
                    "cancelled": row.cancelled,
                    "pending": row.pending,
                    "profit_units": str(row.one_unit_profit),
                    "roi": str(row.roi) if row.roi is not None else None,
                    "hit_rate": row.hit_rate,
                }
                for row in summaries
            ],
            "bets": [
                {
                    "decision_id": row.decision_id,
                    "decision_at": row.decision_at.isoformat(),
                    "match_start_at": row.match_start_at.isoformat() if row.match_start_at else None,
                    "tournament": row.tournament,
                    "player_1": row.player_1,
                    "player_2": row.player_2,
                    "selection": row.selection,
                    "odds": str(row.market_decimal_price),
                    "bookmaker": row.bookmaker_code,
                    "consensus_probability": str(row.consensus_probability)
                    if row.consensus_probability is not None
                    else None,
                    "result": row.settlement_status,
                    "profit_units": str(row.profit_units) if row.profit_units is not None else None,
                    "settled_at": row.settled_at.isoformat() if row.settled_at else None,
                }
                for row in bets
            ],
        }
    )
