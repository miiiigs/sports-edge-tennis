from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import psycopg


_SUPPORTED_TOURS = ("ATP_MAIN", "CHALLENGER")
_SUPPORTED_TIERS = (
    "GRAND_SLAM",
    "MASTERS_1000",
    "ATP_500",
    "ATP_250",
    "ATP_TOUR",
    "CHALLENGER",
)


def load_price_scanner_candidate_match_ids(
    conn: psycopg.Connection,
    *,
    horizon_start: date,
    horizon_days: int,
    as_of: datetime,
) -> tuple[int, ...]:
    """Return every canonical in-scope scheduled API-Tennis singles fixture.

    Type B is intentionally independent of predictive-agent and Manager eligibility,
    so its price slate must be derived from the canonical fixture feed rather than
    prediction_run_targets or manager_consensus_results.
    """
    if horizon_days < 1:
        return ()
    start_at = datetime.combine(horizon_start, time.min, tzinfo=timezone.utc)
    end_at = start_at + timedelta(days=horizon_days)
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.match_status::text AS match_status,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                mv.match_start_at
            FROM match_versions mv
            WHERE mv.as_of <= %s
              AND mv.source_observed_at <= %s
            ORDER BY
                mv.match_id,
                mv.as_of DESC,
                mv.source_observed_at DESC,
                mv.match_version_id DESC
        )
        SELECT latest.match_id
        FROM latest
        WHERE latest.match_status = 'SCHEDULED'
          AND latest.tour_scope = ANY(%s)
          AND latest.competition_tier = ANY(%s)
          AND latest.match_start_at >= %s
          AND latest.match_start_at < %s
          AND EXISTS (
              SELECT 1
              FROM match_source_ids msi
              WHERE msi.match_id = latest.match_id
                AND msi.source_system = 'API_TENNIS'
          )
        ORDER BY latest.match_id
        """,
        (
            as_of,
            as_of,
            list(_SUPPORTED_TOURS),
            list(_SUPPORTED_TIERS),
            start_at,
            end_at,
        ),
    ).fetchall()
    return tuple(int(row[0]) for row in rows)
