"""Synthetic verification of rank-gap sign, ECE, and distinct agent/population rows."""
from research.tennis_pit_rank_evidence_20261022.cohort_analysis import (
    analyze, ece, gap_band
)


def test_gap_sign_and_boundaries():
    assert gap_band(-201) == "OPPONENT_BETTER_201+"
    assert gap_band(-1) == "OPPONENT_BETTER_1-200"
    assert gap_band(0) == "EQUAL"
    assert gap_band(1) == "SELECTED_BETTER_1-200"
    assert gap_band(201) == "SELECTED_BETTER_201+"
    assert gap_band(None) == "MISSING_OR_UNRANKED"


def test_ece_and_cohort_not_scoring_void():
    rows = [
        {"population": "MANAGER", "manager_side": 1,
         "selected_rank": 451, "opponent_rank": 31,
         "selected_rank_status": "VERIFIED", "opponent_rank_status": "VERIFIED",
         "selected_rank_band": "401-600",
         "opponent_rank_band": "1-100", "rank_gap_opponent_minus_selected": -420,
         "surface": "CLAY", "tournament_tier": "CHALLENGER",
         "outcome_status": "COMPLETED", "winner_side": 1,
         "manager_side": 1, "manager_p": .8,
         "a1_side": 2, "a1_p": .7, "a2_side": 1, "a2_p": .6},
        {"population": "MANAGER", "manager_side": 1,
         "selected_rank": 451, "opponent_rank": 31,
         "selected_rank_status": "VERIFIED", "opponent_rank_status": "VERIFIED",
         "selected_rank_band": "401-600",
         "opponent_rank_band": "1-100", "rank_gap_opponent_minus_selected": -420,
         "surface": "CLAY", "tournament_tier": "CHALLENGER",
         "outcome_status": "WALKOVER", "winner_side": None,
         "manager_side": 1, "manager_p": .9,
         "a1_side": 1, "a1_p": .9, "a2_side": 1, "a2_p": .9},
    ]
    report = analyze(rows)
    selected = [x for x in report if x["axis"] == "selected_rank"]
    assert len(selected) == 3
    expected = {"manager": "401-600", "a1": "1-100", "a2": "401-600"}
    assert {x["agent"]: x["cohort"] for x in selected} == expected
    assert all(x["eligible"] == 2 and x["settled_with_prediction"] == 1 for x in selected)
    assert abs(next(x for x in selected if x["agent"] == "manager")["ece_10"] - .2) < 1e-10
    assert next(x for x in selected if x["agent"] == "a1")["losses"] == 1
    gaps = {x["agent"]: x["cohort"] for x in report if x["axis"] == "rank_gap"}
    assert gaps == {
        "manager": "OPPONENT_BETTER_201+",
        "a1": "SELECTED_BETTER_201+",
        "a2": "OPPONENT_BETTER_201+",
    }


def test_empty_calibration_is_none():
    assert ece([], "manager") is None
