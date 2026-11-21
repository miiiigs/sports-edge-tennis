"""Guard against outcome cherry-picking in published five-day Tennis findings."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research.tennis_a3_a4_guide_20261110.analyze_production_snapshot import (
    analyze_snapshot,
)


def _snapshot():
    path = (
        Path(__file__).parents[1]
        / "research/tennis_a3_a4_guide_20261110"
        / "production_snapshot_20261110_1805PHT.json"
    )
    return json.loads(path.read_text("utf-8"))


def test_real_frozen_production_aggregates_keep_original_and_shadow_separate():
    result = analyze_snapshot(_snapshot())
    assert result["status"] == "OBSERVATIONAL_BASELINE_ONLY_NOT_A4_V3_VALIDATION"
    assert result["source_as_of"] == "2026-11-10T10:05:32.463946630Z"
    assert result["period"] == {"start": "2026-11-05", "end": "2026-11-09"}
    assert result["totals"]["original"] == {
        "picks": 124, "won": 63, "lost": 41, "void": 4,
        "pending": 16, "graded": 104,
    }
    assert result["totals"]["confidence70"] == {
        "picks": 56, "won": 33, "lost": 14, "void": 2,
        "pending": 7, "graded": 47,
    }
    assert result["totals"]["manager_only"] == {
        "picks": 69, "won": 35, "lost": 25, "void": 2,
        "pending": 7, "graded": 60,
    }
    assert result["manager_only_win_rate"] == pytest.approx(35/60)
    assert result["manager_only_cohort_independent"] is True
    assert result["avoided_losses"] == 27
    assert result["missed_winners"] == 30
    assert result["confidence70_selection_coverage"] == pytest.approx(56/124)
    assert result["original_win_rate"] == pytest.approx(63/104)
    assert result["confidence70_win_rate"] == pytest.approx(33/47)
    assert result["original_win_rate_wilson95"][0] == pytest.approx(.51, abs=.01)
    assert result["original_win_rate_wilson95"][1] == pytest.approx(.694, abs=.01)
    assert result["confidence70_win_rate_wilson95"][0] == pytest.approx(.56, abs=.01)
    assert result["confidence70_win_rate_wilson95"][1] == pytest.approx(.813, abs=.01)
    assert (result["confidence70_win_rate_wilson95"][0]
            < result["original_win_rate_wilson95"][1])
    assert result["graded_accuracy_gap_percentage_points"] == pytest.approx(
        100*(33/47-63/104)
    )
    assert result["fully_settled_days_met_73"] == {
        "original": 1, "confidence70": 1, "manager_only": 0,
    }


def test_unsettled_days_not_counted_as_met_and_show_possible_bounds():
    data = analyze_snapshot(_snapshot())
    days = {row["date"]: row for row in data["daily"]}
    assert days["2026-11-06"]["original"]["research_day_status"] == "MET_73"
    assert days["2026-11-07"]["confidence70"]["research_day_status"] == "MET_73"
    assert days["2026-11-06"]["confidence70"]["research_day_status"] == "INSUFFICIENT_GRADED"
    assert days["2026-11-08"]["confidence70"]["research_day_status"] == "UNSETTLED_MAX_BELOW_73"
    assert days["2026-11-09"]["original"]["research_day_status"] == "UNSETTLED_MAX_BELOW_73"
    assert days["2026-11-09"]["confidence70"]["research_day_status"] == "UNSETTLED_OUTCOME_DEPENDENT"
    assert days["2026-11-09"]["confidence70"]["wins_needed_if_all_pending_grade"] == 4
    assert days["2026-11-09"]["confidence70"]["best_if_all_pending_win"] == pytest.approx(21/26)
    assert days["2026-11-09"]["confidence70"]["worst_if_all_pending_lose"] == pytest.approx(15/26)
    assert days["2026-11-09"]["original"]["best_if_all_pending_win"] == pytest.approx(41/58)


def test_rejects_changed_original_math_missing_day_or_shadow_outcome_inflation():
    payload = _snapshot()
    flawed = deepcopy(payload)
    flawed["daily"]["2026-11-09"]["original"]["pending"] -= 1
    with pytest.raises(ValueError, match="total"):
        analyze_snapshot(flawed)
    flawed = deepcopy(payload)
    del flawed["daily"]["2026-11-07"]
    with pytest.raises(ValueError, match="every calendar day"):
        analyze_snapshot(flawed)
    flawed = deepcopy(payload)
    flawed["daily"]["2026-11-07"]["confidence70"]["won"] += 1
    flawed["daily"]["2026-11-07"]["confidence70"]["lost"] -= 1
    with pytest.raises(ValueError, match="report rate"):
        analyze_snapshot(flawed)
    flawed = deepcopy(payload)
    flawed["daily"]["2026-11-09"]["confidence70"]["won"] = 30
    flawed["daily"]["2026-11-09"]["confidence70"]["lost"] = 0
    flawed["daily"]["2026-11-09"]["confidence70"]["graded"] = 30
    with pytest.raises(ValueError):
        analyze_snapshot(flawed)


def test_nov10_2205_updated_ledger_preserves_all_populations_and_pending_truth():
    """A new later as-of is a new frozen snapshot, not silent old-score rewriting."""
    file = (Path(__file__).parents[1] /
            "research/tennis_a3_a4_guide_20261110" /
            "production_snapshot_20261110_2205PHT.json")
    data = analyze_snapshot(json.loads(file.read_text("utf-8")))
    assert data["source_as_of"] == "2026-11-10T14:05:38.057828108Z"
    assert data["totals"]["original"] == {
        "picks": 124, "won": 64, "lost": 41, "void": 4,
        "pending": 15, "graded": 105,
    }
    assert data["totals"]["confidence70"] == {
        "picks": 56, "won": 34, "lost": 14, "void": 2,
        "pending": 6, "graded": 48,
    }
    assert data["totals"]["manager_only"] == {
        "picks": 69, "won": 35, "lost": 27, "void": 2,
        "pending": 5, "graded": 62,
    }
    assert data["original_win_rate"] == pytest.approx(64/105)
    assert data["confidence70_win_rate"] == pytest.approx(34/48)
    assert data["manager_only_win_rate"] == pytest.approx(35/62)
    assert data["missed_winners"] == 30
    assert data["avoided_losses"] == 27
    assert data["fully_settled_days_met_73"] == {
        "original": 1, "confidence70": 1, "manager_only": 0,
    }
    last = data["daily"][-1]
    assert last["confidence70"]["pending"] == 5
    assert last["confidence70"]["worst_if_all_pending_lose"] == pytest.approx(16/26)
    assert last["confidence70"]["best_if_all_pending_win"] == pytest.approx(21/26)
    assert last["confidence70"]["research_day_status"] == "UNSETTLED_OUTCOME_DEPENDENT"
