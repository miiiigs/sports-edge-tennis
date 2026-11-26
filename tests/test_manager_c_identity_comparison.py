"""Pure tests for the fixed Manager C A-versus-B identity comparison."""
from __future__ import annotations

import pytest

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import CODES
from sports_edge_tennis.validation.manager_c_identity_comparison import (
    _candidate,
    _equal_count,
    _group_metrics,
    _p1,
    _select,
    _set_report,
)
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY


def _row(match_id: int, winner: int, a: tuple[int, float],
         b: tuple[int, float] | None = None) -> dict:
    return {
        "match_id": match_id, "winner": winner,
        "candidates": {POLICY: a, "B": b if b is not None else a},
    }


def test_p1_reconstructs_player_one_probability_from_selected_side():
    assert _p1(1, 0.73) == pytest.approx(0.73)
    assert _p1(2, 0.73) == pytest.approx(0.27)
    assert _p1(None, 0.5) is None


def test_candidate_changes_only_when_a1_is_replaced():
    active = {CODES[0]: (1, 0.90), CODES[1]: (1, 0.50), CODES[2]: (1, 0.50)}
    # POLICY C is the mean of the three p1 values = 0.633... (side 1).
    assert _candidate(active, None)[0] == 1
    changed = _candidate(active, 0.10)
    assert changed[0] == 2
    assert changed[1] == pytest.approx((0.10 + 0.50 + 0.50) / 3)


def test_frozen_threshold_selection_is_inclusive():
    rows = [_row(1, 1, (1, 0.70)), _row(2, 1, (1, 0.62))]
    assert {r["match_id"] for r in _select(rows, POLICY, 0.66)} == {1}
    assert {r["match_id"] for r in _select(rows, POLICY, 0.62)} == {1, 2}


def test_equal_count_uses_confidence_then_match_id():
    rows = [_row(1, 1, (1, 0.70)), _row(2, 1, (1, 0.80)), _row(3, 1, (1, 0.60))]
    assert [r["match_id"] for r in _equal_count(rows, POLICY, 2)] == [2, 1]


def test_set_report_separates_removed_added_and_corrected():
    a = [_row(1, 1, (2, 0.30)), _row(2, 2, (1, 0.72))]
    b = [_row(1, 1, (1, 0.70)), _row(3, 1, (1, 0.65))]
    # Match 1 flips from the wrong side 2 to the correct side 1 -> corrected.
    report = _set_report(a, b, arm_a=POLICY, arm_b="B")
    assert report["shared"] == 1
    assert report["removed"] == 1
    assert report["added"] == 1
    assert report["shared_side_flips"] == 1
    assert report["corrected_winner_predictions"] == 1
    assert report["newly_incorrect_winner_predictions"] == 0


def test_group_metrics_use_selected_side_expectation():
    rows = [_row(1, 1, (1, 0.70)), _row(2, 2, (1, 0.70))]
    metrics = _group_metrics(rows, POLICY)
    assert metrics["wins"] == 1
    assert metrics["expected_wins"] == pytest.approx(1.40)
    assert metrics["residual"] == pytest.approx(-0.40)
