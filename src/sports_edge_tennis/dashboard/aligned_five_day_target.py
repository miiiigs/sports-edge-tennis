"""Read-only Brian acceptance: all original aligned Model Picks, Manila days.

Matches the rolling window and integer 73% criterion from the production
runner. This is not the Manager-only diagnostic and never filters picks.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Protocol

WINDOW_START = date(2026, 10, 28)
WINDOW_DAYS = 5
TARGET_PERCENT = 73


class AlignedDay(Protocol):
    decision_date: date
    picks: int
    scored: int
    wins: int
    losses: int
    voids: int
    pending: int


def aligned_five_day_target(
    summaries: Iterable[AlignedDay], *, as_of_date: date
) -> dict[str, object]:
    """Compare every original aligned pick across five consecutive Manila dates.

    The initial window is 28 Oct–1 Nov. After 2 Nov it rolls to the
    five immediately preceding *completed* Manila days; gaps are not skipped.
    An in-progress or unsettled day cannot qualify, regardless of its W/L rate.
    """
    start = max(WINDOW_START, as_of_date - timedelta(days=WINDOW_DAYS))
    by_day: dict[date, list[AlignedDay]] = {}
    for row in summaries:
        by_day.setdefault(row.decision_date, []).append(row)

    days: list[dict[str, object]] = []
    for offset in range(WINDOW_DAYS):
        day = start + timedelta(days=offset)
        rows = by_day.get(day, ())
        picks = scored = won = lost = void = pending = 0
        if len(rows) == 1:
            v = rows[0]
            picks, scored, won, lost, void, pending = (
                int(v.picks), int(v.scored), int(v.wins), int(v.losses),
                int(v.voids), int(v.pending),
            )

        if day >= as_of_date:
            status = "IN_PROGRESS"
        elif len(rows) > 1:
            status = "INCONSISTENT"
        elif any(x < 0 for x in (picks, scored, won, lost, void, pending)):
            status = "INCONSISTENT"
        elif scored != won + lost or picks != scored + void + pending:
            status = "INCONSISTENT"
        elif picks == 0:
            status = "NO_PICKS"
        elif pending:
            status = "UNSETTLED"
        elif scored == 0:
            status = "NO_GRADED"
        elif 100 * won >= TARGET_PERCENT * scored:
            status = "MET_73"
        else:
            status = "BELOW_73"

        days.append({
            "date": day.isoformat(), "picks": picks, "graded": scored,
            "wins": won, "losses": lost, "voids": void, "pending": pending,
            "win_rate": won / scored if scored else None,
            "status": status, "low_sample": 0 < scored < 5,
        })

    qualified = sum(day["status"] == "MET_73" for day in days)
    return {
        "scope": "ALL_ORIGINAL_ALIGNED_MODEL_PICKS",
        "timezone": "Asia/Manila",
        "target_percent": TARGET_PERCENT,
        "required_consecutive_days": WINDOW_DAYS,
        "completed_qualifying_days": qualified,
        "target_met": qualified == WINDOW_DAYS,
        "status": "MET_FIVE_COMPLETE_DAYS" if qualified == WINDOW_DAYS else "NOT_YET_MET",
        "start": start.isoformat(),
        "end": (start + timedelta(days=WINDOW_DAYS - 1)).isoformat(),
        "days": days,
        "low_sample_warning": any(x["low_sample"] for x in days if x["status"] == "MET_73"),
    }
