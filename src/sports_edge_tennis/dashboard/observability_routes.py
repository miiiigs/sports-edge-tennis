"""Read-only operational diagnostics endpoints.

These endpoints expose agent abstention reasons and aligned-pick result coverage.
They are intentionally separate from the client-facing Today/Model Picks surfaces
and never change any prediction, decision, settlement, or model behavior.
"""

from __future__ import annotations

from fastapi import Query
from fastapi.responses import HTMLResponse, JSONResponse

from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect
from sports_edge_tennis.dashboard.result_coverage import (
    load_terminal_fixtures_without_result,
)
from sports_edge_tennis.live.pending_result_diagnostics import (
    load_pending_result_diagnostics,
    report_json,
)
from sports_edge_tennis.performance.abstention_evidence import (
    AbstentionWindow,
    load_abstention_windows,
)
from sports_edge_tennis.performance.calibration_monitor import (
    load_calibration_health,
)

_DB_UNAVAILABLE = "database unavailable"


def _window_json(window: AbstentionWindow) -> dict[str, object]:
    return {
        "window": window.window,
        "window_start": window.window_start.isoformat(),
        "window_end": window.window_end.isoformat(),
        "agents": [
            {
                "agent_code": agent.agent_code,
                "evaluations": agent.evaluations,
                "predicted": agent.predicted,
                "abstained": agent.abstained,
                "predicted_rate": agent.predicted_rate,
                "abstained_rate": agent.abstained_rate,
                "unknown_reason_count": agent.unknown_reason_count,
                "reasons": [
                    {
                        "reason_code": reason.reason_code,
                        "count": reason.count,
                        "share": reason.share,
                    }
                    for reason in agent.reasons
                ],
            }
            for agent in window.agents
        ],
    }


def api_agent_abstention():
    if DEMO_MODE:
        return JSONResponse(
            {
                "initialized": False,
                "reason": "agent abstention diagnostics require persisted production evidence",
            }
        )
    conn = _connect()
    if conn is None:
        return JSONResponse(
            {"initialized": False, "reason": _DB_UNAVAILABLE}, status_code=503
        )
    try:
        windows = load_abstention_windows(conn)
    finally:
        conn.close()

    return JSONResponse(
        {
            "initialized": True,
            "scope": (
                "production predictive_agent_result_v1 runs; abstain_reason_code is "
                "persisted from migration 0017 and windowed by prediction as_of (UTC)"
            ),
            "unknown_reason_bucket": "UNKNOWN_NOT_PERSISTED",
            "windows": [_window_json(window) for window in windows],
        }
    )


def api_result_coverage(
    grace_hours: int = Query(default=12, ge=0, le=168),
    limit: int = Query(default=500, ge=1, le=2000),
):
    if DEMO_MODE:
        return JSONResponse(
            {
                "initialized": False,
                "reason": "result coverage diagnostics require persisted production evidence",
            }
        )
    conn = _connect()
    if conn is None:
        return JSONResponse(
            {"initialized": False, "reason": _DB_UNAVAILABLE}, status_code=503
        )
    try:
        report = load_terminal_fixtures_without_result(
            conn, grace_hours=grace_hours, limit=limit
        )
    finally:
        conn.close()

    return JSONResponse(
        {
            "initialized": True,
            "scope": (
                "aligned production Model Picks whose latest fixture status is terminal "
                "but which have no terminal match_result_observations row"
            ),
            "grace_hours": report.grace_hours,
            "generated_at": report.generated_at.isoformat(),
            "last_result_refresh_at": report.last_result_refresh_at.isoformat()
            if report.last_result_refresh_at
            else None,
            "stale_count": report.stale_count,
            "items": [
                {
                    "match_id": item.match_id,
                    "match_status": item.match_status,
                    "match_start_at": item.match_start_at.isoformat()
                    if item.match_start_at
                    else None,
                    "fixture_observed_at": item.fixture_observed_at.isoformat(),
                    "picked_at": item.picked_at.isoformat(),
                    "age_hours": item.age_hours,
                    "source_systems": list(item.source_systems),
                    "source_match_ids": list(item.source_match_ids),
                    "last_result_refresh_at": item.last_result_refresh_at.isoformat()
                    if item.last_result_refresh_at
                    else None,
                }
                for item in report.items
            ],
        }
    )


def api_pending_results(
    lookback_days: int = Query(default=60, ge=1, le=400),
    limit: int = Query(default=500, ge=1, le=2000),
):
    if DEMO_MODE:
        return JSONResponse(
            {
                "initialized": False,
                "reason": "pending result diagnostics require persisted production evidence",
            }
        )
    conn = _connect()
    if conn is None:
        return JSONResponse(
            {"initialized": False, "reason": _DB_UNAVAILABLE}, status_code=503
        )
    try:
        # Defence in depth: the diagnostics only SELECT, but the session is
        # forced read-only so no future change here can write production data.
        conn.execute("SET default_transaction_read_only = on")
        report = load_pending_result_diagnostics(
            conn, lookback_days=lookback_days, limit=limit
        )
    except Exception:
        return JSONResponse(
            {"initialized": False, "reason": "pending result diagnostics unavailable"},
            status_code=503,
        )
    finally:
        conn.close()

    payload = report_json(report)
    payload["initialized"] = True
    payload["scope"] = (
        "production Model Picks and settlement-eligible Type A BETs with no terminal "
        "match_result_observations row; one primary reason per match, read-only"
    )
    return JSONResponse(payload)


def _calibration_health_payload() -> tuple[dict | None, int]:
    if DEMO_MODE:
        return {"initialized": False, "reason": "calibration health requires persisted evidence"}, 200
    conn = _connect()
    if conn is None:
        return {"initialized": False, "reason": _DB_UNAVAILABLE}, 503
    try:
        report = load_calibration_health(conn)
    except Exception:
        return {"initialized": False, "reason": "calibration health unavailable"}, 503
    finally:
        conn.close()
    report["initialized"] = True
    report["scope"] = (
        "read-only aligned Model Pick calibration monitoring; production "
        "predictive_agent_result_v1 runs only; no automatic retraining or filtering"
    )
    return report, 200


def api_calibration_health():
    payload, status = _calibration_health_payload()
    return JSONResponse(payload, status_code=status)


def _html_escape(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _pct(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def calibration_diagnostics_page():
    payload, status = _calibration_health_payload()
    if status != 200 or not payload.get("initialized"):
        return HTMLResponse(
            f"<h1>Calibration diagnostics unavailable</h1><p>{_html_escape(payload.get('reason'))}</p>",
            status_code=status,
        )
    rows = []
    for window, data in payload["windows"].items():
        c = data["combined"]
        rows.append(
            "<tr>"
            f"<td>{_html_escape(window)}</td>"
            f"<td>{c['scored']}</td>"
            f"<td>{c['wins']}</td>"
            f"<td>{c['losses']}</td>"
            f"<td>{_pct(c['observed_hit_rate'])}</td>"
            f"<td>{_pct(c['average_displayed'])}</td>"
            f"<td>{_pct(c['calibration_gap'])}</td>"
            "</tr>"
        )
    hc = []
    for label, row in payload["windows"]["ALL_CLEAN_PRODUCTION"]["high_confidence"].items():
        hc.append(
            f"<tr><td>{_html_escape(label)}</td><td>{row['scored']}</td>"
            f"<td>{_pct(row['observed_hit_rate'])}</td>"
            f"<td>{_pct(row['average_displayed'])}</td>"
            f"<td>{_pct(row['calibration_gap'])}</td>"
            f"<td>{_html_escape(row['health'])}</td></tr>"
        )
    flags = ", ".join(payload.get("flags", [])) or "NONE"
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Calibration diagnostics</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;color:#222}}table{{border-collapse:collapse;margin:1rem 0}}
th,td{{border:1px solid #ccc;padding:0.35rem 0.6rem;text-align:left}}th{{background:#f2f2f2}}</style></head>
<body>
<h1>Calibration diagnostics (internal)</h1>
<p>Read-only aligned Model Pick calibration monitoring. No automatic retraining or filtering.
Reference date {_html_escape(payload['reference_date'])}.</p>
<h2>Windows</h2>
<table><thead><tr><th>Window</th><th>Scored</th><th>W</th><th>L</th><th>Observed</th><th>Avg displayed</th><th>Gap</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>High confidence (all clean)</h2>
<table><thead><tr><th>Band</th><th>Scored</th><th>Observed</th><th>Avg displayed</th><th>Gap</th><th>Health</th></tr></thead>
<tbody>{''.join(hc)}</tbody></table>
<h2>Flags</h2><p>{_html_escape(flags)}</p>
</body></html>"""
    return HTMLResponse(body)


__all__ = [
    "api_agent_abstention",
    "api_calibration_health",
    "api_pending_results",
    "api_result_coverage",
    "calibration_diagnostics_page",
]
