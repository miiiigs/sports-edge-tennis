"""Read-only dashboard and startup diagnostics for missing Pinnacle references."""

from __future__ import annotations

from collections import Counter

import psycopg
from fastapi import Query
from fastapi.responses import JSONResponse

from sports_edge_tennis.dashboard.app import DEMO_MODE, _connect
from sports_edge_tennis.live.sharp_blockers import (
    SharpReferenceBlocker,
    format_sharp_blocker,
    load_missing_sharp_reference_blockers,
)


def load_recent_live_missing_sharp_run_ids(
    conn: psycopg.Connection,
    *,
    limit: int = 5,
) -> tuple[int, ...]:
    """Return recent production-live runs containing canonical sharp blockers."""
    rows = conn.execute(
        """
        SELECT d.prediction_run_id
        FROM decisions d
        JOIN prediction_runs pr
          ON pr.prediction_run_id = d.prediction_run_id
        JOIN manager_consensus_results mcr
          ON mcr.manager_consensus_result_id = d.manager_consensus_result_id
        WHERE pr.prediction_contract_version = 'predictive_agent_result_v1'
          AND pr.run_status = 'COMPLETED'
          AND pr.prediction_finalized_at IS NOT NULL
          AND pr.experiment_id IS NULL
          AND d.signal_type = 'TYPE_A_MODEL_EDGE'
          AND d.market_type = 'MATCH_WINNER'
          AND d.decision_status = 'NO_BET'
          AND d.reason_code = 'MISSING_SHARP_REFERENCE'
          AND mcr.manager_status = 'CONSENSUS'
          AND mcr.selected_side IN (1, 2)
          AND mcr.consensus_probability IS NOT NULL
        GROUP BY d.prediction_run_id
        ORDER BY max(d.as_of) DESC, d.prediction_run_id DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return tuple(int(row[0]) for row in rows)


def _blocker_payload(blocker: SharpReferenceBlocker) -> dict[str, object]:
    return {
        "prediction_run_id": blocker.prediction_run_id,
        "decision_id": blocker.decision_id,
        "match_id": blocker.match_id,
        "classification": blocker.classification,
        "player_1": blocker.player_1_name,
        "player_2": blocker.player_2_name,
        "tournament": blocker.tournament_name,
        "match_start_at": blocker.match_start_at.isoformat(),
        "selected_side": blocker.selected_side,
        "consensus_probability": str(blocker.consensus_probability),
        "pinnapi_mapping_count": blocker.pinnapi_mapping_count,
        "pinnapi_provider_event_id": blocker.pinnapi_provider_event_id,
        "sharp_quotes_at_decision": blocker.sharp_quotes_at_decision,
        "sharp_player_1_quotes_at_decision": blocker.sharp_player_1_quotes_at_decision,
        "sharp_player_2_quotes_at_decision": blocker.sharp_player_2_quotes_at_decision,
        "sharp_complete_pairs_at_decision": blocker.sharp_complete_pairs_at_decision,
        "sharp_quotes_total": blocker.sharp_quotes_total,
        "sharp_player_1_quotes_total": blocker.sharp_player_1_quotes_total,
        "sharp_player_2_quotes_total": blocker.sharp_player_2_quotes_total,
        "sharp_complete_pairs_total": blocker.sharp_complete_pairs_total,
        "latest_sharp_observed_at": (
            blocker.latest_sharp_observed_at.isoformat()
            if blocker.latest_sharp_observed_at is not None
            else None
        ),
    }


def api_sharp_blockers(limit: int = Query(default=5, ge=1, le=20)):
    """Expose recent production-live sharp blockers without mutating state."""
    if DEMO_MODE:
        return JSONResponse(
            {
                "initialized": False,
                "reason": "sharp blocker diagnostics require persisted production evidence",
            }
        )

    conn = _connect()
    if conn is None:
        return JSONResponse({"initialized": False, "reason": "database unavailable"}, status_code=503)
    try:
        run_ids = load_recent_live_missing_sharp_run_ids(conn, limit=limit)
        runs = []
        for prediction_run_id in run_ids:
            blockers = load_missing_sharp_reference_blockers(
                conn,
                prediction_run_id=prediction_run_id,
            )
            runs.append(
                {
                    "prediction_run_id": prediction_run_id,
                    "blocker_count": len(blockers),
                    "blockers": [_blocker_payload(blocker) for blocker in blockers],
                }
            )
    finally:
        conn.close()

    return JSONResponse(
        {
            "initialized": True,
            "runs": runs,
            "safety": (
                "Read-only diagnostics only. No reconciliation, pricing, Manager, Market, "
                "Veto, staking, publication, settlement, or shadow-strategy policy is changed."
            ),
        }
    )


def log_recent_sharp_blockers_on_startup(*, limit: int = 5) -> None:
    """Emit bounded historical blocker evidence once per dashboard process startup.

    This deliberately does not raise. Observability must never prevent the dashboard
    from starting when the database is unavailable or a diagnostic query fails.
    """
    if DEMO_MODE:
        return
    conn = _connect()
    if conn is None:
        print("SHARP_BLOCKER_HISTORY_UNAVAILABLE database_unavailable", flush=True)
        return
    try:
        run_ids = load_recent_live_missing_sharp_run_ids(conn, limit=limit)
        blockers: list[SharpReferenceBlocker] = []
        for prediction_run_id in run_ids:
            blockers.extend(
                load_missing_sharp_reference_blockers(
                    conn,
                    prediction_run_id=prediction_run_id,
                )
            )
        for blocker in blockers:
            print("SHARP_BLOCKER_HISTORY " + format_sharp_blocker(blocker), flush=True)
        counts = Counter(blocker.classification for blocker in blockers)
        classification_text = ",".join(
            f"{classification}={count}"
            for classification, count in sorted(counts.items())
        ) or "none"
        print(
            "SHARP_BLOCKER_HISTORY_SUMMARY "
            f"runs={len(run_ids)} blockers={len(blockers)} classifications={classification_text}",
            flush=True,
        )
    except Exception as exc:  # pragma: no cover - production safety boundary
        print(
            "SHARP_BLOCKER_HISTORY_FAILED "
            f"error={type(exc).__name__}",
            flush=True,
        )
    finally:
        conn.close()


__all__ = [
    "api_sharp_blockers",
    "load_recent_live_missing_sharp_run_ids",
    "log_recent_sharp_blockers_on_startup",
]
