"""Pure-Python accuracy, calibration, and uncertainty helpers.

The module deliberately avoids numpy so the audit can run in the same minimal
runtime as the production test suite.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise

from .dataset import SCORED_STATUSES, Pick


@dataclass(frozen=True)
class AccuracyMetrics:
    n: int
    wins: int
    losses: int
    accuracy: float | None
    ci_low: float | None
    ci_high: float | None
    mean_probability: float | None
    calibration_gap: float | None
    brier: float | None
    log_loss: float | None
    ece: float | None

    def to_dict(self) -> dict[str, float | int | None]:
        return asdict(self)


def scored_picks(picks: Iterable[Pick]) -> tuple[Pick, ...]:
    return tuple(p for p in picks if p.result_status in SCORED_STATUSES)


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = wins / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def expected_calibration_error(
    probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> float | None:
    if not probabilities:
        return None
    buckets: list[list[int]] = [[] for _ in range(bins)]
    for probability, outcome in zip(probabilities, outcomes, strict=True):
        index = min(bins - 1, max(0, int(probability * bins)))
        buckets[index].append((probability, outcome))
    total = len(probabilities)
    ece = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        bucket_p = sum(item[0] for item in bucket) / len(bucket)
        bucket_y = sum(item[1] for item in bucket) / len(bucket)
        ece += len(bucket) / total * abs(bucket_p - bucket_y)
    return ece


def accuracy_metrics(picks: Iterable[Pick]) -> AccuracyMetrics:
    rows = scored_picks(picks)
    n = len(rows)
    if n == 0:
        return AccuracyMetrics(0, 0, 0, None, None, None, None, None, None, None, None)
    wins = sum(1 for row in rows if row.won)
    probabilities = [row.probability for row in rows]
    outcomes = [row.target for row in rows]
    mean_probability = sum(probabilities) / n
    accuracy = wins / n
    interval = wilson_interval(wins, n)
    brier = (
        sum(
            (probability - outcome) ** 2
            for probability, outcome in zip(probabilities, outcomes, strict=True)
        )
        / n
    )
    log_loss = (
        -sum(
            outcome * math.log(max(probability, 1e-12))
            + (1 - outcome) * math.log(max(1 - probability, 1e-12))
            for probability, outcome in zip(probabilities, outcomes, strict=True)
        )
        / n
    )
    return AccuracyMetrics(
        n=n,
        wins=wins,
        losses=n - wins,
        accuracy=accuracy,
        ci_low=interval[0] if interval else None,
        ci_high=interval[1] if interval else None,
        mean_probability=mean_probability,
        calibration_gap=accuracy - mean_probability,
        brier=brier,
        log_loss=log_loss,
        ece=expected_calibration_error(probabilities, outcomes),
    )


def bootstrap_accuracy_interval(
    picks: Iterable[Pick],
    *,
    iterations: int = 4000,
    seed: int = 20261021,
) -> tuple[float, float] | None:
    rows = scored_picks(picks)
    if not rows:
        return None
    outcomes = [row.target for row in rows]
    rng = random.Random(seed)
    n = len(outcomes)
    estimates: list[float] = []
    for _ in range(iterations):
        sample = [outcomes[rng.randrange(n)] for _ in range(n)]
        estimates.append(sum(sample) / n)
    estimates.sort()
    low = estimates[int(0.025 * iterations)]
    high = estimates[min(iterations - 1, int(0.975 * iterations))]
    return low, high


@dataclass(frozen=True)
class StrategyEffect:
    name: str
    description: str
    base: AccuracyMetrics
    kept: AccuracyMetrics
    rejected: AccuracyMetrics
    coverage: float | None
    accuracy_delta: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "coverage": self.coverage,
            "accuracy_delta": self.accuracy_delta,
            "base": self.base.to_dict(),
            "kept": self.kept.to_dict(),
            "rejected": self.rejected.to_dict(),
        }


def evaluate_filter(
    picks: Sequence[Pick],
    *,
    name: str,
    description: str,
    rejects: Callable[[Pick], bool],
) -> StrategyEffect:
    base = accuracy_metrics(picks)
    kept = accuracy_metrics(p for p in picks if not rejects(p))
    rejected = accuracy_metrics(p for p in picks if rejects(p))
    coverage = (kept.n / base.n) if base.n else None
    delta = (
        kept.accuracy - base.accuracy
        if kept.accuracy is not None and base.accuracy is not None
        else None
    )
    return StrategyEffect(
        name=name,
        description=description,
        base=base,
        kept=kept,
        rejected=rejected,
        coverage=coverage,
        accuracy_delta=delta,
    )


def probability_bands(
    picks: Sequence[Pick],
    *,
    boundaries: Sequence[float] = (0.60, 0.70, 0.80, 0.90, 1.01),
) -> list[dict[str, object]]:
    bands: list[dict[str, object]] = []
    for low, high in pairwise(boundaries):
        rows = [p for p in picks if low <= p.probability < high]
        metrics = accuracy_metrics(rows)
        bands.append(
            {
                "band": f"{low:.2f}-{high:.2f}",
                "low": low,
                "high": high,
                **metrics.to_dict(),
            }
        )
    return bands


def grouped_metrics(
    picks: Sequence[Pick], key: Callable[[Pick], str]
) -> list[dict[str, object]]:
    groups: dict[str, list[Pick]] = {}
    for pick in picks:
        groups.setdefault(key(pick), []).append(pick)
    rows: list[dict[str, object]] = []
    for label in sorted(groups):
        metrics = accuracy_metrics(groups[label])
        rows.append({"group": label, **metrics.to_dict()})
    return rows


__all__ = [
    "AccuracyMetrics",
    "StrategyEffect",
    "accuracy_metrics",
    "bootstrap_accuracy_interval",
    "evaluate_filter",
    "expected_calibration_error",
    "grouped_metrics",
    "probability_bands",
    "scored_picks",
    "wilson_interval",
]
