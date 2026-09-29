"""Canonical aligned Model Pick population, shared by the dashboard read models.

This is the single definition of the current-rule aligned Model Picks population.
Both the Model Picks Results page (``model_pick_history``) and Today
(``read_model``) build their primary population from here, so the two surfaces can
never disagree about which sporting matches are Model Picks on a given local day.

The population is presentation/read-model selection only: it never creates,
edits, re-scores, or settles anything, and it never promotes a Wide-Spread
tracking case into a Value Bet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg

from sports_edge_tennis.dashboard.history_alignment import (
    ALIGNED_MIN_MODEL_PROBABILITY,
)
from sports_edge_tennis.performance.regimes import PRODUCTION_RUN_PREDICATE

MANAGER_CONSENSUS_PICK = "MANAGER_CONSENSUS"
A1_A2_WIDE_SPREAD_PICK = "A1_A2_WIDE_SPREAD"

#: Point-in-time match-version join: the latest version visible at ``p.picked_at``.
PIT_MATCH_START_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT mv0.match_start_at
        FROM match_versions mv0
        WHERE mv0.match_id = p.match_id
          AND mv0.as_of <= p.picked_at
          AND mv0.source_observed_at <= p.picked_at
        ORDER BY mv0.as_of DESC,
                 mv0.source_observed_at DESC,
                 mv0.match_version_id DESC
        LIMIT 1
    ) mv ON TRUE
"""

#: The one per-match local-day bucket shared by every aligned reader.
ALIGNED_PICK_DATE_FILTER = (
    "COALESCE((mv.match_start_at AT TIME ZONE %s::text)::date, "
    "(p.picked_at AT TIME ZONE %s::text)::date) = %s"
)


@dataclass(frozen=True)
class AlignedPick:
    """One qualifying aligned Model Pick for a local calendar day."""

    manager_consensus_result_id: int
    prediction_run_id: int
    match_id: int
    picked_at: datetime
    selected_side: int
    aligned_probability: Decimal
    aligned_pick_type: str
    match_start_at: datetime | None


def aligned_production_model_picks_cte() -> str:
    """Current-rule Sep-10 model-pick ledger over immutable live evidence.

    A qualifying aligned Model Pick is the earliest production evaluation per
    sporting match that satisfies either:

    1. Manager CONSENSUS with probability >= 60%; or
    2. A1+A2 same-side, A3 abstained, Manager rejected only because the A1/A2
       spread exceeded the abstention gate, with A1/A2 arithmetic mean >= 60%.

    The second case is tracking-only and never becomes a Value Bet.
    """
    floor = str(ALIGNED_MIN_MODEL_PROBABILITY)
    return f"""
        aligned_model_candidates AS (
            SELECT
                mcr.manager_consensus_result_id,
                mcr.prediction_run_id,
                mcr.match_id,
                mcr.as_of AS picked_at,
                mcr.selected_side,
                mcr.consensus_probability AS aligned_probability,
                'MANAGER_CONSENSUS'::text AS aligned_pick_type
            FROM manager_consensus_results mcr
            JOIN prediction_runs pr
              ON pr.prediction_run_id = mcr.prediction_run_id
            WHERE mcr.manager_status = 'CONSENSUS'
              AND mcr.selected_side IN (1, 2)
              AND mcr.consensus_probability >= {floor}
              AND {PRODUCTION_RUN_PREDICATE}

            UNION ALL

            SELECT
                mcr.manager_consensus_result_id,
                mcr.prediction_run_id,
                mcr.match_id,
                mcr.as_of AS picked_at,
                a1.selection_side AS selected_side,
                ((a1.win_probability + a2.win_probability) / 2.0) AS aligned_probability,
                'A1_A2_WIDE_SPREAD'::text AS aligned_pick_type
            FROM manager_consensus_results mcr
            JOIN prediction_runs pr
              ON pr.prediction_run_id = mcr.prediction_run_id
            JOIN predictions a1
              ON a1.prediction_run_id = mcr.prediction_run_id
             AND a1.match_id = mcr.match_id
             AND a1.agent_code = 'AGENT_1_SURFACE_ELO'
            JOIN predictions a2
              ON a2.prediction_run_id = mcr.prediction_run_id
             AND a2.match_id = mcr.match_id
             AND a2.agent_code = 'AGENT_2_HIERARCHICAL_MARKOV'
            JOIN predictions a3
              ON a3.prediction_run_id = mcr.prediction_run_id
             AND a3.match_id = mcr.match_id
             AND a3.agent_code = 'AGENT_3_INTRANSITIVITY'
            WHERE mcr.manager_status = 'NO_BET'
              AND mcr.reason_code = 'AGENT3_ABSTENTION_SPREAD_EXCEEDED'
              AND a1.prediction_status = 'PREDICTED'
              AND a2.prediction_status = 'PREDICTED'
              AND a3.prediction_status = 'ABSTAINED'
              AND a1.selection_side IN (1, 2)
              AND a2.selection_side = a1.selection_side
              AND a1.win_probability IS NOT NULL
              AND a2.win_probability IS NOT NULL
              AND ((a1.win_probability + a2.win_probability) / 2.0) >= {floor}
              AND {PRODUCTION_RUN_PREDICATE}
        ),
        aligned_model_picks AS (
            SELECT DISTINCT ON (match_id)
                manager_consensus_result_id,
                prediction_run_id,
                match_id,
                picked_at,
                selected_side,
                aligned_probability,
                aligned_pick_type
            FROM aligned_model_candidates
            ORDER BY
                match_id,
                picked_at,
                manager_consensus_result_id,
                aligned_pick_type
        )
    """


def load_aligned_day_picks(
    conn: psycopg.Connection,
    *,
    decision_date: date,
    timezone_name: str = "UTC",
) -> tuple[AlignedPick, ...]:
    """Qualifying aligned Model Picks whose local match day is ``decision_date``."""
    rows = conn.execute(
        f"""
        WITH {aligned_production_model_picks_cte()}
        SELECT
            p.manager_consensus_result_id,
            p.prediction_run_id,
            p.match_id,
            p.picked_at,
            p.selected_side,
            p.aligned_probability,
            p.aligned_pick_type,
            mv.match_start_at
        FROM aligned_model_picks p
        {PIT_MATCH_START_LATERAL}
        WHERE {ALIGNED_PICK_DATE_FILTER}
        ORDER BY p.picked_at DESC, p.manager_consensus_result_id DESC
        """,
        (timezone_name, timezone_name, decision_date),
    ).fetchall()

    picks: list[AlignedPick] = []
    for row in rows:
        if row[4] not in (1, 2):
            continue
        picks.append(
            AlignedPick(
                manager_consensus_result_id=int(row[0]),
                prediction_run_id=int(row[1]),
                match_id=int(row[2]),
                picked_at=row[3],
                selected_side=int(row[4]),
                aligned_probability=Decimal(row[5]),
                aligned_pick_type=str(row[6]),
                match_start_at=row[7],
            )
        )
    return tuple(picks)


__all__ = [
    "A1_A2_WIDE_SPREAD_PICK",
    "ALIGNED_PICK_DATE_FILTER",
    "MANAGER_CONSENSUS_PICK",
    "PIT_MATCH_START_LATERAL",
    "AlignedPick",
    "aligned_production_model_picks_cte",
    "load_aligned_day_picks",
]
