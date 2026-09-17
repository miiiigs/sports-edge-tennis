"""Live-only selector for the prospective shadow strategy.

The core shadow evaluator can replay any finalized predictive run explicitly. This
adapter is the production boundary: scheduled live runs have no experiment_id,
while research/backtest runs are excluded from the live shadow ledger.
"""

from __future__ import annotations

from datetime import datetime

import psycopg

from sports_edge_tennis.live.sharp_blockers import (
    format_sharp_blocker,
    load_missing_sharp_reference_blockers,
)
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
    """Consume prospective completed non-experiment prediction runs only.

    Before consuming each new live run, emit bounded read-only diagnostics for any
    canonical Manager consensus that the production Market rejected specifically
    because no Pinnacle sharp reference existed at its decision boundary. This is
    observational only and executes inside the already fail-isolated shadow lane.
    """
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

    outcomes: list[ShadowRunOutcome] = []
    for row in rows:
        prediction_run_id = int(row[0])
        for blocker in load_missing_sharp_reference_blockers(
            conn,
            prediction_run_id=prediction_run_id,
        ):
            print(format_sharp_blocker(blocker), flush=True)
        outcomes.append(
            observe_shadow_run(
                conn,
                prediction_run_id=prediction_run_id,
                observed_at=observed_at,
                sharp_reference=sharp_reference,
            )
        )
    return tuple(outcomes)


__all__ = ["observe_pending_live_shadow_runs"]
