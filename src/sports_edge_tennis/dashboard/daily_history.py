"""Dashboard routes for read-only daily Tennis bet history."""

from __future__ import annotations

from dataclasses import dataclass
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
from sports_edge_tennis.performance.regimes import (
    RegimeCohort,
    RegimeIdentity,
    current_regime_started_at,
    load_regime_cohorts,
    resolve_current_regime,
)

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


@dataclass(frozen=True)
class ResultsView:
    """Everything the Results surfaces need, already split by regime.

    ``current_summaries`` contains only bets produced by the regime that is
    generating picks right now. ``all_summaries`` is the full production record
    across every regime, unchanged. The two are computed from the same
    first-bet-per-match ledger, so they can never disagree about a bet's terms.
    """

    all_summaries: tuple[DailyBetSummary, ...] = ()
    current_summaries: tuple[DailyBetSummary, ...] = ()
    current_regime: RegimeIdentity | None = None
    current_regime_started_at: datetime | None = None
    cohorts: tuple[RegimeCohort, ...] = ()
    selected_date: date | None = None
    selected_summary: DailyBetSummary | None = None
    bets: tuple = ()
    failed: bool = False

    @property
    def has_multiple_regimes(self) -> bool:
        return len(self.cohorts) > 1


def _load_history(requested_date: date | None) -> ResultsView:
    if DEMO_MODE:
        return ResultsView(selected_date=requested_date)
    conn = _connect()
    if conn is None:
        return ResultsView(selected_date=requested_date, failed=True)
    try:
        summaries = load_daily_bet_summaries(conn)
        current_regime = resolve_current_regime(conn)
        current_summaries: tuple[DailyBetSummary, ...] = ()
        started_at = None
        if current_regime is not None:
            current_summaries = load_daily_bet_summaries(
                conn, regime_key=current_regime.regime_key
            )
            started_at = current_regime_started_at(conn, current_regime.regime_key)
        cohorts = load_regime_cohorts(conn)

        selected_date = requested_date or (
            summaries[0].decision_date if summaries else None
        )
        selected_summary: DailyBetSummary | None = None
        bets: tuple = ()
        if selected_date is not None:
            selected_summary = next(
                (row for row in summaries if row.decision_date == selected_date),
                None,
            )
            bets = load_daily_bets(conn, decision_date=selected_date)
        return ResultsView(
            all_summaries=summaries,
            current_summaries=current_summaries,
            current_regime=current_regime,
            current_regime_started_at=started_at,
            cohorts=cohorts,
            selected_date=selected_date,
            selected_summary=selected_summary,
            bets=bets,
        )
    except Exception:
        return ResultsView(selected_date=requested_date, failed=True)
    finally:
        conn.close()


@router.get("/bet-history", response_class=HTMLResponse)
def bet_history(request: Request, date: str | None = Query(default=None)):
    try:
        requested_date = _parse_optional_date(date)
    except ValueError:
        return HTMLResponse("Invalid date", status_code=400)

    view = _load_history(requested_date)
    as_of_date = datetime.now(UTC).date()
    return templates.TemplateResponse(
        request,
        "bet_history.html",
        {
            "pres": pres,
            "demo_mode": DEMO_MODE,
            "latest_activity": _latest_activity(),
            "daily_summaries": view.all_summaries,
            "selected_date": view.selected_date,
            "selected_summary": view.selected_summary,
            "daily_bets": view.bets,
            # "Current System" windows use only the active regime's bets.
            "current_windows": build_performance_windows(
                view.current_summaries, as_of_date=as_of_date
            ),
            "current_regime": view.current_regime,
            "current_regime_started_at": view.current_regime_started_at,
            "current_bets": sum(row.bets for row in view.current_summaries),
            # "All Historical" spans every production regime, unchanged.
            "performance_windows": build_performance_windows(
                view.all_summaries, as_of_date=as_of_date
            ),
            "cohorts": view.cohorts,
            "has_multiple_regimes": view.has_multiple_regimes,
            "performance_as_of": as_of_date,
            "bet_history_error": view.failed,
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

    view = _load_history(requested_date)
    if view.failed:
        return JSONResponse({"error": "bet history unavailable"}, status_code=503)

    summaries = view.all_summaries
    selected_date = view.selected_date
    selected_summary = view.selected_summary
    bets = view.bets
    as_of_date = datetime.now(UTC).date()
    performance_windows = build_performance_windows(summaries, as_of_date=as_of_date)
    current_windows = build_performance_windows(
        view.current_summaries, as_of_date=as_of_date
    )

    return JSONResponse(
        {
            "as_of_date": as_of_date.isoformat(),
            # Existing key retained for compatibility: all production regimes.
            "performance_windows": {key: _period_json(value) for key, value in performance_windows.items()},
            "current_regime": None
            if view.current_regime is None
            else {
                "regime_key": view.current_regime.regime_key,
                "label": view.current_regime.label,
                "manager_policy_version": view.current_regime.manager_policy_version,
                "started_at": view.current_regime_started_at.isoformat()
                if view.current_regime_started_at
                else None,
            },
            "current_regime_performance": {
                key: _period_json(value) for key, value in current_windows.items()
            },
            "all_historical_performance": {
                key: _period_json(value) for key, value in performance_windows.items()
            },
            "regimes": [
                {
                    "regime_key": c.regime_key,
                    "label": c.label,
                    "manager_policy_version": c.manager_policy_version,
                    "bets": c.bets,
                    "settled": c.settled,
                    "pending": c.pending,
                    "wins": c.wins,
                    "losses": c.losses,
                    "voids": c.voids,
                    "cancelled": c.cancelled,
                    "profit_units": str(c.one_unit_profit),
                    "roi": str(c.roi) if c.roi is not None else None,
                    "hit_rate": c.hit_rate,
                    "first_bet_date": c.first_bet_date.isoformat() if c.first_bet_date else None,
                    "last_bet_date": c.last_bet_date.isoformat() if c.last_bet_date else None,
                    "odds_sources": list(c.odds_sources),
                    "is_current": view.current_regime is not None
                    and c.regime_key == view.current_regime.regime_key,
                }
                for c in view.cohorts
            ],
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
                    "consensus_probability": str(row.consensus_probability) if row.consensus_probability is not None else None,
                    "result": row.settlement_status,
                    "profit_units": str(row.profit_units) if row.profit_units is not None else None,
                    "settled_at": row.settled_at.isoformat() if row.settled_at else None,
                    "regime_key": row.regime_key,
                    "manager_policy_version": row.manager_policy_version,
                    "price_source_system": row.price_source_system,
                }
                for row in bets
            ],
        }
    )
