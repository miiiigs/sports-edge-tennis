"""Offline calibration metric/method tests (pure, no database)."""

from __future__ import annotations

from sports_edge_tennis.performance.calibration import (
    Sample,
    brier,
    calibration_slope_intercept,
    chronological_split,
    expected_calibration_error,
    fit_isotonic,
    fit_platt,
    log_loss,
    reliability_bins,
)


def test_brier_and_log_loss_on_perfect_and_worst_predictions():
    perfect = [(0.99, 1), (0.01, 0)]
    assert brier(perfect) is not None and brier(perfect) < 0.001
    wrong = [(0.99, 0), (0.01, 1)]
    assert brier(wrong) is not None and brier(wrong) > 0.9
    assert log_loss(perfect) < log_loss(wrong)


def test_reliability_bins_and_ece():
    pairs = [(0.62, 1)] * 8 + [(0.62, 0)] * 2 + [(0.87, 1)] * 9 + [(0.87, 0)] * 1
    bins = reliability_bins(pairs)
    low = next(b for b in bins if b.label == "0.60-0.65")
    assert low.n == 10
    assert abs(low.observed - 0.8) < 1e-9
    assert abs(low.gap - (0.8 - 0.62)) < 1e-9
    ece = expected_calibration_error(pairs)
    assert ece is not None and ece > 0


def test_reliability_bins_report_empty_bins_as_none():
    bins = reliability_bins([(0.72, 1)])
    empty = [b for b in bins if b.n == 0]
    assert empty and all(b.observed is None and b.gap is None for b in empty)


def test_calibration_slope_intercept_on_underconfident_data():
    # Well-separated, correctly ordered outcomes: slope should be positive.
    samples = [Sample(0.55, 0), Sample(0.60, 0), Sample(0.90, 1), Sample(0.95, 1)] * 5
    intercept, slope = calibration_slope_intercept(samples)
    assert intercept is not None and slope is not None and slope > 0


def test_platt_and_isotonic_are_monotone():
    samples = [Sample(p / 100, 1 if p >= 70 else 0) for p in range(60, 100, 5)] * 4
    platt = fit_platt(samples)
    isotonic = fit_isotonic(samples)
    assert platt is not None and isotonic is not None
    assert platt.predict(0.90) > platt.predict(0.60)
    assert isotonic.predict(0.90) >= isotonic.predict(0.60)


def test_chronological_split_preserves_order_and_sizes():
    samples = [Sample(0.60 + i * 0.001, i % 2) for i in range(100)]
    train, validation, holdout = chronological_split(samples)
    assert (len(train), len(validation), len(holdout)) == (60, 20, 20)
    # No future leakage: training block is strictly the earliest segment.
    assert train[-1].raw < validation[0].raw < holdout[0].raw


def test_isotonic_requires_minimum_sample():
    assert fit_isotonic([Sample(0.9, 1), Sample(0.8, 0)]) is None
    assert fit_platt([Sample(0.9, 1)]) is None
