"""Offline probability-calibration primitives for the Sports Edge Tennis dashboard lane.

This module is research/evaluation only. It is never imported by the live
prediction, Manager, Market, Veto, staking, settlement, or canonical-identity
paths, and it never alters stored probabilities.

All methods are dependency-free pure Python so the evaluation can run in the
existing environment:
- Platt scaling (logistic recalibration of the logit of the raw probability)
- Isotonic regression (PAVA, monotone step function)
- Reliability bins, ECE, Brier score, log loss, Cox calibration slope/intercept

Beta calibration is intentionally omitted; the repository has no beta-fitting
dependency and adding one solely for this is not justified while the sample is
small.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_EPS = 1e-6


@dataclass(frozen=True)
class Sample:
    """One scored observation: a raw model probability and a binary outcome."""

    raw: float
    outcome: int  # 1 = won, 0 = lost


@dataclass(frozen=True)
class PlattModel:
    intercept: float
    slope: float

    def predict(self, raw: float) -> float:
        return _sigmoid(self.intercept + self.slope * _logit(raw))


@dataclass(frozen=True)
class IsotonicModel:
    thresholds: tuple[float, ...]
    values: tuple[float, ...]

    def predict(self, raw: float) -> float:
        if not self.thresholds:
            return 0.5
        value = self.values[0]
        for threshold, mapped in zip(self.thresholds, self.values):
            if raw >= threshold:
                value = mapped
            else:
                break
        return value


@dataclass(frozen=True)
class ReliabilityBin:
    label: str
    lower: float
    upper: float
    n: int
    average_predicted: float | None
    observed: float | None

    @property
    def gap(self) -> float | None:
        if self.average_predicted is None or self.observed is None:
            return None
        return self.observed - self.average_predicted


def _clip(value: float) -> float:
    return min(max(value, _EPS), 1.0 - _EPS)


def _logit(value: float) -> float:
    p = _clip(value)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def brier(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((pred - outcome) ** 2 for pred, outcome in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    total = 0.0
    for pred, outcome in pairs:
        p = _clip(pred)
        total += -(outcome * math.log(p) + (1 - outcome) * math.log(1.0 - p))
    return total / len(pairs)


def reliability_bins(
    pairs: list[tuple[float, int]],
    edges: tuple[float, ...] = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01),
) -> tuple[ReliabilityBin, ...]:
    bins: list[ReliabilityBin] = []
    for lower, upper in zip(edges[:-1], edges[1:]):
        group = [p for p in pairs if lower <= p[0] < upper]
        if group:
            avg = sum(pred for pred, _ in group) / len(group)
            obs = sum(outcome for _, outcome in group) / len(group)
        else:
            avg = None
            obs = None
        bins.append(
            ReliabilityBin(
                label=f"{lower:.2f}-{upper:.2f}",
                lower=lower,
                upper=upper,
                n=len(group),
                average_predicted=avg,
                observed=obs,
            )
        )
    return tuple(bins)


def expected_calibration_error(
    pairs: list[tuple[float, int]],
    edges: tuple[float, ...] = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01),
) -> float | None:
    if not pairs:
        return None
    total = 0.0
    for b in reliability_bins(pairs, edges):
        if b.gap is None:
            continue
        total += (b.n / len(pairs)) * abs(b.gap)
    return total


def _weighted_logistic_fit(
    xs: list[float], ys: list[int], *, iterations: int = 100, tol: float = 1e-10
) -> tuple[float, float]:
    """Return (intercept, slope) from a 2-feature IRLS logistic fit."""
    b0 = 0.0
    b1 = 0.0
    for _ in range(iterations):
        s00 = s01 = s11 = 0.0
        g0 = g1 = 0.0
        for x, y in zip(xs, ys):
            eta = b0 + b1 * x
            p = _sigmoid(eta)
            w = max(p * (1.0 - p), 1e-12)
            residual = y - p
            s00 += w
            s01 += w * x
            s11 += w * x * x
            g0 += residual
            g1 += residual * x
        det = s00 * s11 - s01 * s01
        if abs(det) < 1e-18:
            break
        # Solve [s00 s01; s01 s11] * delta = [g0; g1]
        d0 = (g0 * s11 - g1 * s01) / det
        d1 = (s00 * g1 - s01 * g0) / det
        b0 += d0
        b1 += d1
        if abs(d0) + abs(d1) < tol:
            break
    return b0, b1


def calibration_slope_intercept(samples: list[Sample]) -> tuple[float | None, float | None]:
    """Cox calibration: logistic fit of outcome on logit(raw). Slope 1 / intercept 0 is perfect."""
    if len(samples) < 3:
        return None, None
    xs = [_logit(s.raw) for s in samples]
    ys = [s.outcome for s in samples]
    intercept, slope = _weighted_logistic_fit(xs, ys)
    return intercept, slope


def fit_platt(samples: list[Sample]) -> PlattModel | None:
    if len(samples) < 5:
        return None
    xs = [_logit(s.raw) for s in samples]
    ys = [s.outcome for s in samples]
    intercept, slope = _weighted_logistic_fit(xs, ys)
    return PlattModel(intercept=intercept, slope=slope)


def fit_isotonic(samples: list[Sample]) -> IsotonicModel | None:
    """Pool-adjacent-violators isotonic regression on raw probability -> outcome."""
    if len(samples) < 5:
        return None
    ordered = sorted(samples, key=lambda s: s.raw)
    # Each block: [sum_y, count, max_raw]
    blocks: list[list[float]] = []
    for s in ordered:
        blocks.append([float(s.outcome), 1.0, s.raw])
        while len(blocks) >= 2:
            prev_mean = blocks[-2][0] / blocks[-2][1]
            cur_mean = blocks[-1][0] / blocks[-1][1]
            if prev_mean <= cur_mean:
                break
            merged = blocks.pop()
            blocks[-1][0] += merged[0]
            blocks[-1][1] += merged[1]
            blocks[-1][2] = merged[2]
    thresholds = tuple(block[2] for block in blocks)
    values = tuple(block[0] / block[1] for block in blocks)
    return IsotonicModel(thresholds=thresholds, values=values)


def chronological_split(
    samples: list[Sample], *, train_fraction: float = 0.6, validation_fraction: float = 0.2
) -> tuple[list[Sample], list[Sample], list[Sample]]:
    """Split already chronologically ordered samples into train/validation/holdout."""
    n = len(samples)
    train_end = int(n * train_fraction)
    validation_end = int(n * (train_fraction + validation_fraction))
    return samples[:train_end], samples[train_end:validation_end], samples[validation_end:]


__all__ = [
    "IsotonicModel",
    "PlattModel",
    "ReliabilityBin",
    "Sample",
    "brier",
    "calibration_slope_intercept",
    "chronological_split",
    "expected_calibration_error",
    "fit_isotonic",
    "fit_platt",
    "log_loss",
    "reliability_bins",
]
