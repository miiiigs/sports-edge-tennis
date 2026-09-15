"""Read-only model evidence diagnostics for Sports Edge Tennis.

This module intentionally does not alter prediction, Manager, market, Veto, staking,
or settlement behaviour.  It summarizes the persisted production evidence so historical
model metrics can be compared with live coverage and unique-match outcomes without
counting hourly reruns as independent sporting events.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class ReasonCount:
    status: str
    reason_code: str
    count: int


@dataclass(frozen=True)
class AgentEvidence:
    agent_code: str
    unique_matches: int
    predicted: int
    abstained: int
    scored: int
    correct: int
    accuracy: float | None


@dataclass(frozen=True)
class Agent3ShadowCohort:
    cohort: str
    sample: int
    scored: int
    correct: int
    accuracy: float | None


@dataclass(frozen=True)
class LiveEvidenceSummary:
    initialized: bool
    latest_prediction_run_id: int | None = None
    latest_targets: int = 0
    latest_manager_consensus: int = 0
    latest_manager_no_bet: int = 0
    latest_type_a_bets: int = 0
    unique_first_bets: int = 0
    unique_settled_bets: int = 0
    unique_wins: int = 0
    unique_losses: int = 0
    unique_void_or_cancelled: int = 0
    unique_hit_rate: float | None = None
    manager_reasons: tuple[ReasonCount, ...] = ()
    market_reasons: tuple[ReasonCount, ...] = ()
    agents: tuple[AgentEvidence, ...] = ()
    agent3_shadow: tuple[Agent3ShadowCohort, ...] = ()


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _latest_run_id(conn: psycopg.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT prediction_run_id
        FROM prediction_runs
        WHERE prediction_contract_version = 'predictive_agent_result_v1'
          AND run_status = 'COMPLETED'
          AND prediction_finalized_at IS NOT NULL
        ORDER BY ended_at DESC NULLS LAST, prediction_run_id DESC
        LIMIT 1
        """
    ).fetchone()
    return int(row[0]) if row else None


def _latest_funnel(conn: psycopg.Connection, prediction_run_id: int) -> tuple[int, int, int, int]:
    targets = conn.execute(
        "SELECT count(*) FROM prediction_run_targets WHERE prediction_run_id = %s",
        (prediction_run_id,),
    ).fetchone()[0]
    manager = conn.execute(
        """
        SELECT
            count(*) FILTER (WHERE manager_status = 'CONSENSUS'),
            count(*) FILTER (WHERE manager_status = 'NO_BET')
        FROM manager_consensus_results
        WHERE prediction_run_id = %s
        """,
        (prediction_run_id,),
    ).fetchone()
    bets = conn.execute(
        """
        SELECT count(*)
        FROM decisions
        WHERE prediction_run_id = %s
          AND signal_type = 'TYPE_A_MODEL_EDGE'
          AND market_type = 'MATCH_WINNER'
          AND decision_status = 'BET'
        """,
        (prediction_run_id,),
    ).fetchone()[0]
    return int(targets), int(manager[0]), int(manager[1]), int(bets)


def _reason_counts(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    source: str,
) -> tuple[ReasonCount, ...]:
    if source == "manager":
        rows = conn.execute(
            """
            SELECT manager_status::text, reason_code::text, count(*)
            FROM manager_consensus_results
            WHERE prediction_run_id = %s
            GROUP BY manager_status, reason_code
            ORDER BY count(*) DESC, manager_status, reason_code
            """,
            (prediction_run_id,),
        ).fetchall()
    elif source == "market":
        rows = conn.execute(
            """
            SELECT decision_status::text, reason_code::text, count(*)
            FROM decisions
            WHERE prediction_run_id = %s
              AND signal_type = 'TYPE_A_MODEL_EDGE'
              AND market_type = 'MATCH_WINNER'
            GROUP BY decision_status, reason_code
            ORDER BY count(*) DESC, decision_status, reason_code
            """,
            (prediction_run_id,),
        ).fetchall()
    else:  # pragma: no cover - internal contract guard
        raise ValueError(f"unsupported reason-count source: {source}")
    return tuple(ReasonCount(str(row[0]), str(row[1]), int(row[2])) for row in rows)


def _unique_first_bet_metrics(conn: psycopg.Connection) -> tuple[int, int, int, int, int]:
    row = conn.execute(
        """
        WITH first_bet AS (
            SELECT DISTINCT ON (d.match_id)
                d.decision_id,
                d.match_id
            FROM decisions d
            WHERE d.signal_type = 'TYPE_A_MODEL_EDGE'
              AND d.market_type = 'MATCH_WINNER'
              AND d.decision_status = 'BET'
            ORDER BY d.match_id, d.as_of, d.decision_id
        )
        SELECT
            count(*),
            count(*) FILTER (
                WHERE ds.settlement_status IN ('WON', 'LOST', 'VOID', 'PUSH', 'CANCELLED')
            ),
            count(*) FILTER (WHERE ds.settlement_status = 'WON'),
            count(*) FILTER (WHERE ds.settlement_status = 'LOST'),
            count(*) FILTER (WHERE ds.settlement_status IN ('VOID', 'CANCELLED', 'PUSH'))
        FROM first_bet fb
        LEFT JOIN decision_settlements ds ON ds.decision_id = fb.decision_id
        """
    ).fetchone()
    return tuple(int(value or 0) for value in row)  # type: ignore[return-value]


def _agent_evidence(conn: psycopg.Connection) -> tuple[AgentEvidence, ...]:
    rows = conn.execute(
        """
        WITH first_prediction AS (
            SELECT DISTINCT ON (p.match_id, p.agent_code)
                p.match_id,
                p.agent_code::text AS agent_code,
                p.prediction_status::text AS prediction_status,
                p.selection_side
            FROM predictions p
            JOIN prediction_runs pr ON pr.prediction_run_id = p.prediction_run_id
            WHERE pr.prediction_contract_version = 'predictive_agent_result_v1'
              AND pr.run_status = 'COMPLETED'
              AND pr.prediction_finalized_at IS NOT NULL
            ORDER BY p.match_id, p.agent_code, p.as_of, p.prediction_id
        ),
        latest_results AS (
            SELECT DISTINCT ON (match_id)
                match_id,
                winner_side
            FROM match_result_observations
            WHERE outcome_status = 'COMPLETED'
              AND winner_side IN (1, 2)
            ORDER BY match_id, as_of DESC, source_observed_at DESC,
                     match_result_observation_id DESC
        )
        SELECT
            fp.agent_code,
            count(*) AS unique_matches,
            count(*) FILTER (WHERE fp.prediction_status = 'PREDICTED') AS predicted,
            count(*) FILTER (WHERE fp.prediction_status = 'ABSTAINED') AS abstained,
            count(*) FILTER (
                WHERE fp.prediction_status = 'PREDICTED'
                  AND lr.winner_side IN (1, 2)
            ) AS scored,
            count(*) FILTER (
                WHERE fp.prediction_status = 'PREDICTED'
                  AND lr.winner_side IN (1, 2)
                  AND fp.selection_side = lr.winner_side
            ) AS correct
        FROM first_prediction fp
        LEFT JOIN latest_results lr ON lr.match_id = fp.match_id
        GROUP BY fp.agent_code
        ORDER BY fp.agent_code
        """
    ).fetchall()
    evidence: list[AgentEvidence] = []
    for row in rows:
        scored = int(row[4])
        correct = int(row[5])
        evidence.append(
            AgentEvidence(
                agent_code=str(row[0]),
                unique_matches=int(row[1]),
                predicted=int(row[2]),
                abstained=int(row[3]),
                scored=scored,
                correct=correct,
                accuracy=_safe_ratio(correct, scored),
            )
        )
    return tuple(evidence)


def _agent3_shadow(conn: psycopg.Connection) -> tuple[Agent3ShadowCohort, ...]:
    """Measure outcomes where A1/A2 agree, grouped by Agent 3 participation.

    This is descriptive shadow evidence only.  It does not change the canonical Manager
    contract and does not claim causality.  Hourly reruns are collapsed to the first
    complete three-agent prediction set per sporting match.
    """
    rows = conn.execute(
        """
        WITH first_prediction AS (
            SELECT DISTINCT ON (p.match_id, p.agent_code)
                p.match_id,
                p.agent_code::text AS agent_code,
                p.prediction_status::text AS prediction_status,
                p.selection_side
            FROM predictions p
            JOIN prediction_runs pr ON pr.prediction_run_id = p.prediction_run_id
            WHERE pr.prediction_contract_version = 'predictive_agent_result_v1'
              AND pr.run_status = 'COMPLETED'
              AND pr.prediction_finalized_at IS NOT NULL
            ORDER BY p.match_id, p.agent_code, p.as_of, p.prediction_id
        ),
        pivoted AS (
            SELECT
                match_id,
                max(prediction_status) FILTER (WHERE agent_code = 'AGENT_1_SURFACE_ELO') AS a1_status,
                max(selection_side) FILTER (WHERE agent_code = 'AGENT_1_SURFACE_ELO') AS a1_side,
                max(prediction_status) FILTER (WHERE agent_code = 'AGENT_2_HIERARCHICAL_MARKOV') AS a2_status,
                max(selection_side) FILTER (WHERE agent_code = 'AGENT_2_HIERARCHICAL_MARKOV') AS a2_side,
                max(prediction_status) FILTER (WHERE agent_code = 'AGENT_3_INTRANSITIVITY') AS a3_status,
                max(selection_side) FILTER (WHERE agent_code = 'AGENT_3_INTRANSITIVITY') AS a3_side
            FROM first_prediction
            GROUP BY match_id
        ),
        latest_results AS (
            SELECT DISTINCT ON (match_id)
                match_id,
                winner_side
            FROM match_result_observations
            WHERE outcome_status = 'COMPLETED'
              AND winner_side IN (1, 2)
            ORDER BY match_id, as_of DESC, source_observed_at DESC,
                     match_result_observation_id DESC
        ),
        classified AS (
            SELECT
                p.match_id,
                p.a1_side AS agreed_side,
                lr.winner_side,
                CASE
                    WHEN p.a3_status = 'ABSTAINED' THEN 'A3_ABSTAINS'
                    WHEN p.a3_status = 'PREDICTED' AND p.a3_side = p.a1_side THEN 'A3_ACTIVE_AGREES'
                    WHEN p.a3_status = 'PREDICTED' AND p.a3_side <> p.a1_side THEN 'A3_ACTIVE_DISAGREES'
                    ELSE 'A3_UNRESOLVED'
                END AS cohort
            FROM pivoted p
            LEFT JOIN latest_results lr ON lr.match_id = p.match_id
            WHERE p.a1_status = 'PREDICTED'
              AND p.a2_status = 'PREDICTED'
              AND p.a1_side = p.a2_side
        )
        SELECT
            cohort,
            count(*) AS sample,
            count(*) FILTER (WHERE winner_side IN (1, 2)) AS scored,
            count(*) FILTER (
                WHERE winner_side IN (1, 2)
                  AND agreed_side = winner_side
            ) AS correct
        FROM classified
        GROUP BY cohort
        ORDER BY cohort
        """
    ).fetchall()
    cohorts: list[Agent3ShadowCohort] = []
    for row in rows:
        scored = int(row[2])
        correct = int(row[3])
        cohorts.append(
            Agent3ShadowCohort(
                cohort=str(row[0]),
                sample=int(row[1]),
                scored=scored,
                correct=correct,
                accuracy=_safe_ratio(correct, scored),
            )
        )
    return tuple(cohorts)


def load_live_evidence_summary(conn: psycopg.Connection) -> LiveEvidenceSummary:
    prediction_run_id = _latest_run_id(conn)
    if prediction_run_id is None:
        return LiveEvidenceSummary(initialized=False)

    targets, consensus, no_bet, bets = _latest_funnel(conn, prediction_run_id)
    first_bets, settled, wins, losses, void_or_cancelled = _unique_first_bet_metrics(conn)
    return LiveEvidenceSummary(
        initialized=True,
        latest_prediction_run_id=prediction_run_id,
        latest_targets=targets,
        latest_manager_consensus=consensus,
        latest_manager_no_bet=no_bet,
        latest_type_a_bets=bets,
        unique_first_bets=first_bets,
        unique_settled_bets=settled,
        unique_wins=wins,
        unique_losses=losses,
        unique_void_or_cancelled=void_or_cancelled,
        unique_hit_rate=_safe_ratio(wins, wins + losses),
        manager_reasons=_reason_counts(
            conn, prediction_run_id=prediction_run_id, source="manager"
        ),
        market_reasons=_reason_counts(
            conn, prediction_run_id=prediction_run_id, source="market"
        ),
        agents=_agent_evidence(conn),
        agent3_shadow=_agent3_shadow(conn),
    )


__all__ = [
    "ReasonCount",
    "AgentEvidence",
    "Agent3ShadowCohort",
    "LiveEvidenceSummary",
    "load_live_evidence_summary",
]
