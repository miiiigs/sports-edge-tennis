"""Live five-day evidence for Brian, always derived from database records.

No hardcoded past W/L, no A3/A4 fantasy results and no policy changes.
The same first-issued original population is used for the >=70% shadow.
Manager-only is a distinctly defined independently-issued cohort.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Mapping

from sports_edge_tennis.dashboard.qualified_history import ComparisonDay


def _cohort(values: Iterable[tuple[int, int, int, int, int]]) -> dict[str, object]:
    picks = wins = losses = voids = pending = 0
    for p, w, l, v, pn in values:
        if (any(type(x) is not int or x < 0 for x in (p, w, l, v, pn))
                or p != w + l + v + pn):
            raise ValueError("inconsistent original result totals")
        picks += p
        wins += w
        losses += l
        voids += v
        pending += pn
    graded = wins + losses
    return {
        "picks": picks, "won": wins, "lost": losses,
        "void": voids, "pending": pending, "graded": graded,
        "win_rate": wins / graded if graded else None,
        "provisional": pending > 0,
    }


def latest_five_day_evidence(
    days: Iterable[ComparisonDay],
    *,
    as_of_date: date,
    manager: Mapping[str, object] | None = None,
    independent_original_days: Iterable[object] = (),
) -> dict[str, object]:
    """Five previous Manila dates; never average daily win percentages."""
    selected = {
        d.day: d for d in days
        if as_of_date - timedelta(days=5) <= d.day < as_of_date
    }
    dates = tuple(
        as_of_date - timedelta(days=delta) for delta in range(5, 0, -1)
    )
    if set(selected) != set(dates):
        # The history loader should produce all calendar days, including
        # zero-pick days. Missing dates are a data error, not zero losses.
        raise ValueError("missing one or more five-day dates")
    original = _cohort(
        (
            selected[d].original, selected[d].original_won,
            selected[d].original_lost, selected[d].original_void,
            selected[d].original_pending,
        ) for d in dates
    )
    qualified = _cohort(
        (
            selected[d].qualified, selected[d].qualified_won,
            selected[d].qualified_lost, selected[d].qualified_void,
            selected[d].qualified_pending,
        ) for d in dates
    )
    if any(qualified[k] > original[k] for k in
           ("picks", "won", "lost", "void", "pending", "graded")):
        raise ValueError("research filter is not a subset of original")
    rejected = _cohort([tuple(
        original[k] - qualified[k] for k in
        ("picks", "won", "lost", "void", "pending")
    )])
    coverage = qualified["picks"] / original["picks"] if original["picks"] else None
    aligned_by_date = {x.decision_date: x for x in independent_original_days}
    mismatched_dates: list[str] = []
    for d in dates:
        o = selected[d]
        ref = aligned_by_date.get(d)
        if ref is None or (
            o.original != ref.picks
            or o.original_won != ref.wins
            or o.original_lost != ref.losses
            or o.original_void != ref.voids
            or o.original_pending != ref.pending
        ):
            mismatched_dates.append(d.isoformat())

    manager_output = None
    if manager is not None:
        manager_days = {date.fromisoformat(row["date"]): row for row in manager["days"]}
        if set(manager_days) != set(dates):
            raise ValueError("manager cohort dates mismatch")
        manager_output = _cohort(
            (
                manager_days[d]["picks"], manager_days[d]["wins"],
                manager_days[d]["losses"], manager_days[d]["voids"],
                manager_days[d]["pending"],
            ) for d in dates
        )
        manager_output["independent_cohort"] = True

    daily = []
    for d in dates:
        x = selected[d]
        n = x.qualified_won + x.qualified_lost + x.qualified_pending
        daily.append({
            "date": d.isoformat(),
            "original": x.original,
            "original_won": x.original_won,
            "original_lost": x.original_lost,
            "original_void": x.original_void,
            "original_pending": x.original_pending,
            "original_win_rate": x.original_accuracy,
            "qualified": x.qualified,
            "qualified_won": x.qualified_won,
            "qualified_lost": x.qualified_lost,
            "qualified_void": x.qualified_void,
            "qualified_pending": x.qualified_pending,
            "qualified_win_rate": x.qualified_accuracy,
            "qualified_best_if_pending_win": (
                (x.qualified_won + x.qualified_pending) / n if n else None
            ),
            "qualified_worst_if_pending_lose": (
                x.qualified_won / n if n else None
            ),
            "status": (
                "RECONCILIATION_REQUIRED" if d.isoformat() in mismatched_dates
                else "PROVISIONAL" if x.original_pending or x.qualified_pending
                else "SETTLED" if x.original else "NO_PICKS"
            ),
        })

    return {
        "period_start": dates[0].isoformat(),
        "period_end": dates[-1].isoformat(),
        "timezone": "Asia/Manila",
        "source": "LIVE_DATABASE_FIRST_ISSUED_RESULTS",
        "research_filter": "PIT_CONFIDENCE_GE70_V1",
        "original": original,
        "qualified": {**qualified, "selection_coverage": coverage},
        "rejected": rejected,
        "manager_only": manager_output,
        "ledger_reconciled": not mismatched_dates,
        "reconciliation_mismatch_dates": mismatched_dates,
        "is_final": (
            not mismatched_dates and
            not original["provisional"] and
            not qualified["provisional"] and
            (manager_output is None or not manager_output["provisional"])
        ),
        "a3_a4": {
            "status": "NOT_VALIDATED_NO_LIVE_RESULT_SERIES",
            "picks": None,
            "win_rate": None,
        },
        "daily": daily,
    }
