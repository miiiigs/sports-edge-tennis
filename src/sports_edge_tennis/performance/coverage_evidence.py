"""Read-only point-in-time coverage diagnostics for predictive agents.

The purpose is to measure where each agent can actually participate before changing data
providers or Manager rules. Match metadata is resolved as it was visible at the first
production-live prediction timestamp, avoiding today's corrected metadata leaking into
historical coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class CoverageSlice:
    agent_code: str
    dimension: str
    value: str
    unique_matches: int
    predicted: int
    abstained: int
    coverage_rate: float | None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def load_agent_coverage_slices(conn: psycopg.Connection) -> tuple[CoverageSlice, ...]:
    """Return production-live first-prediction coverage by point-in-time fixture metadata."""
    rows = conn.execute(
        """
        WITH first_prediction AS (
            SELECT DISTINCT ON (p.match_id, p.agent_code)
                p.match_id,
                p.agent_code::text AS agent_code,
                p.prediction_status::text AS prediction_status,
                p.as_of AS prediction_as_of
            FROM predictions p
            JOIN prediction_runs pr ON pr.prediction_run_id = p.prediction_run_id
            WHERE pr.prediction_contract_version = 'predictive_agent_result_v1'
              AND pr.run_status = 'COMPLETED'
              AND pr.prediction_finalized_at IS NOT NULL
              AND pr.experiment_id IS NULL
              AND p.experiment_id IS NULL
            ORDER BY p.match_id, p.agent_code, p.as_of, p.prediction_id
        ),
        enriched AS (
            SELECT
                fp.*,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                COALESCE(mv.surface::text, 'UNKNOWN') AS surface,
                CASE
                    WHEN mv.qualifying_flag IS TRUE THEN 'QUALIFYING'
                    WHEN mv.qualifying_flag IS FALSE THEN 'MAIN_DRAW'
                    ELSE 'UNKNOWN'
                END AS qualifying
            FROM first_prediction fp
            LEFT JOIN LATERAL (
                SELECT
                    v.tour_scope,
                    v.competition_tier,
                    v.surface,
                    v.qualifying_flag
                FROM match_versions v
                WHERE v.match_id = fp.match_id
                  AND v.as_of <= fp.prediction_as_of
                  AND v.source_observed_at <= fp.prediction_as_of
                ORDER BY v.as_of DESC, v.source_observed_at DESC, v.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
        ),
        dimensions AS (
            SELECT agent_code, prediction_status, 'tour_scope'::text AS dimension,
                   COALESCE(tour_scope, 'UNKNOWN')::text AS value
            FROM enriched
            UNION ALL
            SELECT agent_code, prediction_status, 'competition_tier',
                   COALESCE(competition_tier, 'UNKNOWN')
            FROM enriched
            UNION ALL
            SELECT agent_code, prediction_status, 'surface', surface
            FROM enriched
            UNION ALL
            SELECT agent_code, prediction_status, 'draw_status', qualifying
            FROM enriched
        )
        SELECT
            agent_code,
            dimension,
            value,
            count(*) AS unique_matches,
            count(*) FILTER (WHERE prediction_status = 'PREDICTED') AS predicted,
            count(*) FILTER (WHERE prediction_status = 'ABSTAINED') AS abstained
        FROM dimensions
        GROUP BY agent_code, dimension, value
        ORDER BY agent_code, dimension, value
        """
    ).fetchall()

    result: list[CoverageSlice] = []
    for row in rows:
        unique_matches = int(row[3])
        predicted = int(row[4])
        result.append(
            CoverageSlice(
                agent_code=str(row[0]),
                dimension=str(row[1]),
                value=str(row[2]),
                unique_matches=unique_matches,
                predicted=predicted,
                abstained=int(row[5]),
                coverage_rate=_ratio(predicted, unique_matches),
            )
        )
    return tuple(result)


__all__ = ["CoverageSlice", "load_agent_coverage_slices"]
