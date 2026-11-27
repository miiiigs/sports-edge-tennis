"""Manager C cohort reconciliation without changing the production rule.

The published comparison reports four numbers that are easy to conflate:

* 599 settled eligible Agent 1 fixtures;
* 254 Manager-C-retained fixtures using the corrected Agent 1 arm;
* 345 Manager abstentions;
* 218 fixtures in the seven-arm common support.

This module reproduces the accounting from per-fixture arm outputs and makes
the 36-fixture difference explicit. Common support is the intersection of the
arms that actually have coverage. Agent 3 has zero coverage, so the all-arm
intersection is empty; the meaningful seven-arm support includes both the
corrected Manager arm (E) and the original Manager arm (R2). If E covers a
fixture, the A1/A2 mean arms also cover it, so the binding intersection for
those 254 fixtures is E and R2. Thirty-six corrected-Manager selections were
not selected by the original Manager and therefore leave the common support.

No production selection rule, threshold or policy is modified.
"""
from __future__ import annotations

import math
import random
from typing import Any, Mapping, Sequence

RECONCILIATION_VERSION = "MANAGER_C_COHORT_RECONCILIATION_V1"
BOOTSTRAP_ITERATIONS = 4000
BOOTSTRAP_SEED = 20261127
EPSILON = 1e-12

# Published checkpoint counts. They are asserted by the reconciliation, not
# used to fabricate per-fixture membership.
PUBLISHED_COUNTS = {
    "settled_eligible": 599,
    "manager_corrected_retained": 254,
    "manager_abstained": 345,
    "common_support": 218,
    "manager_original_retained": 283,
}


def candidate(p1: float | None) -> tuple[int, float] | None:
    """Return a side for a probability, with exactly 0.5 treated as abstention."""
    if p1 is None:
        return None
    value = float(p1)
    if not math.isfinite(value) or not 0 < value < 1:
        return None
    if abs(value - 0.5) < 1e-12:
        return None
    return (1 if value > 0.5 else 2, value)


def _brier(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return sum(
        (float(row[key]) - (1 if int(row["winner"]) == 1 else 0)) ** 2
        for row in rows
    ) / len(rows)


def _log_loss(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    total = 0.0
    for row in rows:
        p1 = float(row[key])
        outcome = 1 if int(row["winner"]) == 1 else 0
        total -= outcome * math.log(max(p1, EPSILON)) + (1 - outcome) * math.log(
            max(1 - p1, EPSILON)
        )
    return total / len(rows)


def arm_metrics(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    """Metrics on covered, settled fixtures for one probability column."""
    covered = [row for row in rows if candidate(row.get(key)) is not None]
    if not covered:
        return {"eligible": len(rows), "covered": 0, "accuracy": None,
                "brier": None, "log_loss": None}
    correct = sum(
        1 for row in covered
        if (float(row[key]) > 0.5) == (int(row["winner"]) == 1)
    )
    return {
        "eligible": len(rows),
        "covered": len(covered),
        "coverage": len(covered) / len(rows) if rows else None,
        "accuracy": correct / len(covered),
        "brier": _brier(covered, key),
        "log_loss": _log_loss(covered, key),
    }


def paired_side_changes(
    rows: Sequence[Mapping[str, Any]],
    *,
    left: str,
    right: str,
) -> dict[str, Any]:
    """Paired side-change accounting on the shared covered fixtures."""
    shared = [
        row for row in rows
        if candidate(row.get(left)) is not None and candidate(row.get(right)) is not None
    ]
    left_correct = right_correct = 0
    side_flips = left_changed_to_wrong = right_changed_to_wrong = 0
    for row in shared:
        winner = int(row["winner"])
        left_side = 1 if float(row[left]) > 0.5 else 2
        right_side = 1 if float(row[right]) > 0.5 else 2
        left_hit = left_side == winner
        right_hit = right_side == winner
        left_correct += int(left_hit)
        right_correct += int(right_hit)
        if left_side != right_side:
            side_flips += 1
            if left_hit and not right_hit:
                left_changed_to_wrong += 1
            elif right_hit and not left_hit:
                right_changed_to_wrong += 1
    b = sum(
        1 for row in shared
        if _hit(row, left) and not _hit(row, right)
    )
    c = sum(
        1 for row in shared
        if _hit(row, right) and not _hit(row, left)
    )
    return {
        "shared": len(shared),
        "left_correct": left_correct,
        "right_correct": right_correct,
        "accuracy_difference": (
            (left_correct - right_correct) / len(shared) if shared else None
        ),
        "side_flips": side_flips,
        "left_only_correct": b,
        "right_only_correct": c,
        "correct_changed_to_incorrect": left_changed_to_wrong,
        "incorrect_changed_to_correct": right_changed_to_wrong,
        "mcnemar_exact": mcnemar_exact(b, c),
    }


def _hit(row: Mapping[str, Any], key: str) -> bool:
    return (float(row[key]) > 0.5) == (int(row["winner"]) == 1)


def mcnemar_exact(b: int, c: int) -> dict[str, Any]:
    """Two-sided exact McNemar test on discordant pairs."""
    if b < 0 or c < 0:
        raise ValueError("discordant counts must be non-negative")
    n = b + c
    if n == 0:
        return {"b": 0, "c": 0, "n": 0, "p_value": 1.0}
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / (2 ** n)
    p_value = min(1.0, 2 * tail)
    return {"b": b, "c": c, "n": n, "p_value": p_value}


def forecast_average_diagnostics(
    rows: Sequence[Mapping[str, Any]],
    *,
    a1: str = "a1_p1",
    a2: str = "a2_p1",
) -> dict[str, Any]:
    """Does averaging A1 and A2 worsen calibration or sharpness?"""
    pairs = [
        row for row in rows
        if candidate(row.get(a1)) is not None and candidate(row.get(a2)) is not None
    ]
    if not pairs:
        return {"n": 0, "available": False}
    averaged = [
        {
            "winner": row["winner"],
            "p1": 0.5 * float(row[a1]) + 0.5 * float(row[a2]),
            "a1": float(row[a1]),
        }
        for row in pairs
    ]
    a1_rows = [{"winner": row["winner"], "p1": float(row[a1])} for row in pairs]
    a2_rows = [{"winner": row["winner"], "p1": float(row[a2])} for row in pairs]
    return {
        "available": True,
        "n": len(pairs),
        "a1": arm_metrics(a1_rows, "p1"),
        "a2": arm_metrics(a2_rows, "p1"),
        "average": arm_metrics(averaged, "p1"),
        "mean_abs_a1_a2_difference": sum(
            abs(float(row[a1]) - float(row[a2])) for row in pairs
        ) / len(pairs),
        "sharpness_a1_mean_abs_deviation": sum(
            abs(float(row[a1]) - 0.5) for row in pairs
        ) / len(pairs),
        "sharpness_average_mean_abs_deviation": sum(
            abs(row["p1"] - 0.5) for row in averaged
        ) / len(pairs),
        "paired": paired_side_changes(pairs, left=a1, right=a2),
        "paired_average_vs_a1": paired_side_changes(averaged, left="a1", right="p1"),
        "paired_average_vs_a1_bootstrap": paired_bootstrap(
            averaged, left="a1", right="p1"
        ),
    }


def _memberships(rows: Sequence[Mapping[str, Any]], key: str) -> set[int]:
    return {
        int(row["match_id"]) for row in rows
        if candidate(row.get(key)) is not None
    }


def reconcile_cohorts(
    rows: Sequence[Mapping[str, Any]],
    *,
    manager_corrected: str = "manager_corrected_p1",
    manager_original: str = "manager_original_p1",
    arm_keys: Sequence[str] = (
        "a1_p1", "a2_p1", "manager_corrected_p1", "manager_original_p1",
        "a1_original_p1",
    ),
    published: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Reproduce the 599/254/345/218 accounting and explain the 36 difference."""
    settled = [row for row in rows if int(row.get("winner") or 0) in (1, 2)]
    e_ids = _memberships(settled, manager_corrected)
    r2_ids = _memberships(settled, manager_original)
    raw_common = {
        int(row["match_id"]) for row in settled
        if all(candidate(row.get(key)) is not None for key in arm_keys)
    }
    common = raw_common & e_ids & r2_ids
    e_only = e_ids - r2_ids
    r2_only = r2_ids - e_ids
    unexplained = raw_common - (e_ids & r2_ids)
    expected = published or PUBLISHED_COUNTS
    checks = {
        "settled_eligible": len(settled),
        "manager_corrected_retained": len(e_ids),
        "manager_abstained": len(settled) - len(e_ids),
        "common_support": len(common),
        "manager_original_retained": len(r2_ids),
    }
    return {
        "version": RECONCILIATION_VERSION,
        "arms": list(arm_keys),
        "measured": checks,
        "published": dict(expected),
        "matches_published": {
            key: checks[key] == expected[key]
            for key in checks if key in expected
        },
        "difference": {
            "manager_corrected_minus_common_support": len(e_ids) - len(common),
            "explanation": (
                "Common support is the intersection of all covered arms. On a "
                "corrected-Manager selection the A1/A2 mean arms always cover, "
                "so the binding constraint is the original Manager arm (R2). "
                "Corrected-Manager selections absent from R2 leave support."
            ),
            "corrected_only_not_in_original": len(e_only),
            "original_only_not_in_corrected": len(r2_only),
            "union": len(e_ids | r2_ids),
            "intersection_identity": (
                len(e_ids) + len(r2_ids) - len(e_ids & r2_ids) == len(e_ids | r2_ids)
            ),
            "unexplained_common_support_entries": len(unexplained),
        },
        "ids_sha256_inputs": {
            "manager_corrected": sorted(e_ids),
            "manager_original": sorted(r2_ids),
            "common_support": sorted(common),
        },
        "paired": {
            "corrected_vs_original": paired_side_changes(
                [row for row in settled if row["match_id"] in e_ids | r2_ids],
                left=manager_corrected,
                right=manager_original,
            ),
        },
        "averaging": forecast_average_diagnostics(settled),
        "production_rule_changed": False,
    }


def _quantile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def paired_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    *,
    left: str,
    right: str,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Match-clustered percentile intervals for accuracy and Brier differences."""
    shared = [
        row for row in rows
        if candidate(row.get(left)) is not None and candidate(row.get(right)) is not None
    ]
    if not shared:
        return {"n": 0, "brier_improvement_95": None, "accuracy_improvement_95": None}
    rng = random.Random(seed)
    brier: list[float] = []
    accuracy: list[float] = []
    for _ in range(iterations):
        sample = [shared[rng.randrange(len(shared))] for _ in shared]
        brier.append(
            sum((float(row[left]) - (1 if row["winner"] == 1 else 0)) ** 2 for row in sample)
            / len(sample)
            - sum((float(row[right]) - (1 if row["winner"] == 1 else 0)) ** 2 for row in sample)
            / len(sample)
        )
        accuracy.append(
            sum(int(_hit(row, left)) for row in sample) / len(sample)
            - sum(int(_hit(row, right)) for row in sample) / len(sample)
        )
    return {
        "n": len(shared),
        "brier_improvement_95": [_quantile(brier, 0.025), _quantile(brier, 0.975)],
        "accuracy_improvement_95": [_quantile(accuracy, 0.025), _quantile(accuracy, 0.975)],
        "note": "Positive Brier improvement means the left arm scored worse.",
    }
