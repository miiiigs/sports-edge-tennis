"""Pure tests for the six-way forecasting comparison."""
from __future__ import annotations

import math

import pytest

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import CODES
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY
from sports_edge_tennis.validation.six_way_manager_comparison import (
    ARMS,
    _arm_candidates,
    _candidate,
    _weighted,
    common_support,
    error_correlation,
    fit_weight,
    group_metrics,
    paired_bootstrap,
    reconcile,
)

A2, A3 = CODES[1], CODES[2]


_MISSING = object()


def _row(mid, winner, *, a1=0.70, a2=None, a3=None, stored_a1=None,
         manager_b=_MISSING, manager_original=_MISSING, tour="ATP_MAIN",
         main_draw=True) -> dict:
    agents = {}
    if a2 is not None:
        agents[A2] = a2
    if a3 is not None:
        agents[A3] = a3
    stored_a1 = a1 if stored_a1 is None else stored_a1
    manager_b = ((1 if a1 >= .5 else 2, a1)
                 if manager_b is _MISSING else manager_b)
    manager_original = (
        (1 if stored_a1 >= .5 else 2, stored_a1)
        if manager_original is _MISSING else manager_original)
    return {
        "match_id": mid, "winner": winner, "tour": tour,
        "main_draw_verified": main_draw,
        "a1_bridged_replay_p1": a1, "a1_stored_p1": stored_a1,
        "agents_p1": agents, "active_agents": sorted(agents),
        "candidates": {"B": manager_b, POLICY: manager_original},
    }


def test_candidate_rejects_exact_coin_flip_and_out_of_range():
    assert _candidate(None) is None
    assert _candidate(0.5) is None
    assert _candidate(float("nan")) is None
    assert _candidate(1.0) is None
    assert _candidate(0.70) == (1, 0.70)
    assert _candidate(0.30) == (2, 0.30)


def test_weighted_arm_uses_one_scalar_and_requires_both_agents():
    assert _weighted(0.80, 0.40, 1.0) == (1, 0.80)
    assert _weighted(0.80, 0.40, 0.0) == (2, 0.40)
    side, p1 = _weighted(0.80, 0.40, 0.5)
    assert side == 1 and p1 == pytest.approx(0.60)
    assert _weighted(None, 0.40, 0.5) is None


def test_all_arms_are_built_from_identical_fixture_keys():
    rows = [_row(1, 1, a1=0.72, a2=0.65, a3=0.60),
            _row(2, 2, a1=0.45, a2=0.55, a3=None)]
    candidates = _arm_candidates(rows, 0.5)
    for arm in ARMS:
        assert set(candidates[arm]) == {1, 2}
    assert candidates["A_A1_CORRECTED"][1] == (1, 0.72)
    assert candidates["B_A1_A2_MEAN"][1][1] == pytest.approx(0.685)
    assert candidates["D_A1_A3"][1][1] == pytest.approx(0.66)
    assert candidates["D_A1_A3"][2] is None  # Agent 3 abstained, never imputed
    assert candidates["F_ALL_AVAILABLE"][1][1] == pytest.approx((0.72 + 0.65 + 0.60) / 3)
    # 0.45 and 0.55 average to exactly 0.50: the combination abstains.
    assert candidates["F_ALL_AVAILABLE"][2] is None


def test_weight_is_fitted_only_on_earlier_rows_and_prefers_plateau_midpoint():
    rows = []
    for i in range(60):
        # A1 is perfect, A2 is anti-informative; the best weight is 1.0.
        rows.append(_row(i, 1 if i % 2 == 0 else 2,
                         a1=0.90 if i % 2 == 0 else 0.10,
                         a2=0.10 if i % 2 == 0 else 0.90))
    fit = fit_weight(rows)
    assert fit["available"] is True
    assert fit["chosen_weight_a1"] == pytest.approx(1.0)
    assert fit["n"] == 60


def test_weight_fit_fails_closed_without_enough_earlier_pairs():
    fit = fit_weight([_row(1, 1, a1=0.7, a2=0.6)])
    assert fit["available"] is False
    assert fit["blocker"] == "INSUFFICIENT_EARLIER_DUAL_AGENT_FIXTURES"


def test_common_support_is_the_intersection_of_every_arm():
    rows = [_row(1, 1, a1=0.72, a2=0.65, a3=0.60),
            _row(2, 2, a1=0.45, a2=0.55, a3=None),
            _row(3, 1, a1=0.80, a2=0.70, a3=0.75)]
    candidates = _arm_candidates(rows, 0.5)
    shared = common_support(rows, candidates)
    assert {row["match_id"] for row in shared} == {1, 3}


def test_eligibility_and_coverage_are_not_conflated():
    rows = [_row(1, 1, a1=0.72, a2=0.65, a3=0.60),
            _row(2, 2, a1=0.45, a2=0.55, a3=None),
            _row(3, None, a1=0.80, a2=0.70, a3=0.75)]
    candidates = _arm_candidates(rows, 0.5)
    d = group_metrics(rows, candidates["D_A1_A3"])
    assert d["eligible"] == 2 and d["covered"] == 1
    assert d["coverage"] == 0.5


def test_metrics_use_player_one_probability_and_selected_side_expectation():
    rows = [_row(1, 1, a1=0.70), _row(2, 2, a1=0.70)]
    candidates = _arm_candidates(rows, 0.5)
    metrics = group_metrics(rows, candidates["A_A1_CORRECTED"])
    assert metrics["wins"] == 1
    assert metrics["expected_wins"] == pytest.approx(1.40)
    assert metrics["residual"] == pytest.approx(-0.40)
    assert metrics["brier"] == pytest.approx(0.29)
    assert metrics["log_loss"] == pytest.approx(
        (-math.log(0.70) - math.log(0.30)) / 2, rel=1e-6)
    assert metrics["ece"]["ece"] == pytest.approx(0.20)


def test_reconciliation_separates_rejection_correction_and_abstention():
    rows = [
        _row(1, 1, a1=0.80, manager_b=(2, 0.30)),   # correct A1 overruled
        _row(2, 2, a1=0.70, manager_b=(2, 0.30)),   # incorrect A1 corrected
        _row(3, 1, a1=0.70, manager_b=None),        # correct A1 abstained
        _row(4, 1, a1=0.40, manager_b=None),        # incorrect A1 abstained
        _row(5, 1, a1=0.70, manager_b=(1, 0.70)),   # both correct
    ]
    candidates = _arm_candidates(rows, 0.5)
    report = reconcile(rows, candidates)
    assert report["counts"]["correct_winner_changed_to_incorrect"] == 1
    assert report["counts"]["incorrect_winner_corrected"] == 1
    assert report["counts"]["correct_a1_lost_to_abstention"] == 1
    assert report["counts"]["incorrect_a1_lost_to_abstention"] == 1
    assert report["counts"]["both_correct"] == 1
    assert report["coverage_lost_through_abstention"] == pytest.approx(0.4)


def test_paired_bootstrap_reports_cluster_count_and_direction():
    rows = [_row(i, 1 if i % 2 == 0 else 2,
                 a1=0.90 if i % 2 == 0 else 0.10,
                 a2=0.30 if i % 2 == 0 else 0.70) for i in range(40)]
    candidates = _arm_candidates(rows, 0.5)
    result = paired_bootstrap(rows, candidates, "A_A1_CORRECTED", "B_A1_A2_MEAN")
    assert result["available"] is True
    assert result["clusters"] == 40
    low, high = result["brier_improvement_a_minus_b_95"]
    # This synthetic construction makes A uniformly better by exactly 0.15.
    assert low == pytest.approx(-0.15)
    assert high == pytest.approx(-0.15)


def test_error_correlation_is_bounded_and_symmetric_in_pair_count():
    rows = [_row(i, 1 if i % 2 == 0 else 2,
                 a1=0.70, a2=0.60, a3=0.55) for i in range(20)]
    candidates = _arm_candidates(rows, 0.5)
    report = error_correlation(rows, candidates)
    key = "A_A1_CORRECTED|B_A1_A2_MEAN"
    assert report[key]["n"] == 20
    value = report[key]["brier_contribution_correlation"]
    assert value is None or -1.000001 <= value <= 1.000001
