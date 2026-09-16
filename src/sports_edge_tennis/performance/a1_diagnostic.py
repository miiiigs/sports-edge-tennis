"""Read-only Agent 1 production-live Challenger diagnostics.

This module answers a narrow question: where is Agent 1's first-prediction live
Challenger performance succeeding or failing? It intentionally excludes experiment
and backtest runs and never mutates model, Manager, market, Veto, staking, or
publication state.

Only evidence reproducible from the persisted prediction ledger is reported. The
Agent 1 runtime object also computes bridge-aware player history depth and cold-start
diagnostics, but those fields are not persisted with predictions today; this module
does not reconstruct or guess them after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import psycopg


@dataclass(frozen=True)
class A1DiagnosticSlice:
    dimension: str
    value: str
    sample: int
    scored: int
    correct: int
    accuracy: float | None
    brier_score: float | None
    mean_confidence: float | None


@dataclass(frozen=True)
class A1LiveChallengerDiagnostic:
    initialized: bool
    slices: tuple[A1DiagnosticSlice, ...] = ()
    unavailable_fields: tuple[str, ...] = (
        "bridge_aware_player_history_depth",
        "bridge_aware_surface_history_depth",
        "runtime_cold_start_flags",
        "runtime_model_identity_status",
        "runtime_model_surface_resolution_status",
    )


def _as_float(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def load_a1_live_challenger_diagnostic(
    conn: psycopg.Connection,
) -> A1LiveChallengerDiagnostic:
    """Return first-prediction A1 Challenger performance by reproducible slices.

    Production-live boundary:
    - predictive_agent_result_v1 completed/finalized runs only
    - prediction_runs.experiment_id IS NULL
    - predictions.experiment_id IS NULL
    - first A1 prediction per sporting match

    Match metadata is selected point-in-time at the prediction timestamp. Final
    COMPLETED result truth is used only to score the prediction after the event.
    """
    rows = conn.execute(
        """
        WITH first_prediction AS (
            SELECT DISTINCT ON (p.match_id)
                p.prediction_id,
                p.match_id,
                p.selection_side,
                p.win_probability,
                p.model_version,
                p.feature_version,
                p.as_of AS prediction_as_of
            FROM predictions p
            JOIN prediction_runs pr
              ON pr.prediction_run_id = p.prediction_run_id
            WHERE p.agent_code = 'AGENT_1_SURFACE_ELO'
              AND p.prediction_status = 'PREDICTED'
              AND p.selection_side IN (1, 2)
              AND p.win_probability IS NOT NULL
              AND pr.prediction_contract_version = 'predictive_agent_result_v1'
              AND pr.run_status = 'COMPLETED'
              AND pr.prediction_finalized_at IS NOT NULL
              AND pr.experiment_id IS NULL
              AND p.experiment_id IS NULL
            ORDER BY p.match_id, p.as_of, p.prediction_id
        ),
        enriched AS (
            SELECT
                fp.*,
                mv.player_1_id,
                mv.player_2_id,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                COALESCE(mv.surface::text, 'UNKNOWN') AS fixture_surface,
                CASE
                    WHEN mv.qualifying_flag IS TRUE THEN 'QUALIFYING'
                    WHEN mv.qualifying_flag IS FALSE THEN 'MAIN_DRAW'
                    ELSE 'UNKNOWN'
                END AS draw_status,
                mv.match_start_at,
                COALESCE(t.canonical_name, 'UNKNOWN') AS tournament_name,
                lr.winner_side,
                CASE
                    WHEN mv.player_1_id IS NULL OR mv.player_2_id IS NULL THEN 'UNKNOWN'
                    WHEN EXISTS (
                        SELECT 1
                        FROM player_source_ids psi
                        WHERE psi.player_id = mv.player_1_id
                          AND psi.source_system <> 'API_TENNIS'
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM player_source_ids psi
                        WHERE psi.player_id = mv.player_2_id
                          AND psi.source_system <> 'API_TENNIS'
                    ) THEN 'BOTH_DIRECT_HISTORICAL'
                    WHEN EXISTS (
                        SELECT 1
                        FROM player_source_ids psi
                        WHERE psi.player_id = mv.player_1_id
                          AND psi.source_system <> 'API_TENNIS'
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM player_source_ids psi
                        WHERE psi.player_id = mv.player_2_id
                          AND psi.source_system <> 'API_TENNIS'
                    ) THEN 'ONE_DIRECT_HISTORICAL'
                    ELSE 'NO_DIRECT_HISTORICAL'
                END AS direct_identity_provenance
            FROM first_prediction fp
            LEFT JOIN LATERAL (
                SELECT
                    v.player_1_id,
                    v.player_2_id,
                    v.tour_scope,
                    v.competition_tier,
                    v.surface,
                    v.qualifying_flag,
                    v.match_start_at
                FROM match_versions v
                WHERE v.match_id = fp.match_id
                  AND v.as_of <= fp.prediction_as_of
                  AND v.source_observed_at <= fp.prediction_as_of
                ORDER BY v.as_of DESC, v.source_observed_at DESC, v.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
            LEFT JOIN matches m
              ON m.match_id = fp.match_id
            LEFT JOIN tournament_editions te
              ON te.tournament_edition_id = m.tournament_edition_id
            LEFT JOIN tournaments t
              ON t.tournament_id = te.tournament_id
            LEFT JOIN LATERAL (
                SELECT mro.winner_side
                FROM match_result_observations mro
                WHERE mro.match_id = fp.match_id
                  AND mro.outcome_status = 'COMPLETED'
                  AND mro.winner_side IN (1, 2)
                ORDER BY mro.as_of DESC,
                         mro.source_observed_at DESC,
                         mro.match_result_observation_id DESC
                LIMIT 1
            ) lr ON TRUE
            WHERE mv.tour_scope::text = 'CHALLENGER'
               OR mv.competition_tier::text = 'CHALLENGER'
        ),
        base AS (
            SELECT
                *,
                CASE
                    WHEN winner_side IS NULL THEN NULL
                    WHEN selection_side = winner_side THEN 1
                    ELSE 0
                END AS correct_flag,
                CASE
                    WHEN winner_side IS NULL THEN NULL
                    WHEN selection_side = winner_side
                        THEN power(win_probability - 1, 2)
                    ELSE power(win_probability, 2)
                END AS squared_error,
                CASE
                    WHEN win_probability < 0.55 THEN '50-55%'
                    WHEN win_probability < 0.60 THEN '55-60%'
                    WHEN win_probability < 0.65 THEN '60-65%'
                    WHEN win_probability < 0.70 THEN '65-70%'
                    WHEN win_probability < 0.80 THEN '70-80%'
                    ELSE '80%+'
                END AS confidence_band,
                CASE
                    WHEN match_start_at IS NULL THEN 'UNKNOWN'
                    WHEN match_start_at < prediction_as_of THEN 'AFTER_START'
                    WHEN match_start_at - prediction_as_of <= interval '6 hours' THEN '0-6H'
                    WHEN match_start_at - prediction_as_of <= interval '12 hours' THEN '6-12H'
                    WHEN match_start_at - prediction_as_of <= interval '24 hours' THEN '12-24H'
                    WHEN match_start_at - prediction_as_of <= interval '48 hours' THEN '24-48H'
                    ELSE '48H+'
                END AS entry_lead_time,
                to_char(prediction_as_of AT TIME ZONE 'UTC', 'IYYY-"W"IW') AS calendar_week
            FROM enriched
        ),
        dimensions AS (
            SELECT *, 'overall'::text AS dimension, 'ALL'::text AS value FROM base
            UNION ALL
            SELECT *, 'fixture_surface', fixture_surface FROM base
            UNION ALL
            SELECT *, 'draw_status', draw_status FROM base
            UNION ALL
            SELECT *, 'confidence_band', confidence_band FROM base
            UNION ALL
            SELECT *, 'entry_lead_time', entry_lead_time FROM base
            UNION ALL
            SELECT *, 'calendar_week', calendar_week FROM base
            UNION ALL
            SELECT *, 'tournament', tournament_name FROM base
            UNION ALL
            SELECT *, 'direct_identity_provenance', direct_identity_provenance FROM base
            UNION ALL
            SELECT *, 'model_version', model_version FROM base
            UNION ALL
            SELECT *, 'feature_version', feature_version FROM base
        )
        SELECT
            dimension,
            value,
            count(*) AS sample,
            count(*) FILTER (WHERE winner_side IS NOT NULL) AS scored,
            count(*) FILTER (WHERE correct_flag = 1) AS correct,
            avg(correct_flag::numeric) FILTER (WHERE winner_side IS NOT NULL) AS accuracy,
            avg(squared_error) FILTER (WHERE winner_side IS NOT NULL) AS brier_score,
            avg(win_probability) AS mean_confidence
        FROM dimensions
        GROUP BY dimension, value
        ORDER BY
            CASE dimension
                WHEN 'overall' THEN 0
                WHEN 'fixture_surface' THEN 1
                WHEN 'draw_status' THEN 2
                WHEN 'confidence_band' THEN 3
                WHEN 'entry_lead_time' THEN 4
                WHEN 'direct_identity_provenance' THEN 5
                WHEN 'calendar_week' THEN 6
                WHEN 'tournament' THEN 7
                WHEN 'model_version' THEN 8
                WHEN 'feature_version' THEN 9
                ELSE 99
            END,
            value
        """
    ).fetchall()

    slices = tuple(
        A1DiagnosticSlice(
            dimension=str(row[0]),
            value=str(row[1]),
            sample=int(row[2]),
            scored=int(row[3]),
            correct=int(row[4]),
            accuracy=_as_float(row[5]),
            brier_score=_as_float(row[6]),
            mean_confidence=_as_float(row[7]),
        )
        for row in rows
    )
    return A1LiveChallengerDiagnostic(initialized=bool(slices), slices=slices)


__all__ = [
    "A1DiagnosticSlice",
    "A1LiveChallengerDiagnostic",
    "load_a1_live_challenger_diagnostic",
]
