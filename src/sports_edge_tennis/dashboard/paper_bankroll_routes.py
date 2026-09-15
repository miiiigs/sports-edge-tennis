"""Read-only API for the prospective Tennis paper bankroll."""

from __future__ import annotations

from decimal import Decimal

from fastapi.responses import JSONResponse

from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect
from sports_edge_tennis.performance.paper_bankroll import load_paper_bankroll_summary


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.2f}%"


def api_paper_bankroll():
    if DEMO_MODE:
        return JSONResponse(
            {
                "initialized": False,
                "reason": "demo mode",
                "starting_bankroll": "1000.00",
            }
        )

    conn = _connect()
    if conn is None:
        return JSONResponse({"error": "paper bankroll unavailable"}, status_code=503)
    try:
        summary = load_paper_bankroll_summary(conn)
    except Exception:
        return JSONResponse({"error": "paper bankroll unavailable"}, status_code=503)
    finally:
        conn.close()

    return JSONResponse(
        {
            "initialized": summary.bets > 0,
            "paper_only": True,
            "staking_rule": "QUARTER_KELLY_CAP_2_V1",
            "first_bet_per_match_only": True,
            "starting_bankroll": _money(summary.starting_bankroll),
            "current_bankroll": _money(summary.current_bankroll),
            "cash": _money(summary.cash),
            "open_exposure": _money(summary.open_exposure),
            "realized_profit": _money(summary.realized_profit),
            "roi": _percent(summary.roi),
            "bets": summary.bets,
            "settled": summary.settled,
            "pending": summary.pending,
            "wins": summary.wins,
            "losses": summary.losses,
            "voids": summary.voids,
            "max_drawdown": _percent(summary.max_drawdown),
            "legacy_bets_excluded": summary.skipped_without_staking_evidence,
            "methodology": (
                "Prospective paper replay only. Uses the first persisted live BET per match, "
                "the publication-time recorded odds, and the persisted quarter-Kelly stake fraction. "
                "It never places a real wager and does not backfill missing historical prices."
            ),
            "curve": [
                {
                    "at": point.at.isoformat(),
                    "decision_id": point.decision_id,
                    "event": point.event,
                    "bankroll": _money(point.bankroll_equity),
                    "cash": _money(point.cash),
                    "open_exposure": _money(point.open_exposure),
                    "realized_profit": _money(point.realized_profit),
                }
                for point in summary.points
            ],
        }
    )


__all__ = ["api_paper_bankroll"]
