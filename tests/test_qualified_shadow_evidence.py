"""Qualified shadow compares preselected rules without rewriting production results."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from sports_edge_tennis.performance.aligned_historical_replay import ReplayPick
from sports_edge_tennis.performance.qualified_shadow_evidence import (
    _parse, evaluate, load, render, window,
)


def _pick(mid, day, result, probability):
    return ReplayPick(mid, day, "A1_A2_WIDE_SPREAD", Decimal(probability),
                      1, "HARD", result, 1, 1, Decimal("0.90"), Decimal("0.60"))


def test_frozen_five_day_results_improvement_is_pooled_not_daily_acceptance():
    # Synthetic test vectors matching verified live 2026-10-27..31 aggregate rows.
    # Real research computation runs against the canonical PostgreSQL source.
    vectors = (
        # qualified W,L,V,P and excluded W,L,V,P
        ((20, 3, 1, 0), (11, 5, 1, 0)),
        ((8, 1, 0, 0), (1, 2, 1, 0)),
        ((6, 4, 1, 0), (5, 3, 2, 0)),
        ((4, 3, 2, 0), (2, 1, 0, 0)),
        ((1, 2, 0, 3), (4, 3, 0, 1)),
    )
    rows = []
    mid = 1000
    for i, (qual, excl) in enumerate(vectors):
        d = date(2026, 10, 27) + timedelta(days=i)
        for group, conf in ((qual, "0.70"), (excl, "0.69")):
            for count, result in zip(group, ("WON", "LOST", "VOID", "PENDING")):
                for _ in range(count):
                    rows.append(_pick(mid, d, result, conf))
                    mid += 1
    days = evaluate(tuple(rows), as_of=datetime(2026, 10, 31, 18, tzinfo=UTC))
    assert [d.retained for d in days] == [24, 9, 11, 9, 6]
    assert sum(d.original for d in days) == 101
    assert sum(d.retained for d in days) == 59
    assert sum(d.won for d in days) == 39
    assert sum(d.lost for d in days) == 13
    assert sum(d.pending for d in days) == 3
    assert sum(d.void for d in days) == 4
    assert [d.status for d in days] == [
        "RESEARCH_MET73", "RESEARCH_MET73",
        "RESEARCH_BELOW73", "RESEARCH_BELOW73", "UNSETTLED",
    ]
    report = render(days)
    assert "pooled_win_rate_pct=75.0" in report[-1]
    assert "retained=59" in report[-1]
    assert "daily_passed=2/5" in report[-1]
    assert "acceptance=UNCHANGED_ORIGINAL_ALIGNED_ONLY" in report[-1]


def test_pit_probability_threshold_exact_and_no_reclassification():
    original = (_pick(1, date(2026, 10, 30), "LOST", "0.70"),
                _pick(2, date(2026, 10, 30), "WON", "0.699999"),
                _pick(3, date(2026, 10, 30), "VOID", "0.74"))
    rows = evaluate(original, as_of=datetime(2026, 11, 1, 3, tzinfo=UTC))
    d = next(x for x in rows if x.day == date(2026, 10, 30))
    assert (d.original, d.retained, d.won, d.lost, d.void, d.missed_winners) == (3, 2, 0, 1, 1, 1)
    assert d.status == "TOO_FEW_GRADED"


def test_window_rolls_at_manila_midnight():
    assert window(datetime(2026, 10, 31, 15, 59, tzinfo=UTC)) == (
        date(2026, 10, 27), date(2026, 10, 31))
    assert window(datetime(2026, 10, 31, 16, tzinfo=UTC)) == (
        date(2026, 10, 27), date(2026, 10, 31))
    assert window(datetime(2026, 11, 1, 16, tzinfo=UTC)) == (
        date(2026, 10, 28), date(2026, 11, 1))
    with pytest.raises(ValueError):
        window(datetime(2026, 11, 1))


def test_bad_original_rows_fail_closed():
    raw = [(1, date(2026, 10, 27), "MANAGER_CONSENSUS", Decimal("0.70"), 1, "HARD",
            1, 1, Decimal("0.7"), Decimal("0.6"), "WON")]
    assert _parse(raw)[0].probability == Decimal("0.70")
    with pytest.raises(ValueError, match="duplicate"):
        _parse(raw + raw)
    for field, bad in ((3, Decimal("NaN")), (4, 3), (10, "UNKNOWN")):
        mutated = list(raw[0])
        mutated[field] = bad
        with pytest.raises(ValueError):
            _parse([tuple(mutated)])


def test_shadow_query_works_on_empty_postgres(db):
    days = load(db, as_of=datetime(2026, 10, 31, 18, tzinfo=UTC))
    assert len(days) == 5
    assert all(d.status == "NO_ORIGINAL" for d in days)
    assert all(d.original == 0 for d in days)
