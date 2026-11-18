"""Authenticated read-only per-day original Model Picks ATP/Challenger split."""
from __future__ import annotations
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import Query
from fastapi.responses import JSONResponse
from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect
from sports_edge_tennis.dashboard.tour_daily_model_picks import load_tour_daily


def api_tour_daily_model_picks(
    start_date:date|None=Query(default=None),
    end_date:date|None=Query(default=None),
):
    now=datetime.now(UTC)
    today=now.astimezone(ZoneInfo("Asia/Manila")).date()
    last=end_date or today
    first=start_date or (last-timedelta(days=13))
    if last<first or (last-first).days>119:
        return JSONResponse(
            {"initialized":False,"error":"invalid date range, max 120 days"},status_code=422
        )
    if DEMO_MODE:
        return JSONResponse({"initialized":False,"error":"no canonical source in demo mode"})
    conn=_connect()
    if conn is None:
        return JSONResponse({"initialized":False,"error":"database unavailable"},status_code=503)
    try:
        conn.execute("SET default_transaction_read_only=on")
        result=load_tour_daily(conn,start_day=first,end_day=last,as_of=now)
    except Exception:
        return JSONResponse({"initialized":False,"error":"tour daily report unavailable"},status_code=503)
    finally:
        conn.close()
    return JSONResponse({"initialized":True,**result})
