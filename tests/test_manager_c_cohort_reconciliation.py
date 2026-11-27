from __future__ import annotations

from sports_edge_tennis.validation.manager_c_cohort_reconciliation import (
    PUBLISHED_COUNTS,
    arm_metrics,
    forecast_average_diagnostics,
    mcnemar_exact,
    paired_side_changes,
    reconcile_cohorts,
)


def _row(match_id: int, winner: int = 1, **overrides) -> dict:
    row = {
        "match_id": match_id,
        "winner": winner,
        "a1_p1": 0.8,
        "a2_p1": 0.7,
        "a1_original_p1": 0.75,
        "manager_corrected_p1": None,
        "manager_original_p1": None,
    }
    row.update(overrides)
    return row


def test_common_support_difference_is_the_original_manager_arm():
    rows = []
    for match_id in range(1, 11):
        rows.append(_row(match_id))
    # Corrected Manager selects 1-4; original Manager selects 1-3.
    for match_id in (1, 2, 3, 4):
        rows[match_id - 1]["manager_corrected_p1"] = 0.72
    for match_id in (1, 2, 3):
        rows[match_id - 1]["manager_original_p1"] = 0.71

    result = reconcile_cohorts(
        rows,
        published={
            "settled_eligible": 10,
            "manager_corrected_retained": 4,
            "manager_abstained": 6,
            "common_support": 3,
            "manager_original_retained": 3,
        },
    )
    assert result["measured"]["manager_corrected_retained"] == 4
    assert result["measured"]["common_support"] == 3
    assert result["difference"]["manager_corrected_minus_common_support"] == 1
    assert result["difference"]["corrected_only_not_in_original"] == 1
    assert result["difference"]["original_only_not_in_corrected"] == 0
    assert result["difference"]["intersection_identity"] is True
    assert result["difference"]["unexplained_common_support_entries"] == 0
    assert result["matches_published"]["common_support"] is True


def test_published_counts_and_identity_are_recorded():
    assert PUBLISHED_COUNTS["manager_corrected_retained"] == 254
    assert PUBLISHED_COUNTS["manager_abstained"] == 345
    assert PUBLISHED_COUNTS["common_support"] == 218
    assert (
        PUBLISHED_COUNTS["manager_corrected_retained"]
        - PUBLISHED_COUNTS["common_support"]
        == 36
    )


def test_paired_side_changes_and_exact_mcnemar():
    rows = [
        _row(1, winner=1, manager_corrected_p1=0.8, manager_original_p1=0.2),
        _row(2, winner=2, manager_corrected_p1=0.8, manager_original_p1=0.2),
        _row(3, winner=2, manager_corrected_p1=0.8, manager_original_p1=0.2),
        _row(4, winner=1, manager_corrected_p1=0.8, manager_original_p1=0.8),
    ]
    result = paired_side_changes(
        rows, left="manager_corrected_p1", right="manager_original_p1"
    )
    assert result["shared"] == 4
    assert result["side_flips"] == 3
    assert result["left_only_correct"] == 1
    assert result["right_only_correct"] == 2
    assert result["mcnemar_exact"]["p_value"] == 1.0

    assert mcnemar_exact(5, 3)["p_value"] == 0.7265625
    assert mcnemar_exact(0, 0)["p_value"] == 1.0


def test_averaging_diagnostics_report_sharpness_and_brier():
    rows = [
        _row(1, winner=1, a1_p1=0.8, a2_p1=0.2),
        _row(2, winner=2, a1_p1=0.7, a2_p1=0.9),
        _row(3, winner=1, a1_p1=0.9, a2_p1=0.2),
    ]
    result = forecast_average_diagnostics(rows)
    assert result["available"] is True
    assert result["n"] == 3
    assert result["a1"]["brier"] < result["average"]["brier"]
    assert result["sharpness_average_mean_abs_deviation"] < result[
        "sharpness_a1_mean_abs_deviation"
    ]
    assert arm_metrics(rows, "a1_p1")["covered"] == 3
