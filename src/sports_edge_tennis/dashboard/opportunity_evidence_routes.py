"""Read-only API for unique-match lifecycle and first-entry timing evidence."""

from __future__ import annotations

from decimal import Decimal

from fastapi import Query
from fastapi.responses import JSONResponse

from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect
from sports_edge_tennis.performance.opportunity_evidence import (
    load_entry_timing_buckets,
    load_lifecycle_summary,
    load_match_lifecycles,
)


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def api_opportunity_evidence(limit: int = Query(default=100, ge=1, le=500)):
    if DEMO_MODE:
        return JSONResponse(
            {
                "initialized": False,
                "reason": "opportunity evidence requires persisted production evidence",
            }
        )

    conn = _connect()
    if conn is None:
        return JSONResponse({"initialized": False, "reason": "database unavailable"}, status_code=503)
    try:
        summary = load_lifecycle_summary(conn)
        lifecycles = load_match_lifecycles(conn, limit=limit)
        timing = load_entry_timing_buckets(conn)
    finally:
        conn.close()

    return JSONResponse(
        {
            "initialized": True,
            "lifecycle_summary": {
                "completed_prediction_runs": summary.completed_runs,
                "unique_matches_targeted": summary.unique_targets,
                "ever_reached_consensus": summary.ever_consensus,
                "ever_had_first_type_a_bet": summary.ever_bet,
                "latest_state": {
                    "consensus": summary.latest_consensus,
                    "no_bet": summary.latest_no_bet,
                    "unresolved": summary.latest_unresolved,
                    "definition": "exclusive latest Manager state per unique targeted match",
                },
                "interpretation": (
                    "Completed runs are point-in-time checkpoints. Ever-consensus and ever-bet are "
                    "lifecycle milestones and must not be added to latest-state counts as one funnel."
                ),
            },
            "entry_timing": [
                {
                    "bucket": row.bucket,
                    "opportunities": row.opportunities,
                    "settled": row.settled,
                    "wins": row.wins,
                    "losses": row.losses,
                    "void_or_cancelled": row.void_or_cancelled,
                    "average_price": _decimal(row.average_price),
                    "net_units": _decimal(row.net_units),
                    "roi": _decimal(row.roi),
                    "sample_warning": (
                        "INSUFFICIENT SAMPLE" if row.wins + row.losses < 30 else None
                    ),
                }
                for row in timing
            ],
            "matches": [
                {
                    "match_id": row.match_id,
                    "first_target_at": row.first_target_at.isoformat(),
                    "first_consensus": {
                        "at": row.first_consensus_at.isoformat() if row.first_consensus_at else None,
                        "side": row.first_consensus_side,
                        "probability": _decimal(row.first_consensus_probability),
                    },
                    "first_bet": {
                        "at": row.first_bet_at.isoformat() if row.first_bet_at else None,
                        "price": _decimal(row.first_bet_price),
                        "edge_pct": _decimal(row.first_bet_edge_pct),
                        "min_acceptable_price": _decimal(row.first_bet_min_price),
                        "definition": "first Type A BET per sporting match; published_at when present, otherwise legacy as_of",
                    },
                    "latest_model": {
                        "at": row.latest_manager_at.isoformat() if row.latest_manager_at else None,
                        "status": row.latest_manager_status,
                        "side": row.latest_manager_side,
                        "probability": _decimal(row.latest_manager_probability),
                    },
                    "latest_market": {
                        "at": row.latest_market_at.isoformat() if row.latest_market_at else None,
                        "status": row.latest_market_status,
                        "reason": row.latest_market_reason,
                        "price": _decimal(row.latest_market_price),
                    },
                }
                for row in lifecycles
            ],
            "safety": (
                "Read-only evidence only. No betting rule, timing rule, Manager threshold, "
                "market threshold, Veto, staking, or provider behaviour is changed."
            ),
        }
    )


__all__ = ["api_opportunity_evidence"]
