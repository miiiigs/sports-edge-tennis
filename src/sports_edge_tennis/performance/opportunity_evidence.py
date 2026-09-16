"""Read-only lifecycle and entry-timing evidence for Tennis opportunities.

Hourly prediction runs are immutable point-in-time snapshots, not independent sporting bets.
This module collapses those snapshots to one lifecycle per match and measures the first
published Type A BET against the point-in-time scheduled start that was visible when the
opportunity was published.

Nothing in this module changes prediction, Manager, Market, Veto, staking, provider, or
settlement behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg


@dataclass(frozen=True)
class LifecycleSummary:
    completed_runs: int
    unique_targets: int
    ever_consensus: int
    ever_bet: int
    latest_consensus: int
    latest_no_bet: int
    latest_unresolved: int


@dataclass(frozen=True)
class MatchLifecycleEvidence:
    match_id: int
    first_target_at: datetime
    first_consensus_at: datetime | None
    first_consensus_side: int | None
    first_consensus_probability: Decimal | None
    first_bet_at: datetime | None
    first_bet_price: Decimal | None
    first_bet_edge_pct: Decimal | None
    first_bet_min_price: Decimal | None
    latest_manager_at: datetime | None
    latest_manager_status: str | None
    latest_manager_side: int | None
    latest_manager_probability: Decimal | None
    latest_market_at: datetime | None
    latest_market_status: str | None
    latest_market_reason: str | None
    latest_market_price: Decimal | None


@dataclass(frozen=True)
class EntryTimingBucket:
    bucket: str
    opportunities: int
    settled: int
    wins: int
    losses: int
    void_or_cancelled: int
    average_price: Decimal | None
    net_units: Decimal
    roi: Decimal | None


def _safe_decimal_ratio(numerator: Decimal, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return numerator / Decimal(denominator)


def load_lifecycle_summary(conn: psycopg.Connection) -> LifecycleSummary:
    """Return mutually interpretable unique-match lifecycle counts.

    ``ever_consensus`` and ``ever_bet`` are milestones, while ``latest_*`` is an exclusive
    current-state partition across targeted matches with Manager evidence.  They are not
    presented as one additive funnel.
    """
    row = conn.execute(
        """
        WITH completed_runs AS (
            SELECT prediction_run_id, as_of
            FROM prediction_runs
            WHERE prediction_contract_version = 'predictive_agent_result_v1'
              AND run_status = 'COMPLETED'
              AND prediction_finalized_at IS NOT NULL
        ),
        targets AS (
            SELECT DISTINCT t.match_id
            FROM prediction_run_targets t
            JOIN completed_runs pr ON pr.prediction_run_id = t.prediction_run_id
        ),
        ever_consensus AS (
            SELECT DISTINCT m.match_id
            FROM manager_consensus_results m
            JOIN completed_runs pr ON pr.prediction_run_id = m.prediction_run_id
            WHERE m.manager_status = 'CONSENSUS'
        ),
        ever_bet AS (
            SELECT DISTINCT d.match_id
            FROM decisions d
            JOIN completed_runs pr ON pr.prediction_run_id = d.prediction_run_id
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.market_type = 'MATCH_WINNER'
              AND d.decision_status = 'BET'
        ),
        latest_manager AS (
            SELECT DISTINCT ON (m.match_id)
                m.match_id,
                m.manager_status::text AS manager_status
            FROM manager_consensus_results m
            JOIN completed_runs pr ON pr.prediction_run_id = m.prediction_run_id
            ORDER BY m.match_id, m.as_of DESC, m.manager_consensus_result_id DESC
        )
        SELECT
            (SELECT count(*) FROM completed_runs),
            (SELECT count(*) FROM targets),
            (SELECT count(*) FROM ever_consensus),
            (SELECT count(*) FROM ever_bet),
            count(*) FILTER (WHERE lm.manager_status = 'CONSENSUS'),
            count(*) FILTER (WHERE lm.manager_status = 'NO_BET'),
            count(*) FILTER (WHERE lm.match_id IS NULL)
        FROM targets t
        LEFT JOIN latest_manager lm ON lm.match_id = t.match_id
        """
    ).fetchone()
    return LifecycleSummary(
        completed_runs=int(row[0] or 0),
        unique_targets=int(row[1] or 0),
        ever_consensus=int(row[2] or 0),
        ever_bet=int(row[3] or 0),
        latest_consensus=int(row[4] or 0),
        latest_no_bet=int(row[5] or 0),
        latest_unresolved=int(row[6] or 0),
    )


def load_match_lifecycles(
    conn: psycopg.Connection,
    *,
    limit: int = 200,
) -> tuple[MatchLifecycleEvidence, ...]:
    """Collapse hourly snapshots to one first/current lifecycle row per sporting match."""
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")

    rows = conn.execute(
        """
        WITH completed_runs AS (
            SELECT prediction_run_id, as_of
            FROM prediction_runs
            WHERE prediction_contract_version = 'predictive_agent_result_v1'
              AND run_status = 'COMPLETED'
              AND prediction_finalized_at IS NOT NULL
        ),
        first_target AS (
            SELECT DISTINCT ON (t.match_id)
                t.match_id,
                pr.as_of AS first_target_at
            FROM prediction_run_targets t
            JOIN completed_runs pr ON pr.prediction_run_id = t.prediction_run_id
            ORDER BY t.match_id, pr.as_of, t.prediction_run_id
        ),
        first_consensus AS (
            SELECT DISTINCT ON (m.match_id)
                m.match_id,
                m.as_of AS first_consensus_at,
                m.selected_side AS first_consensus_side,
                m.consensus_probability AS first_consensus_probability
            FROM manager_consensus_results m
            JOIN completed_runs pr ON pr.prediction_run_id = m.prediction_run_id
            WHERE m.manager_status = 'CONSENSUS'
            ORDER BY m.match_id, m.as_of, m.manager_consensus_result_id
        ),
        latest_manager AS (
            SELECT DISTINCT ON (m.match_id)
                m.match_id,
                m.as_of AS latest_manager_at,
                m.manager_status::text AS latest_manager_status,
                m.selected_side AS latest_manager_side,
                m.consensus_probability AS latest_manager_probability
            FROM manager_consensus_results m
            JOIN completed_runs pr ON pr.prediction_run_id = m.prediction_run_id
            ORDER BY m.match_id, m.as_of DESC, m.manager_consensus_result_id DESC
        ),
        first_bet AS (
            SELECT DISTINCT ON (d.match_id)
                d.match_id,
                COALESCE(d.published_at, d.as_of) AS first_bet_at,
                d.market_decimal_price AS first_bet_price,
                d.edge_pct AS first_bet_edge_pct,
                d.min_acceptable_odds AS first_bet_min_price
            FROM decisions d
            JOIN completed_runs pr ON pr.prediction_run_id = d.prediction_run_id
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.market_type = 'MATCH_WINNER'
              AND d.decision_status = 'BET'
            ORDER BY d.match_id, COALESCE(d.published_at, d.as_of), d.decision_id
        ),
        latest_market AS (
            SELECT DISTINCT ON (d.match_id)
                d.match_id,
                d.as_of AS latest_market_at,
                d.decision_status::text AS latest_market_status,
                d.reason_code::text AS latest_market_reason,
                d.market_decimal_price AS latest_market_price
            FROM decisions d
            JOIN completed_runs pr ON pr.prediction_run_id = d.prediction_run_id
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.market_type = 'MATCH_WINNER'
            ORDER BY d.match_id, d.as_of DESC, d.decision_id DESC
        )
        SELECT
            ft.match_id,
            ft.first_target_at,
            fc.first_consensus_at,
            fc.first_consensus_side,
            fc.first_consensus_probability,
            fb.first_bet_at,
            fb.first_bet_price,
            fb.first_bet_edge_pct,
            fb.first_bet_min_price,
            lm.latest_manager_at,
            lm.latest_manager_status,
            lm.latest_manager_side,
            lm.latest_manager_probability,
            lmk.latest_market_at,
            lmk.latest_market_status,
            lmk.latest_market_reason,
            lmk.latest_market_price
        FROM first_target ft
        LEFT JOIN first_consensus fc ON fc.match_id = ft.match_id
        LEFT JOIN first_bet fb ON fb.match_id = ft.match_id
        LEFT JOIN latest_manager lm ON lm.match_id = ft.match_id
        LEFT JOIN latest_market lmk ON lmk.match_id = ft.match_id
        ORDER BY ft.first_target_at DESC, ft.match_id DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()

    return tuple(
        MatchLifecycleEvidence(
            match_id=int(row[0]),
            first_target_at=row[1],
            first_consensus_at=row[2],
            first_consensus_side=row[3],
            first_consensus_probability=row[4],
            first_bet_at=row[5],
            first_bet_price=row[6],
            first_bet_edge_pct=row[7],
            first_bet_min_price=row[8],
            latest_manager_at=row[9],
            latest_manager_status=row[10],
            latest_manager_side=row[11],
            latest_manager_probability=row[12],
            latest_market_at=row[13],
            latest_market_status=row[14],
            latest_market_reason=row[15],
            latest_market_price=row[16],
        )
        for row in rows
    )


def load_entry_timing_buckets(conn: psycopg.Connection) -> tuple[EntryTimingBucket, ...]:
    """Score first Type A BETs by time-to-start using point-in-time schedule evidence.

    The scheduled start is selected from the newest match version that was already visible
    at the first publication timestamp.  Later reschedules therefore cannot move an old
    opportunity into a more flattering entry bucket.
    """
    rows = conn.execute(
        """
        WITH first_bet AS (
            SELECT DISTINCT ON (d.match_id)
                d.decision_id,
                d.match_id,
                COALESCE(d.published_at, d.as_of) AS bet_at,
                d.market_decimal_price
            FROM decisions d
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.market_type = 'MATCH_WINNER'
              AND d.decision_status = 'BET'
            ORDER BY d.match_id, COALESCE(d.published_at, d.as_of), d.decision_id
        ),
        with_start AS (
            SELECT
                fb.*,
                pit.match_start_at,
                EXTRACT(EPOCH FROM (pit.match_start_at - fb.bet_at)) / 60.0 AS minutes_to_start
            FROM first_bet fb
            LEFT JOIN LATERAL (
                SELECT mv.match_start_at
                FROM match_versions mv
                WHERE mv.match_id = fb.match_id
                  AND mv.match_start_at IS NOT NULL
                  AND mv.as_of <= fb.bet_at
                  AND mv.source_observed_at <= fb.bet_at
                ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
                LIMIT 1
            ) pit ON TRUE
        ),
        classified AS (
            SELECT
                ws.*,
                ds.settlement_status::text AS settlement_status,
                CASE
                    WHEN ws.minutes_to_start IS NULL THEN 'UNKNOWN'
                    WHEN ws.minutes_to_start < 0 THEN 'AFTER_START'
                    WHEN ws.minutes_to_start < 30 THEN '<30m'
                    WHEN ws.minutes_to_start < 120 THEN '30m-2h'
                    WHEN ws.minutes_to_start < 360 THEN '2-6h'
                    WHEN ws.minutes_to_start < 720 THEN '6-12h'
                    WHEN ws.minutes_to_start < 1440 THEN '12-24h'
                    ELSE '24h+'
                END AS timing_bucket
            FROM with_start ws
            LEFT JOIN decision_settlements ds ON ds.decision_id = ws.decision_id
        ),
        bucketed AS (
            SELECT
                timing_bucket,
                count(*) AS opportunities,
                count(*) FILTER (
                    WHERE settlement_status IN ('WON','LOST','VOID','PUSH','CANCELLED')
                ) AS settled,
                count(*) FILTER (WHERE settlement_status = 'WON') AS wins,
                count(*) FILTER (WHERE settlement_status = 'LOST') AS losses,
                count(*) FILTER (
                    WHERE settlement_status IN ('VOID','PUSH','CANCELLED')
                ) AS void_or_cancelled,
                avg(market_decimal_price) AS average_price,
                sum(
                    CASE
                        WHEN settlement_status = 'WON' THEN market_decimal_price - 1
                        WHEN settlement_status = 'LOST' THEN -1
                        ELSE 0
                    END
                ) AS net_units
            FROM classified
            GROUP BY timing_bucket
        )
        SELECT
            timing_bucket,
            opportunities,
            settled,
            wins,
            losses,
            void_or_cancelled,
            average_price,
            COALESCE(net_units, 0),
            wins + losses AS risked_results
        FROM bucketed
        ORDER BY CASE timing_bucket
            WHEN '24h+' THEN 1
            WHEN '12-24h' THEN 2
            WHEN '6-12h' THEN 3
            WHEN '2-6h' THEN 4
            WHEN '30m-2h' THEN 5
            WHEN '<30m' THEN 6
            WHEN 'AFTER_START' THEN 7
            ELSE 8
        END
        """
    ).fetchall()

    result: list[EntryTimingBucket] = []
    for row in rows:
        net_units = row[7] if isinstance(row[7], Decimal) else Decimal(str(row[7] or 0))
        result.append(
            EntryTimingBucket(
                bucket=str(row[0]),
                opportunities=int(row[1]),
                settled=int(row[2]),
                wins=int(row[3]),
                losses=int(row[4]),
                void_or_cancelled=int(row[5]),
                average_price=row[6],
                net_units=net_units,
                roi=_safe_decimal_ratio(net_units, int(row[8])),
            )
        )
    return tuple(result)


__all__ = [
    "LifecycleSummary",
    "MatchLifecycleEvidence",
    "EntryTimingBucket",
    "load_lifecycle_summary",
    "load_match_lifecycles",
    "load_entry_timing_buckets",
]
