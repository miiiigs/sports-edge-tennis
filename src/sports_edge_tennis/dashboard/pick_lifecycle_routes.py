"""Read-only API routes for the Tennis pick lifecycle ledger."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import Query
from fastapi.responses import JSONResponse

from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect
from sports_edge_tennis.dashboard.pick_lifecycle import load_pick_lifecycle


def _parse_date(raw: str | None) -> date | None:
    if raw is None:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def api_pick_lifecycle(date: str | None = Query(default=None)):
    slate_date = _parse_date(date)
    if slate_date is None:
        return JSONResponse({"error": "invalid date"}, status_code=400)
    if DEMO_MODE:
        return JSONResponse({"date": slate_date.isoformat(), "picks": []})

    conn = _connect()
    if conn is None:
        return JSONResponse({"error": "pick lifecycle unavailable"}, status_code=503)
    try:
        rows = load_pick_lifecycle(conn, slate_date=slate_date)
    except Exception:
        return JSONResponse({"error": "pick lifecycle unavailable"}, status_code=503)
    finally:
        conn.close()

    return JSONResponse(
        {
            "date": slate_date.isoformat(),
            "picks": [
                {
                    "match_id": row.match_id,
                    "match": f"{row.player_1} vs {row.player_2}",
                    "tournament": row.tournament,
                    "competition_tier": row.competition_tier,
                    "scheduled_start": row.scheduled_start.isoformat() if row.scheduled_start else None,
                    "first_shown_at": row.first_pick_at.isoformat(),
                    "first_pick": row.first_selected_player,
                    "first_probability": str(row.first_probability) if row.first_probability is not None else None,
                    "latest_model_at": row.latest_model_at.isoformat(),
                    "latest_model_status": row.latest_manager_status,
                    "latest_pick": row.latest_selected_player,
                    "latest_probability": str(row.latest_probability) if row.latest_probability is not None else None,
                    "model_pick_still_active": row.model_pick_still_active,
                    "model_pick_changed": row.model_pick_changed,
                    "active_agents": row.latest_active_agent_count,
                    "abstaining_agents": row.latest_abstaining_agent_count,
                    "latest_market_status": row.latest_market_status,
                    "latest_market_reason": row.latest_market_reason,
                    "latest_market_price": str(row.latest_market_price) if row.latest_market_price is not None else None,
                    "latest_bookmaker": row.latest_bookmaker,
                    "latest_edge_pct": str(row.latest_edge_pct) if row.latest_edge_pct is not None else None,
                    "lifecycle_state": row.latest_lifecycle_state,
                }
                for row in rows
            ],
        }
    )


__all__ = ["api_pick_lifecycle"]
