from __future__ import annotations

import json
from pathlib import Path

import pytest

from sports_edge_tennis.validation.a1_selective_performance import (
    apply_frozen_threshold,
    confidence,
    coverage_count,
    day_block_bootstrap,
    development_threshold,
    evaluate_coverage,
    evaluate_population,
    four_way,
    load_population_csv,
    rank_rows,
    recovery_summary,
    risk_coverage,
    selection_side,
    wilson_interval,
)

ROOT = Path(__file__).resolve().parents[1]
POPULATION = ROOT / "docs" / "research" / "evidence" / "a1_selective_population_20261126.csv"
EVIDENCE = ROOT / "docs" / "research" / "evidence" / "a1_selective_performance_20261126.json"


def row(prediction_id, p_original, p_corrected, winner, *, tour="CHALLENGER", date="2026-09-15", **extra):
    base = {
        "prediction_id": prediction_id,
        "match_id": 1000 + prediction_id,
        "p1_original": p_original,
        "p1_corrected": p_corrected,
        "winner_side": winner,
        "y": 1 if winner == 1 else 0,
        "tour": tour,
        "competition_tier": "CHALLENGER",
        "date": date,
    }
    base.update(extra)
    return base


def test_probability_conventions():
    assert selection_side(0.5) == 1
    assert selection_side(0.499999) == 2
    assert confidence(0.2) == 0.8
    assert confidence(0.8) == 0.8
    with pytest.raises(ValueError):
        confidence(float("nan"))


def test_coverage_count_uses_floor_and_full_endpoint():
    assert coverage_count(3223, 0.10) == 322
    assert coverage_count(3223, 0.40) == 1289
    assert coverage_count(3223, 1.00) == 3223
    assert coverage_count(0, 0.50) == 0
    with pytest.raises(ValueError):
        coverage_count(10, 1.1)


def test_ranking_is_deterministic_and_outcome_independent():
    rows = [
        row(3, 0.60, 0.60, 1),
        row(1, 0.60, 0.60, 2),
        row(2, 0.90, 0.61, 1),
    ]
    ranked = [item["prediction_id"] for item in rank_rows(rows, "p1_original")]
    assert ranked == [2, 1, 3]
    # 1 and 3 tie at 0.6; prediction_id breaks the tie, not the winner.
    reversed_rows = list(reversed(rows))
    assert [item["prediction_id"] for item in rank_rows(reversed_rows, "p1_original")] == ranked


def test_wilson_interval_is_symmetric_for_half():
    low, high = wilson_interval(50, 100)
    assert abs((low + high) / 2 - 0.5) < 1e-12
    assert wilson_interval(0, 0) is None


def test_coverage_metrics_and_marginal_accuracy():
    rows = [
        row(1, 0.90, 0.90, 1),
        row(2, 0.80, 0.80, 1),
        row(3, 0.70, 0.70, 2),
        row(4, 0.60, 0.60, 1),
    ]
    result = evaluate_coverage(rows, "p1_original", points=(0.50, 1.00), bootstrap=False)
    first = result["points"]["0.50"]
    assert first["selection_count"] == 2
    assert first["wins"] == 2
    assert first["accuracy"] == 1.0
    assert first["marginal"]["selection_count"] == 2
    assert first["marginal"]["accuracy"] == 1.0
    full = result["points"]["1.00"]
    assert full["selection_count"] == 4
    assert full["wins"] == 3
    assert full["marginal"] == {"selection_count": 2, "wins": 1, "losses": 1, "accuracy": 0.5}
    assert full["mean_confidence"] == pytest.approx((0.9 + 0.8 + 0.7 + 0.6) / 4)
    assert full["expected_minus_observed_wins"] == pytest.approx((0.9 + 0.8 + 0.7 + 0.6) - 3)


def test_aurc_and_augrc_no_ties():
    rows = [
        row(1, 0.90, 0.90, 1),
        row(2, 0.80, 0.80, 2),
        row(3, 0.70, 0.70, 1),
        row(4, 0.60, 0.60, 2),
    ]
    result = risk_coverage(rows, "p1_original")
    assert result["n"] == 4
    assert result["errors"] == 2
    assert result["aurc"] == pytest.approx((0 + 1 / 2 + 1 / 3 + 2 / 4) / 4)
    assert result["augrc_paper"] == pytest.approx((3 - 0.5) / 16 + (1 - 0.5) / 16)
    assert result["augrc_left"] == pytest.approx(3 / 16 + 1 / 16)
    assert result["optimal_augrc_binary_ranking"] == pytest.approx(0.5**2 / 2)


def test_aurc_and_augrc_average_equal_confidence_ties():
    rows = [
        row(1, 0.90, 0.90, 1),
        row(2, 0.80, 0.80, 2),
        row(3, 0.80, 0.80, 1),
        row(4, 0.70, 0.70, 2),
    ]
    result = risk_coverage(rows, "p1_original")
    # Group [1,3) has one error in two rows; expected cumulative error is
    # 0.5 at rank 2 and 1.0 at rank 3.
    assert result["aurc"] == pytest.approx((0 + 0.5 / 2 + 1.0 / 3 + 2 / 4) / 4)
    # Paper trapezoid: average rank 1.5 -> (4 - 1.5 - 0.5) / 16.
    assert result["augrc_paper"] == pytest.approx((4 - 1.5 - 0.5) / 16 + (1 - 0.5) / 16)
    assert result["augrc_left"] == pytest.approx((4 - 1.5) / 16 + 1 / 16)


def test_four_way_algebraic_identity():
    rows = [
        row(1, 0.90, 0.90, 1),
        row(2, 0.80, 0.15, 1),
        row(3, 0.70, 0.05, 1),
        row(4, 0.40, 0.80, 1),
        row(5, 0.25, 0.70, 1),
    ]
    result = four_way(rows, k=3)
    assert result["wins"] == {"A": 2, "B": 2, "C": 3, "D": 1}
    assert result["order_averaged_ranking_effect"] == pytest.approx(0.0)
    assert result["order_averaged_winner_effect"] == pytest.approx(-1.0)
    assert result["interaction"] == -2
    assert (
        result["order_averaged_ranking_effect"]
        + result["order_averaged_winner_effect"]
        == pytest.approx(result["wins"]["D"] - result["wins"]["A"])
    )


def test_frozen_threshold_ignores_holdout_outcomes():
    development = [
        row(1, 0.90, 0.90, 1, date="2026-09-10"),
        row(2, 0.80, 0.80, 2, date="2026-09-11"),
        row(3, 0.70, 0.70, 1, date="2026-09-12"),
        row(4, 0.60, 0.60, 2, date="2026-09-13"),
    ]
    threshold = development_threshold(development, 0.50, "p1_original")["threshold"]
    assert threshold == 0.8
    holdout_a = [row(5, 0.85, 0.85, 1, date="2026-10-01"), row(6, 0.55, 0.55, 2, date="2026-10-02")]
    holdout_b = [row(5, 0.85, 0.85, 2, date="2026-10-01"), row(6, 0.55, 0.55, 1, date="2026-10-02")]
    selected_a = apply_frozen_threshold(holdout_a, threshold, "p1_original")
    selected_b = apply_frozen_threshold(holdout_b, threshold, "p1_original")
    assert [r["prediction_id"] for r in selected_a] == [5]
    assert [r["prediction_id"] for r in selected_b] == [5]


def test_day_block_bootstrap_is_deterministic():
    rows = [row(i, 0.5 + i / 100, 0.5 + i / 100, 1 if i % 2 else 2, date=f"2026-09-{10 + i}") for i in range(1, 10)]
    first = day_block_bootstrap(rows, "p1_original", reps=50, seed=7)
    second = day_block_bootstrap(rows, "p1_original", reps=50, seed=7)
    assert first == second
    assert first["accuracy"][0] <= first["accuracy"][1]


def test_recovery_classes_are_mutually_exclusive():
    rows = [
        row(
            1, 0.40, 0.55, 1,
            resolution_p1_bridged=True,
            overall_matches_original_p1=0,
            overall_matches_corrected_p1=10,
            overall_matches_original_p2=0,
            overall_matches_corrected_p2=10,
            overall_rating_original_p1=1500,
            overall_rating_corrected_p1=1600,
            overall_rating_original_p2=1500,
            overall_rating_corrected_p2=1400,
        ),
        row(
            2, 0.40, 0.45, 2,
            resolution_p1_bridged=False,
            overall_matches_original_p1=50,
            overall_matches_corrected_p1=50,
            overall_matches_original_p2=50,
            overall_matches_corrected_p2=50,
            overall_rating_original_p1=1500,
            overall_rating_corrected_p1=1550,
            overall_rating_original_p2=1500,
            overall_rating_corrected_p2=1450,
        ),
    ]
    result = recovery_summary(rows)
    assert result["direct"]["n"] == 1
    assert result["indirect_only"]["n"] == 1
    assert result["mutually_exclusive"] is True


def test_settled_artifact_reproduces_headline_counts():
    rows = load_population_csv(str(POPULATION))
    assert len(rows) == 3223
    report = evaluate_population(rows, bootstrap=False)
    assert all(report["validation"].values()), report["validation"]
    assert report["four_way_top800"]["wins"] == {"A": 596, "B": 589, "C": 646, "D": 655}
    assert report["cohort_comparison_top800"]["shared"]["n"] == 509
    assert report["cohort_comparison_top800"]["removed"]["n"] == 291
    assert report["cohort_comparison_top800"]["added"]["n"] == 291
    assert report["cohort_comparison_top800"]["shared"]["side_changes"] == 0
    assert report["cohort_comparison_top800"]["shared"]["probability_changes"] == 495
    assert report["recovery"]["direct"]["n"] == 3078
    assert report["recovery"]["indirect_only"]["n"] == 72
    assert report["provenance"]["method_gap_vs_slots"] == 0
    assert report["provenance"]["player_slots"] == 6446

    corrected_40 = report["coverage_experiment"]["corrected"]["points"]["0.40"]
    corrected_45 = report["coverage_experiment"]["corrected"]["points"]["0.45"]
    corrected_50 = report["coverage_experiment"]["corrected"]["points"]["0.50"]
    assert corrected_40["selection_count"] == 1289
    assert corrected_40["accuracy"] == pytest.approx(0.7758, abs=1e-4)
    assert corrected_45["accuracy"] == pytest.approx(0.7634, abs=1e-4)
    assert corrected_50["accuracy"] == pytest.approx(0.7430, abs=1e-4)

    assert report["risk_coverage"]["corrected"]["aurc"] == pytest.approx(0.24055, abs=1e-4)
    assert report["risk_coverage"]["original"]["aurc"] == pytest.approx(0.29342, abs=1e-4)
    assert report["risk_coverage"]["corrected"]["augrc_paper"] == pytest.approx(0.14124, abs=1e-4)


def test_committed_evidence_matches_the_recomputation():
    rows = load_population_csv(str(POPULATION))
    report = evaluate_population(rows, bootstrap=False)
    committed = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert committed["four_way_top800"]["wins"] == report["four_way_top800"]["wins"]
    assert committed["risk_coverage"]["corrected"]["aurc"] == pytest.approx(
        report["risk_coverage"]["corrected"]["aurc"]
    )
    assert committed["strict_prematch_sensitivity"]["excluded"] == 21
    assert committed["strict_prematch_sensitivity"]["corrected"]["points"]["0.40"]["accuracy"] == pytest.approx(
        report["coverage_experiment"]["corrected"]["points"]["0.40"]["accuracy"], abs=0.01
    )
