"""Read-only API for seasonal coverage and historical corpus inventory."""
from __future__ import annotations
from datetime import UTC, date, datetime, timedelta
from fastapi import Query
from fastapi.responses import JSONResponse
from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect
from sports_edge_tennis.dashboard.calendar_coverage import load_seasonal_coverage


def api_calendar_coverage(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
):
    if DEMO_MODE:
        return JSONResponse({"initialized": False, "reason": "demo has no persisted corpus"})
    now = datetime.now(UTC)
    first = start_date or (now.date() - timedelta(days=now.weekday()))
    last = end_date or (first + timedelta(days=49))
    if last < first or (last - first).days > 84:
        return JSONResponse({"initialized": False, "reason": "invalid window"}, status_code=422)
    conn = _connect()
    if conn is None:
        return JSONResponse({"initialized": False, "reason": "database unavailable"}, status_code=503)
    try:
        conn.execute("SET default_transaction_read_only = on")
        report = load_seasonal_coverage(conn, start_date=first, end_date=last, as_of=now)
    except Exception:
        return JSONResponse({"initialized": False, "reason": "coverage query unavailable"}, status_code=503)
    finally:
        conn.close()
    return JSONResponse({"initialized": True, **report})
