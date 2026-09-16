"""Live-only selector for the prospective shadow strategy.

The core shadow evaluator can replay any finalized predictive run explicitly. This
adapter is the production boundary: scheduled live runs have no experiment_id,
while research/backtest runs are excluded from the live shadow ledger.
"""

from __future__ import annotations

from datetime import datetime

import psycopg

from sports_edge_tennis.market.contracts import SharpReferenceSpec
from sports_edge_tennis.performance.shadow_strategy import (
    STRATEGY_CODE,
    ShadowRunOutcome,
    activate_shadow_strategy,
    observe_shadow_run,
)


def observe_pending_live_shadow_runs(
    conn: psycopg.Connection,
    *,
    observed_at: datetime,
    sharp_reference: SharpReferenceSpec,
    limit: int = 100,
) -> tuple[ShadowRunOutcome, ...]:
    """Consume prospective completed non-experiment prediction runs only."""
    activation = activate_shadow_strategy(conn, activated_at=observed_at)
    rows = conn.execute(
        """
        SELECT pr.prediction_run_id
        FROM prediction_runs pr
        WHERE pr.prediction_contract_version = 'predictive_agent_result_v1'
          AND pr.run_status = 'COMPLETED'
          AND pr.prediction_finalized_at IS NOT NULL
          AND pr.experiment_id IS NULL
          AND pr.prediction_finalized_at >= %s
          AND pr.prediction_finalized_at <= %s
          AND NOT EXISTS (
              SELECT 1
              FROM shadow_strategy_runs sr
              WHERE sr.strategy_code = %s
                AND sr.prediction_run_id = pr.prediction_run_id
          )
        ORDER BY pr.prediction_finalized_at, pr.prediction_run_id
        LIMIT %s
        """,
        (activation, observed_at, STRATEGY_CODE, limit),
    ).fetchall()
    return tuple(
        observe_shadow_run(
            conn,
            prediction_run_id=int(row[0]),
            observed_at=observed_at,
            sharp_reference=sharp_reference,
        )
        for row in rows
    )


__all__ = ["observe_pending_live_shadow_runs"]
