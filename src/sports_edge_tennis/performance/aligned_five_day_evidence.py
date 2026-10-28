"""Five-day acceptance on ALL ORIGINAL ALIGNED Model Picks, not Manager-only.

This module reads the same canonical first-production aligned population as the
Results dashboard and never changes decisions, results, thresholds or bankroll.
A day qualifies only with original non-empty picks, completed local date, full
result accounting and >=73% graded win rate. The target is not a guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary,
    load_aligned_daily_model_pick_summaries,
)

MANILA = ZoneInfo("Asia/Manila")
WINDOW_START = date(2026, 10, 28)
WINDOW_DAYS = 5
TARGET_PERCENT = 73


@dataclass(frozen=True)
class AlignedDailyEvidence:
    day: date
    picks: int
    wins: int
    losses: int
    voids: int
    pending: int
    complete_day: bool

    @property
    def graded(self) -> int:
        return self.wins + self.losses

    @property
    def accuracy_pct(self) -> float | None:
        return (100 * self.wins / self.graded) if self.graded else None

    @property
    def status(self) -> str:
        if not self.complete_day:
            return "IN_PROGRESS"
        if self.picks < 1:
            return "NO_PICKS"
        if min(self.wins, self.losses, self.voids, self.pending) < 0:
            return "INCONSISTENT"
        if self.wins + self.losses + self.voids + self.pending != self.picks:
            return "INCONSISTENT"
        if self.pending:
            return "UNSETTLED"
        if not self.graded:
            return "NO_GRADED"
        return "MET_73" if 100*self.wins >= TARGET_PERCENT*self.graded else "BELOW_73"


@dataclass(frozen=True)
class AlignedFiveDayEvidence:
    start: date
    end: date
    observed_at: datetime
    days: tuple[AlignedDailyEvidence, ...]

    @property
    def status(self) -> str:
        if len(self.days) != WINDOW_DAYS:
            return "INCOMPLETE_WINDOW"
        if any(d.status != "MET_73" for d in self.days):
            return "NOT_YET_MET"
        return "MET_FIVE_COMPLETE_DAYS"


def from_dashboard_rows(
    rows: tuple[DailyModelPickSummary, ...], *, as_of: datetime,
) -> AlignedFiveDayEvidence:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    today_local = as_of.astimezone(MANILA).date()
    by_day = {}
    for row in rows:
        if row.decision_date in by_day:
            raise ValueError("duplicate aligned day")
        by_day[row.decision_date] = row
    days = []
    for offset in range(WINDOW_DAYS):
        day = WINDOW_START + timedelta(days=offset)
        v = by_day.get(day)
        days.append(AlignedDailyEvidence(
            day=day, picks=v.picks if v else 0,
            wins=v.wins if v else 0, losses=v.losses if v else 0,
            voids=v.voids if v else 0, pending=v.pending if v else 0,
            complete_day=day < today_local,
        ))
    return AlignedFiveDayEvidence(
        start=WINDOW_START,
        end=WINDOW_START + timedelta(days=WINDOW_DAYS-1),
        observed_at=as_of, days=tuple(days),
    )


def load_aligned_five_day_evidence(
    conn: psycopg.Connection, *, as_of: datetime,
) -> AlignedFiveDayEvidence:
    # One immutable read-only current-Results population query, independent of
    # whether the upstream fixture provider succeeded in this hourly cycle.
    rows = load_aligned_daily_model_pick_summaries(
        conn, limit=100, timezone_name="Asia/Manila"
    )
    return from_dashboard_rows(rows, as_of=as_of)


def render_aligned_five_day_evidence(
    report: AlignedFiveDayEvidence,
) -> tuple[str, ...]:
    lines = []
    for d in report.days:
        percentage = f"{d.accuracy_pct:.1f}" if d.accuracy_pct is not None else "-"
        lines.append(
            "ALIGNED_DAILY_EVIDENCE "
            f"day={d.day.isoformat()} scope=ALL_ORIGINAL_ALIGNED_MODEL_PICKS "
            f"tz=Asia/Manila original_picks={d.picks} "
            f"won={d.wins} lost={d.losses} void={d.voids} pending={d.pending} "
            f"graded={d.graded} win_rate_pct={percentage} "
            f"target=73 status={d.status}"
        )
    completed = sum(x.status == "MET_73" for x in report.days)
    lines.append(
        "ALIGNED_FIVE_DAY_STATUS "
        f"start={report.start.isoformat()} end={report.end.isoformat()} "
        "scope=ALL_ORIGINAL_ALIGNED_MODEL_PICKS "
        f"completed_qualifying_days={completed} required=5 "
        f"status={report.status}"
    )
    return tuple(lines)
