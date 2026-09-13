"""Aggregate prospective evidence routes layered onto the existing dashboard app.

This module intentionally exposes aggregate metrics only. Raw decision/audit evidence
remains in PostgreSQL and should be surfaced only after owner authentication is enabled.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from sports_edge_tennis.dashboard.app import _db_dsn, app, templates
from sports_edge_tennis.performance import (
    ProspectivePerformanceSummary,
    load_prospective_performance,
)


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _summary_payload(summary: ProspectivePerformanceSummary) -> dict[str, object]:
    return {
        "initialized": summary.initialized,
        "decision_counts": {
            "total": summary.total_decisions,
            "bet": summary.bet_decisions,
            "no_bet": summary.no_bet_decisions,
            "vetoed": summary.vetoed_decisions,
        },
        "settlement": {
            "settled_bets": summary.settled_bets,
            "pending_bets": summary.pending_bets,
            "wins": summary.wins,
            "losses": summary.losses,
            "voids": summary.voids,
            "cancelled": summary.cancelled,
            "one_unit_profit": _decimal_text(summary.one_unit_profit),
            "roi": _decimal_text(summary.roi),
            "hit_rate": summary.hit_rate,
        },
        "calibration": {
            "sample": summary.calibration_sample,
            "brier_score": summary.brier_score,
            "log_loss": summary.log_loss,
            "ece": summary.ece,
            "bins": [
                {
                    "lower": bin_.lower,
                    "upper": bin_.upper,
                    "sample": bin_.sample,
                    "mean_probability": bin_.mean_probability,
                    "observed_rate": bin_.observed_rate,
                    "absolute_gap": bin_.absolute_gap,
                }
                for bin_ in summary.bins
            ],
        },
    }


def _load_summary() -> ProspectivePerformanceSummary:
    dsn = _db_dsn()
    if not dsn:
        return ProspectivePerformanceSummary(initialized=False)
    with psycopg.connect(dsn, autocommit=True) as conn:
        return load_prospective_performance(conn)


@app.get("/api/dashboard/prospective-performance")
def api_prospective_performance():
    try:
        summary = _load_summary()
    except (psycopg.Error, ValueError) as exc:
        return JSONResponse(
            {
                "detail": "Prospective performance evidence is temporarily unavailable",
                "error_type": type(exc).__name__,
            },
            status_code=503,
        )
    return _summary_payload(summary)


@app.get("/evidence", response_class=HTMLResponse)
def evidence_page(request: Request):
    try:
        summary = _load_summary()
        error = None
    except (psycopg.Error, ValueError) as exc:
        summary = ProspectivePerformanceSummary(initialized=False)
        error = type(exc).__name__
    return templates.TemplateResponse(
        request,
        "evidence.html",
        {
            "prospective": summary,
            "evidence_error": error,
        },
    )
