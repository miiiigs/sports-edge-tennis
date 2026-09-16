"""Read-only readiness audit for an apples-to-apples historical Type A backtest.

A full strategy replay requires point-in-time model inputs *and* genuine pre-start market
prices.  This module proves whether the canonical database has enough historical match-winner
and Pinnacle evidence before any ROI/backtest claim is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class HistoricalYearCoverage:
    season_year: int
    completed_matches: int
    any_prestart_market_matches: int
    complete_pinnacle_prestart_matches: int

    @property
    def market_coverage(self) -> float | None:
        return (
            self.any_prestart_market_matches / self.completed_matches
            if self.completed_matches
            else None
        )

    @property
    def pinnacle_coverage(self) -> float | None:
        return (
            self.complete_pinnacle_prestart_matches / self.completed_matches
            if self.completed_matches
            else None
        )


@dataclass(frozen=True)
class HistoricalStrategyReadiness:
    ready_for_full_type_a_backtest: bool
    years: tuple[HistoricalYearCoverage, ...]
    reason: str


def load_historical_strategy_readiness(
    conn: psycopg.Connection,
    *,
    year_start: int = 2019,
    year_end: int = 2025,
    minimum_pinnacle_coverage: float = 0.80,
) -> HistoricalStrategyReadiness:
    if year_start > year_end:
        raise ValueError("year_start must be <= year_end")
    if not 0 <= minimum_pinnacle_coverage <= 1:
        raise ValueError("minimum_pinnacle_coverage must be within [0, 1]")

    rows = conn.execute(
        """
        WITH completed AS (
            SELECT DISTINCT ON (mro.match_id)
                mro.match_id,
                te.season_year
            FROM match_result_observations mro
            JOIN matches m ON m.match_id = mro.match_id
            JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
            WHERE mro.outcome_status = 'COMPLETED'
              AND mro.winner_side IN (1, 2)
              AND te.season_year BETWEEN %s AND %s
            ORDER BY mro.match_id, mro.as_of DESC, mro.source_observed_at DESC,
                     mro.match_result_observation_id DESC
        ),
        starts AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.match_start_at
            FROM match_versions mv
            JOIN completed c ON c.match_id = mv.match_id
            WHERE mv.match_start_at IS NOT NULL
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        ),
        any_market AS (
            SELECT DISTINCT os.match_id
            FROM odds_snapshots os
            JOIN starts s ON s.match_id = os.match_id
            WHERE os.market_type = 'MATCH_WINNER'
              AND os.source_observed_at < s.match_start_at
              AND os.as_of < s.match_start_at
              AND os.market_status = 'OPEN'
        ),
        pinnacle_snapshot_pairs AS (
            SELECT
                os.match_id,
                COALESCE(os.source_snapshot_id, os.source_record_hash) AS snapshot_identity,
                os.source_observed_at,
                os.as_of
            FROM odds_snapshots os
            JOIN starts s ON s.match_id = os.match_id
            WHERE os.market_type = 'MATCH_WINNER'
              AND os.source_system = 'SHARP'
              AND os.bookmaker_code = 'PINNACLE'
              AND os.source_observed_at < s.match_start_at
              AND os.as_of < s.match_start_at
              AND os.market_status = 'OPEN'
              AND os.selection_type IN ('PLAYER_1', 'PLAYER_2')
            GROUP BY
                os.match_id,
                COALESCE(os.source_snapshot_id, os.source_record_hash),
                os.source_observed_at,
                os.as_of
            HAVING count(DISTINCT os.selection_type) = 2
        ),
        pinnacle_pairs AS (
            SELECT DISTINCT match_id
            FROM pinnacle_snapshot_pairs
        )
        SELECT
            c.season_year,
            count(*)::int AS completed_matches,
            count(*) FILTER (WHERE am.match_id IS NOT NULL)::int AS any_prestart_market_matches,
            count(*) FILTER (WHERE pp.match_id IS NOT NULL)::int AS complete_pinnacle_prestart_matches
        FROM completed c
        LEFT JOIN any_market am ON am.match_id = c.match_id
        LEFT JOIN pinnacle_pairs pp ON pp.match_id = c.match_id
        GROUP BY c.season_year
        ORDER BY c.season_year
        """,
        (year_start, year_end),
    ).fetchall()

    by_year = {
        int(row[0]): HistoricalYearCoverage(
            season_year=int(row[0]),
            completed_matches=int(row[1]),
            any_prestart_market_matches=int(row[2]),
            complete_pinnacle_prestart_matches=int(row[3]),
        )
        for row in rows
    }
    years = tuple(
        by_year.get(
            year,
            HistoricalYearCoverage(
                season_year=year,
                completed_matches=0,
                any_prestart_market_matches=0,
                complete_pinnacle_prestart_matches=0,
            ),
        )
        for year in range(year_start, year_end + 1)
    )

    insufficient = [
        row
        for row in years
        if row.completed_matches == 0
        or row.pinnacle_coverage is None
        or row.pinnacle_coverage < minimum_pinnacle_coverage
    ]
    if insufficient:
        missing_years = ",".join(str(row.season_year) for row in insufficient)
        return HistoricalStrategyReadiness(
            ready_for_full_type_a_backtest=False,
            years=years,
            reason=(
                "Historical Type A ROI replay is blocked until genuine complete pre-start "
                f"Pinnacle coverage reaches {minimum_pinnacle_coverage:.0%} for every evaluation "
                f"year; insufficient years: {missing_years}."
            ),
        )

    return HistoricalStrategyReadiness(
        ready_for_full_type_a_backtest=True,
        years=years,
        reason="Historical pre-start Pinnacle coverage meets the configured readiness gate.",
    )


__all__ = [
    "HistoricalYearCoverage",
    "HistoricalStrategyReadiness",
    "load_historical_strategy_readiness",
]
