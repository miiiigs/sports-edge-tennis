"""Bounded, read-only Manager-only daily outcome evidence for operations.

Uses the same first-production Manager match ledger and point-in-time match
date as the customer Results dashboard, not A1/A2 wide-spread research rows
or Value Bets. Never updates results, model probabilities or wager decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from sports_edge_tennis.performance.regimes import first_production_model_picks_cte

MANILA_TZ = ZoneInfo("Asia/Manila")
LOOKBACK_COMPLETED_DAYS = 7


@dataclass(frozen=True)
class ManagerDailyEvidence:
    day: date
    picks: int
    wins: int
    losses: int
    voids: int
    pending: int
    model_regimes: int

    @property
    def scored(self) -> int:
        return self.wins + self.losses

    @property
    def status(self) -> str:
        if self.picks == 0:
            return "NO_PICKS"
        if (self.scored + self.voids + self.pending != self.picks
                or min(self.picks, self.wins, self.losses, self.voids, self.pending) < 0):
            return "INCONSISTENT"
        if self.pending:
            return "UNSETTLED"
        if self.scored == 0:
            return "NO_GRADED"
        if self.model_regimes != 1:
            return "MIXED_OR_UNKNOWN_MODEL_REGIME"
        return "MET_73" if self.wins * 100 >= self.scored * 73 else "BELOW_73"


def manager_daily_query() -> str:
    """One bound query; no history rewrite, provider access, or odds lookup."""
    return f"""
        WITH {first_production_model_picks_cte()},
        dated AS (
            SELECT
                p.match_id,
                p.selected_side,
                p.model_regime_key,
                COALESCE(
                    (mv.match_start_at AT TIME ZONE 'Asia/Manila')::date,
                    (p.picked_at AT TIME ZONE 'Asia/Manila')::date
                ) AS local_day
            FROM first_model_picks p
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
        ),
        recent AS (
            SELECT d.*, r.outcome_status, r.winner_side
            FROM dated d
            LEFT JOIN LATERAL (
                SELECT mro.outcome_status::text AS outcome_status,
                       mro.winner_side
                FROM match_result_observations mro
                WHERE mro.match_id = d.match_id
                  AND mro.outcome_status IN (
                      'COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED'
                  )
                ORDER BY mro.as_of DESC,
                         mro.source_observed_at DESC,
                         mro.match_result_observation_id DESC
                LIMIT 1
            ) r ON TRUE
            WHERE d.local_day >= %s::date
              AND d.local_day < %s::date
        )
        SELECT
            local_day,
            count(*)::int,
            count(*) FILTER (
                WHERE outcome_status = 'COMPLETED'
                  AND winner_side = selected_side
            )::int AS wins,
            count(*) FILTER (
                WHERE outcome_status = 'COMPLETED'
                  AND winner_side IN (1, 2)
                  AND winner_side <> selected_side
            )::int AS losses,
            count(*) FILTER (
                WHERE outcome_status IN ('RETIRED', 'WALKOVER', 'CANCELLED')
            )::int AS voids,
            count(*) FILTER (
                WHERE outcome_status IS NULL
                  OR (
                      outcome_status = 'COMPLETED'
                      AND (winner_side IS NULL OR winner_side NOT IN (1, 2))
                  )
            )::int AS pending,
            count(DISTINCT model_regime_key)::int AS model_regimes
        FROM recent
        GROUP BY local_day
        ORDER BY local_day
    """


def load_manager_daily_evidence(
    conn: psycopg.Connection, *, as_of: datetime,
) -> tuple[ManagerDailyEvidence, ...]:
    """Seven consecutive *completed* calendar dates even if zero picks.

    No observations are inferred if the date is absent. The first-production
    Manager ledger remains the same immutable accounting population.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    today = as_of.astimezone(MANILA_TZ).date()
    start = today - timedelta(days=LOOKBACK_COMPLETED_DAYS)
    raw = conn.execute(manager_daily_query(), (start, today)).fetchall()
    by_day = {}
    for row in raw:
        day, picks, wins, losses, voids, pending, regimes = row
        if day in by_day:
            raise ValueError("duplicate daily Manager result")
        by_day[day] = ManagerDailyEvidence(
            day, int(picks), int(wins), int(losses),
            int(voids), int(pending), int(regimes),
        )
    return tuple(
        by_day.get(start + timedelta(days=i),
                   ManagerDailyEvidence(start + timedelta(days=i), 0, 0, 0, 0, 0, 0))
        for i in range(LOOKBACK_COMPLETED_DAYS)
    )


def render_manager_daily_evidence(row: ManagerDailyEvidence) -> str:
    accuracy = f"{row.wins / row.scored * 100:.1f}" if row.scored else "-"
    return (
        "MANAGER_DAILY_EVIDENCE "
        f"day={row.day.isoformat()} tz=Asia/Manila "
        "scope=FIRST_PRODUCTION_MANAGER_ONLY "
        f"picks={row.picks} won={row.wins} lost={row.losses} "
        f"void={row.voids} pending={row.pending} scored={row.scored} "
        f"win_rate_pct={accuracy} regimes={row.model_regimes} "
        f"target={row.status}"
    )
