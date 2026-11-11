"""Authoritative three-window W/L/V/P calculations with no database writes."""
from __future__ import annotations
from datetime import date, datetime, timedelta
from sports_edge_tennis.dashboard.qualified_history import ComparisonDay, START_DAY

FIXED_START = date(2026, 11, 5)
FIXED_END = date(2026, 11, 9)


def _aggregate(rows: list[tuple[int, int, int, int, int]]) -> dict:
    picks = won = lost = void = pending = 0
    for p, w, l, v, n in rows:
        if min(p, w, l, v, n) < 0 or p != w+l+v+n:
            raise ValueError("inconsistent original or research W/L/VOID/PENDING")
        picks += p
        won += w
        lost += l
        void += v
        pending += n
    graded = won+lost
    return {
        "picks": picks, "won": won, "lost": lost,
        "void": void, "pending": pending, "graded": graded,
        "settled_accuracy": won/graded if graded else None,
    }


def report_windows(
    days: list[ComparisonDay], manager: list,
    *,
    as_of_date: date,
    source_refresh_at: datetime,
) -> dict:
    """All three reporting windows share ONE as-of and ONE classification."""
    if as_of_date < date(2026, 11, 10):
        raise ValueError("fixed November 5-9 cannot be current before November 10")
    if source_refresh_at.tzinfo is None or source_refresh_at.utcoffset() is None:
        raise ValueError("source timestamp must be timezone aware")
    by_day = {x.day: x for x in days}
    if len(by_day) != len(days):
        raise ValueError("duplicate research daily record")
    man = {x.decision_date: x for x in manager}
    if len(man) != len(manager):
        raise ValueError("duplicate Manager daily record")
    end = as_of_date - timedelta(days=1)
    periods = {
        "fixed_nov05_nov09": (FIXED_START, FIXED_END),
        "rolling_five_complete_dates": (end-timedelta(days=4), end),
        "cumulative_since_2026_10_27": (START_DAY, end),
    }
    output = {}
    for name, (start, last) in periods.items():
        wanted = [start+timedelta(days=i) for i in range((last-start).days+1)]
        if any(d not in by_day for d in wanted):
            raise ValueError("missing daily original/qualified evidence in "+name)
        original = _aggregate([
            (by_day[d].original, by_day[d].original_won, by_day[d].original_lost,
             by_day[d].original_void, by_day[d].original_pending)
            for d in wanted
        ])
        selected = _aggregate([
            (by_day[d].qualified, by_day[d].qualified_won, by_day[d].qualified_lost,
             by_day[d].qualified_void, by_day[d].qualified_pending)
            for d in wanted
        ])
        for k in ("picks", "won", "lost", "void", "pending"):
            if selected[k] > original[k]:
                raise ValueError("filtered picks not subset of Original: "+name)
        selected["coverage"] = (
            selected["picks"] / original["picks"]
            if original["picks"] else None
        )
        manager_rows = [man[d] for d in wanted if d in man]
        separate_manager = (
            _aggregate([
                (x.picks, x.wins, x.losses, x.voids, x.pending)
                for x in manager_rows
            ])
            if len(manager_rows) == len(wanted) else None
        )
        output[name] = {
            "start": start.isoformat(), "end": last.isoformat(),
            "status": (
                "MANAGER_COHORT_NOT_RECONCILED" if separate_manager is None
                else "PROVISIONAL" if original["pending"] or selected["pending"]
                or separate_manager["pending"] else "SETTLED"
            ),
            "original": original, "existing_qualified_ge70": selected,
            "manager_only_separate_population": separate_manager,
            "excluded_winners": original["won"]-selected["won"],
            "excluded_losses": original["lost"]-selected["lost"],
            "days": [
                {
                    "day": d.isoformat(),
                    "original": {
                        "picks": by_day[d].original,
                        "won": by_day[d].original_won,
                        "lost": by_day[d].original_lost,
                        "void": by_day[d].original_void,
                        "pending": by_day[d].original_pending,
                    },
                    "qualified": {
                        "picks": by_day[d].qualified,
                        "won": by_day[d].qualified_won,
                        "lost": by_day[d].qualified_lost,
                        "void": by_day[d].qualified_void,
                        "pending": by_day[d].qualified_pending,
                    },
                }
                for d in wanted
            ],
        }
    return {
        "source": "RAILWAY_PRODUCTION_POSTGRES_READ_ONLY_SINGLE_TRANSACTION",
        "data_refreshed_at": source_refresh_at.isoformat(),
        "timezone": "Asia/Manila",
        "original_population": "FIRST_ISSUED_ALIGNED_MODEL_PICKS",
        "qualified_policy": "PIT_CONFIDENCE_GE70_V1_RESEARCH_ONLY",
        "manager_population": "INDEPENDENT_FIRST_PRODUCTION_MANAGER_ONLY",
        "accuracy_denominator": "WON+LOST (void/pending excluded)",
        "a3_a4_forward_evidence": "NOT_AVAILABLE",
        "periods": output,
    }

