"""Synthetic-only regression of point-in-time ranking audit invariants."""
from datetime import UTC, datetime, timedelta

from research.tennis_pit_rank_evidence_20261022.contract import (
    as_of_rank, first_qualified, rank_band, score_completed, wilson_interval
)


def test_ranking_band_boundaries():
    assert [rank_band(x) for x in (1, 100, 101, 200, 201, 400, 401, 600, 601, None)] == [
        "1-100", "1-100", "101-200", "101-200", "201-400",
        "201-400", "401-600", "401-600", "601+", "MISSING_OR_UNRANKED"
    ]


def test_historical_source_must_precede_prediction():
    picked = datetime(2026, 10, 19, 8, tzinfo=UTC)
    good = dict(rank=451, rank_as_of=picked-timedelta(days=7),
                source_published_at=picked-timedelta(days=5),
                verified_official_archive=True)
    future = dict(good, rank=50, source_published_at=picked+timedelta(seconds=1))
    unverified = dict(good, rank=8, verified_official_archive=False)
    assert as_of_rank(picked, [good, future, unverified]) == 451
    assert as_of_rank(picked, [future, unverified]) is None
    assert rank_band(as_of_rank(picked, [good, future])) == "401-600"


def test_first_qualified_preserves_population():
    day = datetime(2026, 10, 19, 8, tzinfo=UTC)
    rows = [
        dict(population="MANAGER", match_id=1, pick_id=10, picked_at=day),
        dict(population="MANAGER", match_id=1, pick_id=11, picked_at=day+timedelta(hours=1)),
        dict(population="A1_A2_TRACKING", match_id=1, pick_id=12, picked_at=day),
    ]
    qualified = first_qualified(rows)
    assert len(qualified) == 2
    assert [r["pick_id"] for r in qualified] == [10, 12]


def test_agent_direction_and_void_stay_separate():
    rows = [
        dict(outcome_status="COMPLETED", winner_side=1, manager_side=1, manager_p=.75,
             a1_side=1, a1_p=.72, a2_side=2, a2_p=.66),
        dict(outcome_status="WALKOVER", winner_side=1, manager_side=1, manager_p=.88,
             a1_side=1, a1_p=.85, a2_side=1, a2_p=.82),
    ]
    manager = score_completed(rows, selected_side_key="manager_side", probability_key="manager_p")
    a2 = score_completed(rows, selected_side_key="a2_side", probability_key="a2_p")
    assert (manager["eligible"], manager["settled_with_prediction"], manager["wins"]) == (2, 1, 1)
    assert (a2["losses"], a2["wins"]) == (1, 0)
    assert manager["exploratory_only"]


def test_small_sample_interval_is_wide():
    lo, hi = wilson_interval(1, 1)
    assert lo < .3 and hi == 1
    assert wilson_interval(0, 0) is None
