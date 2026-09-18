from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg


SOURCE_SYSTEM = "SPORTS_EDGE_TENNIS"
DATASET_NAME = "LIVE_BATCH_RUNTIME"


@dataclass(frozen=True)
class LiveOperationalHealth:
    status: str
    freshness_state: str
    completed_runs: int
    target_count: int
    latest_finalized_at: datetime | None
    error_count: int
    notes: str


def evaluate_live_operational_health(
    conn: psycopg.Connection,
    *,
    cycle_started_at: datetime,
    live_status: int,
) -> LiveOperationalHealth:
    if live_status != 0:
        return LiveOperationalHealth(
            status="ERROR",
            freshness_state="UNKNOWN",
            completed_runs=0,
            target_count=0,
            latest_finalized_at=None,
            error_count=1,
            notes=f"live_batch_status={live_status}",
        )

    row = conn.execute(
        """
        WITH cycle_runs AS (
            SELECT prediction_run_id, prediction_finalized_at
            FROM prediction_runs
            WHERE experiment_id IS NULL
              AND run_status = 'COMPLETED'
              AND started_at >= %s
        )
        SELECT
            count(DISTINCT cr.prediction_run_id)::INTEGER,
            count(prt.match_id)::INTEGER,
            max(cr.prediction_finalized_at)
        FROM cycle_runs cr
        LEFT JOIN prediction_run_targets prt
          ON prt.prediction_run_id = cr.prediction_run_id
        """,
        (cycle_started_at,),
    ).fetchone()
    if row is None:
        raise RuntimeError("live operational health query returned no row")

    completed_runs = int(row[0])
    target_count = int(row[1])
    latest_finalized_at = row[2]

    if completed_runs == 0:
        return LiveOperationalHealth(
            status="WARNING",
            freshness_state="UNKNOWN",
            completed_runs=0,
            target_count=0,
            latest_finalized_at=None,
            error_count=0,
            notes="live_batch_status=0 but no completed production run was found in cycle",
        )

    if target_count == 0:
        return LiveOperationalHealth(
            status="WARNING",
            freshness_state="FRESH",
            completed_runs=completed_runs,
            target_count=0,
            latest_finalized_at=latest_finalized_at,
            error_count=0,
            notes=f"completed_runs={completed_runs} target_count=0 zero_volume=1",
        )

    return LiveOperationalHealth(
        status="HEALTHY",
        freshness_state="FRESH",
        completed_runs=completed_runs,
        target_count=target_count,
        latest_finalized_at=latest_finalized_at,
        error_count=0,
        notes=(
            f"completed_runs={completed_runs} "
            f"target_count={target_count} zero_volume=0"
        ),
    )


def persist_live_operational_health(
    conn: psycopg.Connection,
    *,
    health: LiveOperationalHealth,
    checked_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO data_health_checks (
            source_system,
            dataset_name,
            checked_at,
            latest_observation_at,
            freshness_state,
            coverage_ratio,
            unmatched_count,
            error_count,
            status,
            notes
        )
        VALUES (%s, %s, %s, %s, %s, NULL, 0, %s, %s, %s)
        """,
        (
            SOURCE_SYSTEM,
            DATASET_NAME,
            checked_at,
            health.latest_finalized_at,
            health.freshness_state,
            health.error_count,
            health.status,
            health.notes,
        ),
    )
