"""Evaluation metrics and paired uncertainty for the calibration shadow study.

All functions operate on selected-side confidences and binary outcomes.  The
binning convention matches the Agent 2 lookback baseline: ten 5-point bins from
0.50 to 1.00.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

try:
    import numpy as np
except ImportError:  # pragma: no cover - research dependency
    np = None  # type: ignore[assignment]

from .calibrators import logit, sigmoid

ECE_EDGES: tuple[float, ...] = tuple(0.50 + 0.05 * index for index in range(11))
CONFIDENCE_THRESHOLDS: tuple[float, ...] = (0.60, 0.70, 0.80, 0.90)
_EPS = 1e-15


def _require_numpy() -> None:
    if np is None:  # pragma: no cover - research dependency
        raise RuntimeError(
            "numpy is required for calibration metrics; install "
            "research/a2_calibration_shadow/requirements.txt"
        )


def _as_arrays(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
) -> tuple[np.ndarray, np.ndarray]:
    _require_numpy()
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    if p.shape != y.shape:
        raise ValueError("probabilities and outcomes must have the same shape")
    if p.size == 0:
        raise ValueError("metrics require at least one observation")
    if np.any(p < 0.0) or np.any(p > 1.0):
        raise ValueError("probabilities must be within [0, 1]")
    if not np.all(np.isin(y, (0.0, 1.0))):
        raise ValueError("outcomes must be binary")
    return p, y


def ece_numpy(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    _require_numpy()
    indices = np.clip(((probabilities - 0.5) / 0.05).astype(int), 0, 9)
    total = float(probabilities.size)
    ece = 0.0
    for index in range(10):
        mask = indices == index
        count = int(mask.sum())
        if count:
            mean_probability = float(probabilities[mask].mean())
            observed = float(outcomes[mask].mean())
            ece += (count / total) * abs(observed - mean_probability)
    return ece


def log_loss_numpy(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    _require_numpy()
    clipped = np.clip(probabilities, _EPS, 1.0 - _EPS)
    return float(
        -(
            outcomes * np.log(clipped)
            + (1.0 - outcomes) * np.log(1.0 - clipped)
        ).mean()
    )


def brier_numpy(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    _require_numpy()
    return float(((probabilities - outcomes) ** 2).mean())


def point_metrics(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    thresholds: Sequence[float] = CONFIDENCE_THRESHOLDS,
) -> dict:
    p, y = _as_arrays(probabilities, outcomes)
    n = int(p.size)
    correct = int(y.sum())
    result = {
        "n": n,
        "correct": correct,
        "accuracy": correct / n,
        "brier": brier_numpy(p, y),
        "log_loss": log_loss_numpy(p, y),
        "ece": ece_numpy(p, y),
        "mean_probability": float(p.mean()),
        "observed": float(y.mean()),
        "gap": float(y.mean() - p.mean()),
        "accuracy_wilson": wilson_interval(correct, n),
        "reliability": reliability_bins(p, y),
        "cumulative": [
            confidence_band(p, y, threshold) for threshold in thresholds
        ],
    }
    return result


def reliability_bins(
    probabilities: np.ndarray,
    outcomes: np.ndarray,
    *,
    edges: Sequence[float] = ECE_EDGES,
) -> list[dict]:
    _require_numpy()
    bins: list[dict] = []
    for lower, upper in pairwise(edges):
        if upper == 1.0:
            mask = (probabilities >= lower) & (probabilities <= upper)
        else:
            mask = (probabilities >= lower) & (probabilities < upper)
        count = int(mask.sum())
        if count:
            mean_probability = float(probabilities[mask].mean())
            observed = float(outcomes[mask].mean())
            bins.append(
                {
                    "lower": float(lower),
                    "upper": float(upper),
                    "n": count,
                    "mean_probability": mean_probability,
                    "observed": observed,
                    "gap": observed - mean_probability,
                    "abs_gap": abs(observed - mean_probability),
                }
            )
        else:
            bins.append(
                {
                    "lower": float(lower),
                    "upper": float(upper),
                    "n": 0,
                    "mean_probability": None,
                    "observed": None,
                    "gap": None,
                    "abs_gap": None,
                }
            )
    return bins


def confidence_band(
    probabilities: np.ndarray,
    outcomes: np.ndarray,
    threshold: float,
) -> dict:
    _require_numpy()
    mask = probabilities >= threshold
    count = int(mask.sum())
    if count == 0:
        return {
            "threshold": float(threshold),
            "n": 0,
            "wins": 0,
            "losses": 0,
            "mean_probability": None,
            "observed": None,
            "gap": None,
            "expected_wins": None,
            "excess_wins": None,
            "wilson": None,
        }
    band_p = probabilities[mask]
    band_y = outcomes[mask]
    wins = int(band_y.sum())
    losses = count - wins
    mean_probability = float(band_p.mean())
    observed = wins / count
    expected_wins = float(band_p.sum())
    return {
        "threshold": float(threshold),
        "n": count,
        "wins": wins,
        "losses": losses,
        "mean_probability": mean_probability,
        "observed": observed,
        "gap": observed - mean_probability,
        "expected_wins": expected_wins,
        "excess_wins": wins - expected_wins,
        "wilson": wilson_interval(wins, count),
    }


def wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> list[float] | None:
    if trials <= 0:
        return None
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (proportion + z * z / (2.0 * trials)) / denominator
    half = (
        z
        * ((proportion * (1.0 - proportion) / trials) + z * z / (4.0 * trials * trials)) ** 0.5
        / denominator
    )
    return [max(0.0, centre - half), min(1.0, centre + half)]


def percentile_interval(values: Sequence[float]) -> list[float] | None:
    _require_numpy()
    if not values:
        return None
    array = np.asarray(values, dtype=float)
    return [
        float(np.percentile(array, 2.5)),
        float(np.percentile(array, 97.5)),
    ]


def paired_bootstrap(
    baseline_probabilities: Sequence[float],
    calibrated_probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    reps: int = 1000,
    seed: int = 20261021,
) -> dict:
    """Match-level paired bootstrap of calibrated minus baseline metrics.

    A negative Brier, log-loss or ECE difference is an improvement.  A
    positive accuracy difference would be an improvement, but a monotone
    calibrator cannot change the selected side and therefore cannot change
    accuracy.
    """

    base_p, y = _as_arrays(baseline_probabilities, outcomes)
    cal_p = np.asarray(calibrated_probabilities, dtype=float)
    if cal_p.shape != base_p.shape:
        raise ValueError("baseline and calibrated probabilities must have the same shape")

    base_metrics = _metric_vector(base_p, y)
    cal_metrics = _metric_vector(cal_p, y)
    deltas = {
        "brier": cal_metrics[0] - base_metrics[0],
        "log_loss": cal_metrics[1] - base_metrics[1],
        "ece": cal_metrics[2] - base_metrics[2],
        "accuracy": cal_metrics[3] - base_metrics[3],
    }

    rng = np.random.default_rng(seed)
    n = int(y.size)
    samples: dict[str, list[float]] = {key: [] for key in deltas}
    for _ in range(reps):
        indices = rng.integers(0, n, n)
        base_sample = _metric_vector(base_p[indices], y[indices])
        cal_sample = _metric_vector(cal_p[indices], y[indices])
        samples["brier"].append(cal_sample[0] - base_sample[0])
        samples["log_loss"].append(cal_sample[1] - base_sample[1])
        samples["ece"].append(cal_sample[2] - base_sample[2])
        samples["accuracy"].append(cal_sample[3] - base_sample[3])

    return {
        "n": n,
        "reps": reps,
        "seed": seed,
        "delta": deltas,
        "delta_ci": {
            key: percentile_interval(values) for key, values in samples.items()
        },
    }


def bootstrap_intervals(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    reps: int = 1000,
    seed: int = 20261021,
) -> dict:
    p, y = _as_arrays(probabilities, outcomes)
    rng = np.random.default_rng(seed)
    n = int(y.size)
    collected = {"brier": [], "log_loss": [], "ece": [], "accuracy": []}
    for _ in range(reps):
        indices = rng.integers(0, n, n)
        metrics = _metric_vector(p[indices], y[indices])
        collected["brier"].append(metrics[0])
        collected["log_loss"].append(metrics[1])
        collected["ece"].append(metrics[2])
        collected["accuracy"].append(metrics[3])
    return {
        "reps": reps,
        "seed": seed,
        "brier": percentile_interval(collected["brier"]),
        "log_loss": percentile_interval(collected["log_loss"]),
        "ece": percentile_interval(collected["ece"]),
        "accuracy": percentile_interval(collected["accuracy"]),
    }


def _metric_vector(probabilities: np.ndarray, outcomes: np.ndarray) -> tuple[float, float, float, float]:
    return (
        brier_numpy(probabilities, outcomes),
        log_loss_numpy(probabilities, outcomes),
        ece_numpy(probabilities, outcomes),
        float(outcomes.mean()),
    )


def cox_calibration(probabilities: Sequence[float], outcomes: Sequence[int]) -> dict:
    """Logistic recalibration slope and intercept of outcome on logit(probability).

    Perfect calibration gives intercept 0 and slope 1.  An overconfident model
    typically has slope below 1.
    """

    p, y = _as_arrays(probabilities, outcomes)
    x = np.asarray([logit(float(value)) for value in p], dtype=float)
    intercept = 0.0
    slope = 1.0
    for _ in range(200):
        linear = intercept + slope * x
        fitted = np.asarray([sigmoid(float(value)) for value in linear], dtype=float)
        weight = np.maximum(fitted * (1.0 - fitted), 1e-12)
        residual = y - fitted
        g0 = float(residual.sum())
        g1 = float((residual * x).sum())
        h00 = float(weight.sum())
        h01 = float((weight * x).sum())
        h11 = float((weight * x * x).sum())
        determinant = h00 * h11 - h01 * h01
        if abs(determinant) < 1e-18:
            break
        delta_intercept = (g0 * h11 - g1 * h01) / determinant
        delta_slope = (h00 * g1 - h01 * g0) / determinant
        intercept += delta_intercept
        slope += delta_slope
        if abs(delta_intercept) + abs(delta_slope) < 1e-12:
            break
    return {
        "intercept": float(intercept),
        "slope": float(slope),
    }


def threshold_effects(
    baseline_probabilities: Sequence[float],
    calibrated_probabilities: Sequence[float],
    *,
    thresholds: Sequence[float] = CONFIDENCE_THRESHOLDS,
) -> list[dict]:
    _require_numpy()
    base = np.asarray(baseline_probabilities, dtype=float)
    cal = np.asarray(calibrated_probabilities, dtype=float)
    total = int(base.size)
    effects: list[dict] = []
    sorted_cal = np.sort(cal)[::-1]
    for threshold in thresholds:
        base_count = int((base >= threshold).sum())
        cal_count = int((cal >= threshold).sum())
        equivalent = None
        if base_count:
            equivalent = float(sorted_cal[base_count - 1])
        effects.append(
            {
                "threshold": float(threshold),
                "baseline_count": base_count,
                "calibrated_count": cal_count,
                "delta_count": cal_count - base_count,
                "baseline_share": base_count / total,
                "calibrated_share": cal_count / total,
                "calibrated_threshold_for_baseline_count": equivalent,
            }
        )
    return effects


def high_confidence_summary(
    baseline_probabilities: Sequence[float],
    calibrated_probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    thresholds: Sequence[float] = CONFIDENCE_THRESHOLDS,
) -> list[dict]:
    _require_numpy()
    base = np.asarray(baseline_probabilities, dtype=float)
    cal = np.asarray(calibrated_probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    result: list[dict] = []
    for threshold in thresholds:
        mask = base >= threshold
        count = int(mask.sum())
        if count == 0:
            result.append({"threshold": float(threshold), "n": 0})
            continue
        wins = int(y[mask].sum())
        losses = count - wins
        result.append(
            {
                "threshold": float(threshold),
                "n": count,
                "wins": wins,
                "losses": losses,
                "baseline_mean": float(base[mask].mean()),
                "calibrated_mean": float(cal[mask].mean()),
                "observed": wins / count,
                "baseline_gap": wins / count - float(base[mask].mean()),
                "calibrated_gap": wins / count - float(cal[mask].mean()),
                "baseline_expected_wins": float(base[mask].sum()),
                "calibrated_expected_wins": float(cal[mask].sum()),
                "expectation_shortfall_baseline": wins - float(base[mask].sum()),
                "expectation_shortfall_calibrated": wins - float(cal[mask].sum()),
            }
        )
    return result


__all__ = [
    "CONFIDENCE_THRESHOLDS",
    "ECE_EDGES",
    "bootstrap_intervals",
    "cox_calibration",
    "ece_numpy",
    "high_confidence_summary",
    "log_loss_numpy",
    "paired_bootstrap",
    "point_metrics",
    "reliability_bins",
    "threshold_effects",
    "wilson_interval",
]
