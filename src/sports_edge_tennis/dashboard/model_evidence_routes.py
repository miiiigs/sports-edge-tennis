"""Read-only dashboard endpoint for live model evidence diagnostics."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect
from sports_edge_tennis.performance.a1_diagnostic import load_a1_live_challenger_diagnostic
from sports_edge_tennis.performance.coverage_evidence import load_agent_coverage_slices
from sports_edge_tennis.performance.evidence import load_live_evidence_summary


def _percent(value: float | None) -> str | None:
    return None if value is None else f"{value * 100:.2f}%"


def api_model_evidence():
    if DEMO_MODE:
        return JSONResponse(
            {
                "initialized": False,
                "reason": "model evidence diagnostics require persisted production evidence",
            }
        )

    conn = _connect()
    if conn is None:
        return JSONResponse({"initialized": False, "reason": "database unavailable"}, status_code=503)
    try:
        summary = load_live_evidence_summary(conn)
        coverage_slices = load_agent_coverage_slices(conn)
        a1_challenger = load_a1_live_challenger_diagnostic(conn)
    finally:
        conn.close()

    return JSONResponse(
        {
            "initialized": summary.initialized,
            "latest_run": {
                "prediction_run_id": summary.latest_prediction_run_id,
                "targets": summary.latest_targets,
                "manager_consensus": summary.latest_manager_consensus,
                "manager_no_bet": summary.latest_manager_no_bet,
                "type_a_bets": summary.latest_type_a_bets,
            },
            "unique_first_bets": {
                "bets": summary.unique_first_bets,
                "settled": summary.unique_settled_bets,
                "wins": summary.unique_wins,
                "losses": summary.unique_losses,
                "void_or_cancelled": summary.unique_void_or_cancelled,
                "hit_rate": _percent(summary.unique_hit_rate),
                "definition": "first published Type A BET per sporting match",
            },
            "manager_reasons": [
                {
                    "status": row.status,
                    "reason_code": row.reason_code,
                    "count": row.count,
                }
                for row in summary.manager_reasons
            ],
            "market_reasons": [
                {
                    "status": row.status,
                    "reason_code": row.reason_code,
                    "count": row.count,
                }
                for row in summary.market_reasons
            ],
            "agents": [
                {
                    "agent_code": row.agent_code,
                    "unique_matches": row.unique_matches,
                    "predicted": row.predicted,
                    "abstained": row.abstained,
                    "scored": row.scored,
                    "correct": row.correct,
                    "accuracy": _percent(row.accuracy),
                }
                for row in summary.agents
            ],
            "coverage_slices": [
                {
                    "agent_code": row.agent_code,
                    "dimension": row.dimension,
                    "value": row.value,
                    "unique_matches": row.unique_matches,
                    "predicted": row.predicted,
                    "abstained": row.abstained,
                    "coverage_rate": _percent(row.coverage_rate),
                }
                for row in coverage_slices
            ],
            "a1_challenger_diagnostic": {
                "initialized": a1_challenger.initialized,
                "definition": (
                    "first production-live Agent 1 prediction per Challenger match; "
                    "experiment/backtest rows excluded"
                ),
                "unavailable_fields": list(a1_challenger.unavailable_fields),
                "slices": [
                    {
                        "dimension": row.dimension,
                        "value": row.value,
                        "sample": row.sample,
                        "scored": row.scored,
                        "correct": row.correct,
                        "accuracy": _percent(row.accuracy),
                        "brier_score": row.brier_score,
                        "mean_confidence": _percent(row.mean_confidence),
                    }
                    for row in a1_challenger.slices
                ],
            },
            "agent3_shadow": [
                {
                    "cohort": row.cohort,
                    "sample": row.sample,
                    "scored": row.scored,
                    "correct": row.correct,
                    "accuracy": _percent(row.accuracy),
                }
                for row in summary.agent3_shadow
            ],
            "interpretation": (
                "Read-only production-live evidence. Coverage and Agent 1 slices use fixture "
                "metadata visible at the first prediction timestamp. Agent 3 shadow cohorts "
                "are descriptive and do not alter the canonical C1-C4 Manager contract."
            ),
        }
    )


__all__ = ["api_model_evidence"]
