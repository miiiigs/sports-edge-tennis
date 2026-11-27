"""Pure tests for the complete marginal-cohort calibration reconciliation."""
from __future__ import annotations

import math

import pytest

from sports_edge_tennis.validation.manager_c_marginal_calibration import (
    band_block,
    bucket,
    confidence_band,
    expected_variance,
)


def _row(match_id: int, side: int, p1: float, winner: int,
         cluster: int | None = None) -> dict:
    return {"match_id": match_id, "side": side, "p": p1,
            "winner": winner, "cluster": cluster if cluster is not None else match_id}


def test_confidence_band_boundaries_are_half_open():
    assert confidence_band(0.55) == "[0.55,0.60)"
    assert confidence_band(0.599999) == "[0.55,0.60)"
    assert confidence_band(0.60) == "[0.60,0.65)"
    assert confidence_band(0.65) == "[0.65,0.70)"
    assert confidence_band(0.80) == "[0.80,1.00]"
    assert confidence_band(0.5499) == "BELOW_0.55"


def test_bucket_preserves_unknown_and_open_ended_ranges():
    buckets = ((0, 0), (1, 9), (10, 19), (20, 10**9))
    assert bucket(0, buckets) == "0"
    assert bucket(9, buckets) == "1-9"
    assert bucket(20, buckets) == "20+"
    assert bucket(None, buckets) == "UNKNOWN"


def test_band_block_uses_selected_side_probability():
    # Side 2 with p1=0.30 is a 0.70 selected-side forecast that wins.
    rows = [_row(1, 2, 0.30, 2), _row(2, 2, 0.30, 1)]
    block = band_block(rows)
    assert block["settled"] == 2
    assert block["wins"] == 1
    assert block["expected_wins"] == pytest.approx(1.40)
    assert block["residual"] == pytest.approx(-0.40)
    assert block["mean_selected_probability"] == pytest.approx(0.70)
    assert block["brier"] == pytest.approx((0.30**2 + 0.70**2) / 2)


def test_band_block_residual_z_uses_independent_variance():
    rows = [_row(1, 1, 0.70, 1), _row(2, 1, 0.70, 2)]
    block = band_block(rows)
    variance = 2 * 0.70 * 0.30
    assert block["independent_variance"] == pytest.approx(variance)
    assert block["residual_z"] == pytest.approx(
        block["residual"] / math.sqrt(variance))


def test_empty_band_is_reported_as_zero_not_omitted():
    block = band_block([])
    assert block["settled"] == 0
    assert block["expected_wins"] == 0.0
    assert block["mean_selected_probability"] is None


def test_expected_variance_reports_dependence_limitation():
    rows = [_row(1, 1, 0.70, 1, cluster=10),
            _row(2, 1, 0.65, 2, cluster=10),
            _row(3, 1, 0.60, 1, cluster=11)]
    result = expected_variance(rows)
    assert result["clusters"] == 2
    assert result["largest_cluster"] == 2
    assert result["independent_variance"] == pytest.approx(
        0.70 * 0.30 + 0.65 * 0.35 + 0.60 * 0.40)
    assert "independent" in result["limitation"].lower()
    assert result["cluster_bootstrap"]["available"] is True
