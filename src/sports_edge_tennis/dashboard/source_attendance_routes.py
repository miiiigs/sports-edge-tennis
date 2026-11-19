"""Authenticated read-only ATP/Ch fixture source-attendance evidence."""
from __future__ import annotations
from datetime import UTC,date,datetime,timedelta
from zoneinfo import ZoneInfo
from fastapi import Query
from fastapi.responses import JSONResponse
from sports_edge_tennis.dashboard.app import DEMO_MODE,_connect
from sports_edge_tennis.dashboard.source_attendance import load_source_attendance


def api_source_attendance(
    start_date:date|None=Query(default=None),
    end_date:date|None=Query(default=None),
):
    now=datetime.now(UTC)
    today=now.astimezone(ZoneInfo("Asia/Manila")).date()
    last=end_date or today
    first=start_date or (last-timedelta(days=13))
    if last<first or (last-first).days>59:
        return JSONResponse({"initialized":False,"error":"invalid range, max 60 days"},status_code=422)
    if DEMO_MODE:
        return JSONResponse({"initialized":False,"error":"no canonical source in demo mode"})
    conn=_connect()
    if conn is None:
        return JSONResponse({"initialized":False,"error":"database unavailable"},status_code=503)
    try:
        conn.execute("SET default_transaction_read_only=on")
        report=load_source_attendance(conn,first=first,last=last,as_of=now)
    except Exception:
        return JSONResponse({"initialized":False,"error":"source attendance unavailable"},status_code=503)
    finally:
        conn.close()
    return JSONResponse({"initialized":True,**report})
