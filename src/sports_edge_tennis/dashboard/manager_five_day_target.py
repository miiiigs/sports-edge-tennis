"""Read-only Manager-only 73%-on-five-consecutive-days evidence.

This scorecard is NOT a model improvement or future win-rate guarantee.
Evaluates the five most recent *completed local calendar days*, never
cherry-picks successful days or counts A1/A2 research-only tracking picks.
Pending results block certification of their day; empty calendar days break
the sequence instead of silently being skipped.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Protocol


class ManagerDay(Protocol):
    decision_date: date
    picks: int
    scored: int
    pending: int
    voids: int
    wins: int
    losses: int


def manager_five_day_target(
    summaries: Iterable[ManagerDay],
    *,
    as_of_date: date,
) -> dict[str, object]:
    """Compute Brian's target from original Manager-first production picks.

    The 73% comparison uses integer arithmetic to avoid rounding errors.
    A day with 1/1 wins technically clears 73%, but is labeled low-sample:
    the dashboard must display its scored count, not suggest statistical proof.
    """
    grouped: dict[date, list[ManagerDay]] = {}
    for summary in summaries:
        grouped.setdefault(summary.decision_date, []).append(summary)

    days: list[dict[str, object]] = []
    for delta in range(1, 6):
        day = as_of_date - timedelta(days=delta)
        rows = grouped.get(day, [])
        picks = scored = wins = losses = voids = pending = 0
        status = "NO_PICKS"
        if len(rows) > 1:
            status = "DUPLICATE_DAY"
        elif rows:
            row = rows[0]
            picks = int(row.picks)
            scored = int(row.scored)
            wins = int(row.wins)
            losses = int(row.losses)
            voids = int(row.voids)
            pending = int(row.pending)
            if (
                any(x < 0 for x in (picks, scored, wins, losses, voids, pending))
                or scored != wins + losses
                or picks != scored + voids + pending
            ):
                status = "INCONSISTENT"
            elif picks == 0:
                status = "NO_PICKS"
            elif pending:
                status = "AWAITING_RESULTS"
            elif scored == 0:
                status = "NO_GRADED"
            elif 100 * wins >= 73 * scored:
                status = "MET"
            else:
                status = "BELOW_TARGET"
        days.append({
            "date": day.isoformat(),
            "picks": picks,
            "scored": scored,
            "wins": wins,
            "losses": losses,
            "voids": voids,
            "pending": pending,
            "win_rate": wins / scored if scored else None,
            "status": status,
            "low_sample": scored < 5 and scored > 0,
        })

    streak = 0
    for row in days:
        if row["status"] != "MET":
            break
        streak += 1
    return {
        "target_percent": 73,
        "required_consecutive_days": 5,
        "observed_consecutive_days": streak,
        "target_met": streak == 5,
        "scope": "FIRST_PRODUCTION_MANAGER_ONLY",
        "timezone_day_rule": "five completed local calendar days immediately before as_of_date",
        "voids_excluded": True,
        "pending_days_cannot_pass": True,
        "low_sample_warning": any(row["low_sample"] for row in days if row["status"] == "MET"),
        "days": days,
    }
