"""Read-only monitor for aligned Model Picks whose fixture is terminal but has no result.

Match ``131446`` (Asia/Manila 2026-10-01) was reported ``COMPLETED`` by the fixture
feed for many hours while zero ``match_result_observations`` rows existed, so the
pick stayed ``PENDING``. This module surfaces that exact pattern as a diagnostic
condition; it never repairs or mutates production result truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)

TERMINAL_FIXTURE_STATUSES = ("COMPLETED", "RETIRED", "WALKOVER", "CANCELLED")

_COVERAGE_SQL = """
    WITH {cte},
    aligned_matches AS (
        SELECT match_id, max(picked_at) AS picked_at
        FROM aligned_model_picks
        GROUP BY match_id
    ),
    latest_match_version AS (
        SELECT DISTINCT ON (match_id)
            match_id,
            match_status::text AS match_status,
            match_start_at,
            source_observed_at
        FROM match_versions
        ORDER BY match_id, as_of DESC, source_observed_at DESC, match_version_id DESC
    ),
    terminal_result AS (
        SELECT DISTINCT ON (match_id)
            match_id
        FROM match_result_observations
        WHERE outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
        ORDER BY match_id, as_of DESC, source_observed_at DESC, match_result_observation_id DESC
    )
    SELECT
        am.match_id,
        lmv.match_status,
        lmv.match_start_at,
        lmv.source_observed_at,
        am.picked_at
    FROM aligned_matches am
    JOIN latest_match_version lmv ON lmv.match_id = am.match_id
    LEFT JOIN terminal_result tr ON tr.match_id = am.match_id
    WHERE tr.match_id IS NULL
      AND lmv.match_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
      AND lmv.match_start_at IS NOT NULL
      AND lmv.match_start_at < %s
    ORDER BY lmv.match_start_at, am.match_id
    LIMIT %s
"""

_SOURCES_SQL = """
    SELECT match_id, source_system, source_match_id
    FROM match_source_ids
    WHERE match_id = ANY(%s)
    ORDER BY match_id, source_system
"""

_LAST_REFRESH_SQL = """
    SELECT max(COALESCE(finished_at, started_at))
    FROM ingest_runs
    WHERE source_dataset = 'api_tennis_completed_result_refresh'
"""


@dataclass(frozen=True)
class TerminalFixtureWithoutResult:
    match_id: int
    match_status: str
    match_start_at: datetime | None
    fixture_observed_at: datetime
    picked_at: datetime
    age_hours: float | None
    source_systems: tuple[str, ...]
    source_match_ids: tuple[str, ...]
    last_result_refresh_at: datetime | None


@dataclass(frozen=True)
class ResultCoverageReport:
    generated_at: datetime
    grace_hours: int
    last_result_refresh_at: datetime | None
    items: tuple[TerminalFixtureWithoutResult, ...]

    @property
    def stale_count(self) -> int:
        return len(self.items)


def load_terminal_fixtures_without_result(
    conn: psycopg.Connection,
    *,
    grace_hours: int = 12,
    limit: int = 500,
    now: datetime | None = None,
) -> ResultCoverageReport:
    """Aligned Model Picks whose latest fixture status is terminal with no result."""
    if grace_hours < 0:
        raise ValueError("grace_hours must be non-negative")
    if limit < 1:
        raise ValueError("limit must be positive")

    reference = now or datetime.now(tz=UTC)
    cutoff = reference - timedelta(hours=grace_hours)

    rows = conn.execute(
        _COVERAGE_SQL.format(cte=aligned_production_model_picks_cte()),
        (cutoff, limit),
    ).fetchall()

    match_ids = [int(row[0]) for row in rows]
    sources: dict[int, list[tuple[str, str]]] = {}
    if match_ids:
        for match_id, source_system, source_match_id in conn.execute(
            _SOURCES_SQL, (match_ids,)
        ).fetchall():
            sources.setdefault(int(match_id), []).append(
                (str(source_system), str(source_match_id))
            )

    last_refresh_row = conn.execute(_LAST_REFRESH_SQL).fetchone()
    last_refresh = last_refresh_row[0] if last_refresh_row else None

    items: list[TerminalFixtureWithoutResult] = []
    for match_id, status, match_start_at, observed_at, picked_at in rows:
        match_id = int(match_id)
        entry_sources = sources.get(match_id, [])
        items.append(
            TerminalFixtureWithoutResult(
                match_id=match_id,
                match_status=str(status),
                match_start_at=match_start_at,
                fixture_observed_at=observed_at,
                picked_at=picked_at,
                age_hours=_age_hours(match_start_at, reference),
                source_systems=tuple(system for system, _ in entry_sources),
                source_match_ids=tuple(source_id for _, source_id in entry_sources),
                last_result_refresh_at=last_refresh,
            )
        )

    return ResultCoverageReport(
        generated_at=reference,
        grace_hours=grace_hours,
        last_result_refresh_at=last_refresh,
        items=tuple(items),
    )


def _age_hours(match_start_at: datetime | None, reference: datetime) -> float | None:
    if match_start_at is None:
        return None
    return (reference - match_start_at).total_seconds() / 3600.0


__all__ = [
    "ResultCoverageReport",
    "TERMINAL_FIXTURE_STATUSES",
    "TerminalFixtureWithoutResult",
    "load_terminal_fixtures_without_result",
]
