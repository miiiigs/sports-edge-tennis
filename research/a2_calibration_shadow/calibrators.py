"""Monotone probability calibrators for the Agent 2 calibration shadow study.

Every calibrator maps a *selected-side* confidence in ``[0.5, 1]`` to a
calibrated confidence in ``[0.5, 1]``.  Constraining the output to stay at or
above 0.5 guarantees that a calibration layer can never change which player
Agent 2 selected.  The mapping is monotone non-decreasing, so the ranking of
matches by confidence is preserved.

The module is research-only and is never imported by production prediction,
Manager, Market, Veto or staking code.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

_EPS = 1e-12
_MIN_CONFIDENCE = 0.5
_MAX_CONFIDENCE = 1.0

Sample = tuple[float, int]

CANDIDATE_NAMES: tuple[str, ...] = (
    "identity",
    "temperature",
    "platt",
    "isotonic_500",
    "isotonic_200",
    "isotonic_100",
    "isotonic_50",
    "piecewise_linear",
)

# A lower rank means a simpler, more defensible model.  The selection protocol
# treats candidates within the pre-registered log-loss tolerance as tied and
# prefers the lower rank.
COMPLEXITY_RANK: dict[str, int] = {
    "identity": 0,
    "temperature": 1,
    "platt": 2,
    "piecewise_linear": 3,
    "isotonic_500": 4,
    "isotonic_200": 5,
    "isotonic_100": 6,
    "isotonic_50": 7,
}

PARAMETER_COUNT: dict[str, int] = {
    "identity": 0,
    "temperature": 1,
    "platt": 2,
    "piecewise_linear": 6,
    "isotonic_500": 1,
    "isotonic_200": 1,
    "isotonic_100": 1,
    "isotonic_50": 1,
}


def _clip_probability(value: float) -> float:
    return min(max(value, _EPS), 1.0 - _EPS)


def logit(value: float) -> float:
    p = _clip_probability(value)
    return math.log(p / (1.0 - p))


def sigmoid(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _validate_samples(samples: Sequence[Sample], *, minimum: int) -> None:
    if len(samples) < minimum:
        raise ValueError(f"calibration requires at least {minimum} samples")
    for confidence, outcome in samples:
        if not (0.5 <= confidence <= 1.0):
            raise ValueError("selected-side confidence must be within [0.5, 1.0]")
        if outcome not in (0, 1):
            raise ValueError("outcome must be 0 or 1")


@dataclass(frozen=True)
class CalibrationModel:
    """A fitted monotone mapping from raw to calibrated selected-side confidence."""

    name: str
    kind: str
    parameters: dict[str, Any]

    @property
    def n_parameters(self) -> int:
        if self.kind == "isotonic":
            return len(self.parameters["values"])
        if self.kind == "piecewise_linear":
            return max(0, len(self.parameters["ys"]) - 1)
        return PARAMETER_COUNT[self.name]

    def predict(self, confidence: float) -> float:
        if not math.isfinite(confidence):
            raise ValueError("confidence must be finite")
        if confidence < 0.5:
            raise ValueError("selected-side confidence must be at least 0.5")
        confidence = min(confidence, 1.0)

        if self.kind == "identity":
            value = confidence
        elif self.kind == "temperature":
            value = sigmoid(float(self.parameters["slope"]) * logit(confidence))
        elif self.kind == "platt":
            value = sigmoid(
                float(self.parameters["intercept"])
                + float(self.parameters["slope"]) * logit(confidence)
            )
        elif self.kind == "isotonic":
            value = _predict_isotonic(
                confidence,
                self.parameters["thresholds"],
                self.parameters["values"],
            )
        elif self.kind == "piecewise_linear":
            value = _predict_piecewise(
                confidence,
                self.parameters["xs"],
                self.parameters["ys"],
            )
        else:  # pragma: no cover - defensive
            raise ValueError(f"unsupported calibrator kind {self.kind!r}")

        return min(max(float(value), _MIN_CONFIDENCE), _MAX_CONFIDENCE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "n_parameters": self.n_parameters,
            "parameters": self.parameters,
        }


def _predict_isotonic(
    confidence: float,
    thresholds: Sequence[float],
    values: Sequence[float],
) -> float:
    if not thresholds:
        return 0.5
    value = float(values[0])
    for threshold, mapped in zip(thresholds, values):
        if confidence <= float(threshold):
            return float(mapped)
        value = float(mapped)
    return value


def _predict_piecewise(
    confidence: float,
    xs: Sequence[float],
    ys: Sequence[float],
) -> float:
    if not xs:
        return 0.5
    if confidence <= float(xs[0]):
        return float(ys[0])
    if confidence >= float(xs[-1]):
        return float(ys[-1])
    for index in range(1, len(xs)):
        upper_x = float(xs[index])
        if confidence <= upper_x:
            lower_x = float(xs[index - 1])
            lower_y = float(ys[index - 1])
            upper_y = float(ys[index])
            if upper_x <= lower_x:
                return upper_y
            weight = (confidence - lower_x) / (upper_x - lower_x)
            return lower_y + weight * (upper_y - lower_y)
    return float(ys[-1])


def fit_temperature(samples: Sequence[Sample]) -> CalibrationModel:
    """Fit ``sigmoid(b * logit(s))`` by minimising log loss.

    The one-dimensional negative log likelihood is convex in ``b`` for
    ``b > 0``, so a bounded golden-section search is deterministic and does
    not need a third-party optimiser.
    """

    _validate_samples(samples, minimum=5)
    xs = [logit(confidence) for confidence, _ in samples]
    ys = [outcome for _, outcome in samples]

    def negative_log_likelihood(slope: float) -> float:
        total = 0.0
        for x, y in zip(xs, ys):
            probability = sigmoid(slope * x)
            probability = min(max(probability, 1e-15), 1.0 - 1e-15)
            total -= y * math.log(probability) + (1 - y) * math.log(1.0 - probability)
        return total / len(samples)

    low, high = 0.01, 5.0
    for _ in range(100):
        left = low + 0.382 * (high - low)
        right = low + 0.618 * (high - low)
        if negative_log_likelihood(left) < negative_log_likelihood(right):
            high = right
        else:
            low = left
    slope = (low + high) / 2.0

    identity_loss = negative_log_likelihood(1.0)
    if not math.isfinite(slope) or negative_log_likelihood(slope) > identity_loss + 1e-12:
        slope = 1.0

    return CalibrationModel(
        name="temperature",
        kind="temperature",
        parameters={"slope": slope},
    )


def fit_platt(samples: Sequence[Sample]) -> CalibrationModel:
    """Fit ``sigmoid(a + b * logit(s))`` with ``a >= 0`` and ``b > 0``.

    The non-negative intercept constraint guarantees that the calibrated
    confidence stays at or above 0.5.  If the unconstrained optimum has a
    negative intercept, the constrained optimum lies on the ``a = 0``
    boundary and the slope is refitted by temperature scaling.
    """

    _validate_samples(samples, minimum=5)
    xs = [logit(confidence) for confidence, _ in samples]
    ys = [outcome for _, outcome in samples]
    intercept = 0.0
    slope = 1.0

    for _ in range(200):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            probability = sigmoid(intercept + slope * x)
            weight = max(probability * (1.0 - probability), _EPS)
            residual = y - probability
            g0 += residual
            g1 += residual * x
            h00 += weight
            h01 += weight * x
            h11 += weight * x * x
        determinant = h00 * h11 - h01 * h01
        if abs(determinant) < 1e-18:
            break
        delta_intercept = (g0 * h11 - g1 * h01) / determinant
        delta_slope = (h00 * g1 - h01 * g0) / determinant
        intercept += delta_intercept
        slope += delta_slope
        if abs(delta_intercept) + abs(delta_slope) < 1e-12:
            break

    if intercept < 0.0 or not math.isfinite(intercept):
        intercept = 0.0
        slope = float(fit_temperature(samples).parameters["slope"])
    if not math.isfinite(slope) or slope <= 0.0:
        intercept = 0.0
        slope = 1.0

    return CalibrationModel(
        name="platt",
        kind="platt",
        parameters={"intercept": intercept, "slope": slope},
    )


def _contiguous_bins(
    samples: Sequence[Sample],
    min_bin: int,
) -> list[list[Sample]]:
    ordered = sorted(samples, key=lambda item: item[0])
    bins: list[list[Sample]] = []
    current: list[Sample] = []
    for sample in ordered:
        current.append(sample)
        if len(current) >= min_bin:
            bins.append(current)
            current = []
    if current:
        if bins and len(current) < min_bin:
            bins[-1].extend(current)
        else:
            bins.append(current)
    return bins


def _pool_adjacent_violators(
    sums: Sequence[float],
    counts: Sequence[int],
    thresholds: Sequence[float],
) -> tuple[list[float], list[float]]:
    blocks: list[list[float]] = []
    for total, count, threshold in zip(sums, counts, thresholds):
        blocks.append([float(total), float(count), float(threshold)])
        while len(blocks) >= 2:
            previous_mean = blocks[-2][0] / blocks[-2][1]
            current_mean = blocks[-1][0] / blocks[-1][1]
            if previous_mean <= current_mean:
                break
            merged = blocks.pop()
            blocks[-1][0] += merged[0]
            blocks[-1][1] += merged[1]
            blocks[-1][2] = merged[2]
    values = [total / count for total, count, _ in blocks]
    upper_bounds = [threshold for _, _, threshold in blocks]
    return values, upper_bounds


def fit_isotonic(samples: Sequence[Sample], *, min_bin: int = 100) -> CalibrationModel:
    """Fit a monotone step function with at least ``min_bin`` samples per step."""

    _validate_samples(samples, minimum=max(5, min_bin * 2))
    bins = _contiguous_bins(samples, min_bin)
    sums = [sum(outcome for _, outcome in bucket) for bucket in bins]
    counts = [len(bucket) for bucket in bins]
    upper_bounds = [max(confidence for confidence, _ in bucket) for bucket in bins]
    values, thresholds = _pool_adjacent_violators(sums, counts, upper_bounds)
    values = [min(max(value, _MIN_CONFIDENCE), _MAX_CONFIDENCE) for value in values]
    return CalibrationModel(
        name=f"isotonic_{min_bin}",
        kind="isotonic",
        parameters={"thresholds": thresholds, "values": values},
    )


def fit_piecewise_linear(
    samples: Sequence[Sample],
    *,
    edges: tuple[float, ...] = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00),
) -> CalibrationModel:
    """Fit a monotone piecewise-linear curve on fixed confidence edges."""

    _validate_samples(samples, minimum=50)
    sums: list[float] = []
    counts: list[int] = []
    centres: list[float] = []
    for lower, upper in pairwise(edges):
        bucket = [
            sample
            for sample in samples
            if lower <= sample[0] < upper or (upper == 1.0 and sample[0] == 1.0)
        ]
        if not bucket:
            continue
        sums.append(sum(outcome for _, outcome in bucket))
        counts.append(len(bucket))
        centres.append((lower + upper) / 2.0)

    if not centres:
        raise ValueError("piecewise calibrator has no populated bins")

    values, _ = _pool_adjacent_violators(sums, counts, centres)
    values = [min(max(value, _MIN_CONFIDENCE), _MAX_CONFIDENCE) for value in values]

    xs: list[float] = []
    ys: list[float] = []
    if centres[0] > 0.5:
        xs.append(0.5)
        ys.append(max(0.5, values[0]))
    for centre, value in zip(centres, values):
        if xs and centre <= xs[-1]:
            ys[-1] = value
            continue
        xs.append(centre)
        ys.append(value)
    if xs[-1] < 1.0:
        xs.append(1.0)
        ys.append(values[-1])

    return CalibrationModel(
        name="piecewise_linear",
        kind="piecewise_linear",
        parameters={"xs": xs, "ys": ys},
    )


def fit_calibrator(name: str, samples: Sequence[Sample]) -> CalibrationModel:
    if name == "identity":
        return CalibrationModel(name="identity", kind="identity", parameters={})
    if name == "temperature":
        return fit_temperature(samples)
    if name == "platt":
        return fit_platt(samples)
    if name == "piecewise_linear":
        return fit_piecewise_linear(samples)
    if name.startswith("isotonic_"):
        try:
            min_bin = int(name.split("_", 1)[1])
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"invalid isotonic calibrator {name!r}") from exc
        return fit_isotonic(samples, min_bin=min_bin)
    raise ValueError(f"unknown calibrator {name!r}")


def apply_symmetrically(model: CalibrationModel, player_1_probability: float) -> float:
    """Apply a selected-side calibrator to a player-1 probability.

    The selected side is always the side with the higher raw probability.  The
    calibrated player-1 probability is the selected-side mapping when player 1
    is the favourite and the complement when player 2 is the favourite.  This
    keeps the two player probabilities complementary and cannot flip the
    selected side.
    """

    if not (0.0 < player_1_probability < 1.0):
        raise ValueError("player-1 probability must be strictly within (0, 1)")
    if player_1_probability >= 0.5:
        return model.predict(player_1_probability)
    return 1.0 - model.predict(1.0 - player_1_probability)


__all__ = [
    "CANDIDATE_NAMES",
    "COMPLEXITY_RANK",
    "CalibrationModel",
    "Sample",
    "apply_symmetrically",
    "fit_calibrator",
    "fit_isotonic",
    "fit_piecewise_linear",
    "fit_platt",
    "fit_temperature",
    "logit",
    "sigmoid",
]
