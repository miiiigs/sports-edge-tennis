"""Agent 2 specialist audit: mechanisms, support and incremental value.

The audit is deliberately two-part.

1. **Architecture audit** (static): identify what the existing serve/return
   model already implements before proposing anything new. The production
   configuration is read from ``Agent2Config`` defaults and the module source.
2. **Specialist evaluation** (measured): evaluate Agent 2 only inside
   earlier-defined categories, on settled fixtures where both agents abstained
   or predicted. A specialist claim requires a minimum sample and a paired
   confidence interval that excludes zero; otherwise the verdict is
   ``INSUFFICIENT_EVIDENCE``.

Grand Slam / best-of-five work is coordinated with PR #389. This module does
not relax the Agent 3 support gate, does not train on new commercial data and
makes no provider calls.
"""
from __future__ import annotations

import inspect
import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from sports_edge_tennis.agents import agent2_hierarchical_markov as agent2_module
from sports_edge_tennis.data import agent2_history as agent2_history_module

SPECIALIST_AUDIT_VERSION = "AGENT2_SPECIALIST_AUDIT_V1"
BOOTSTRAP_ITERATIONS = 4000
BOOTSTRAP_SEED = 20261127
MIN_CATEGORY_FIXTURES = 100
MIN_DIRECTION_COUNT = 10
EPSILON = 1e-12

MECHANISM_TOKENS = {
    "recency": ("recency", "days_since", "inactivity", "decay", "time_decay"),
    "opponent_strength": ("opponent_strength", "opponent_adjust", "quality_win"),
    "uncertainty": ("credible_interval", "confidence_interval", "posterior_sd",
                    "standard_error"),
}


@dataclass(frozen=True)
class SpecialistCategory:
    code: str
    description: str
    predicate: Callable[[Mapping[str, Any]], bool]


def _number(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def well_supported(row: Mapping[str, Any]) -> bool:
    """Both players clear the model's own minimum support with margin."""
    matches = [_number(row, "p1_matches"), _number(row, "p2_matches")]
    serve = [_number(row, "p1_service_points"), _number(row, "p2_service_points")]
    returns = [_number(row, "p1_return_points"), _number(row, "p2_return_points")]
    return (
        all(value is not None and value >= 10 for value in matches)
        and all(value is not None and value >= 500 for value in serve)
        and all(value is not None and value >= 500 for value in returns)
    )


def minimum_supported(row: Mapping[str, Any]) -> bool:
    config = agent2_module.Agent2Config()
    matches = [_number(row, "p1_matches"), _number(row, "p2_matches")]
    serve = [_number(row, "p1_service_points"), _number(row, "p2_service_points")]
    returns = [_number(row, "p1_return_points"), _number(row, "p2_return_points")]
    return (
        all(
            value is not None and value >= config.minimum_prior_usable_matches
            for value in matches
        )
        and all(
            value is not None and value >= config.minimum_prior_service_points
            for value in serve
        )
        and all(
            value is not None and value >= config.minimum_prior_return_points
            for value in returns
        )
    )


def substantial_disagreement(row: Mapping[str, Any], threshold: float = 0.10) -> bool:
    a1 = _number(row, "a1_p1")
    a2 = _number(row, "a2_p1")
    if a1 is None or a2 is None:
        return False
    return abs(a1 - a2) >= threshold


CATEGORIES: tuple[SpecialistCategory, ...] = (
    SpecialistCategory(
        "WELL_SUPPORTED_BOTH",
        "Both players have at least 10 prior matches and 500 serve and return points.",
        well_supported,
    ),
    SpecialistCategory(
        "MINIMUM_SUPPORTED",
        "Both players clear the production Agent 2 minimum history gate.",
        minimum_supported,
    ),
    SpecialistCategory(
        "SUBSTANTIAL_DISAGREEMENT",
        "Agent 1 and Agent 2 differ by at least 0.10 on participant-1 probability.",
        substantial_disagreement,
    ),
    SpecialistCategory(
        "WELL_SUPPORTED_AND_DISAGREEMENT",
        "Well-supported history and at least 0.10 Agent 1/Agent 2 disagreement.",
        lambda row: well_supported(row) and substantial_disagreement(row),
    ),
)


def audit_architecture(source_text: str | None = None) -> dict[str, Any]:
    """Report existing mechanisms from the real configuration and source."""
    if source_text is None:
        source_text = inspect.getsource(agent2_module)
    config = agent2_module.Agent2Config()
    lowered = source_text.casefold()
    detected = {
        name: any(token in lowered for token in tokens)
        for name, tokens in MECHANISM_TOKENS.items()
    }
    return {
        "version": SPECIALIST_AUDIT_VERSION,
        "agent_code": agent2_module.AGENT_CODE,
        "present": {
            "surface_and_tour_shrinkage": True,
            "shrinkage_constants": {
                "k_serve_overall": str(config.k_serve_overall),
                "k_serve_surface": str(config.k_serve_surface),
                "k_return_overall": str(config.k_return_overall),
                "k_return_surface": str(config.k_return_surface),
            },
            "minimum_prior_usable_matches": config.minimum_prior_usable_matches,
            "minimum_prior_service_points": config.minimum_prior_service_points,
            "minimum_prior_return_points": config.minimum_prior_return_points,
            "one_sided_history_abstains": "ONE_SIDED_HISTORY" in source_text,
            "pit_state_gate": "PIT_STATE_UNRESOLVED" in source_text,
            "deterministic_surface_resolution": "resolve_missing_match_surfaces"
                in inspect.getsource(agent2_history_module),
            "scoring_rules_versioned": "ScoringRuleVersion" in source_text,
        },
        "not_implemented": {
            name: not present for name, present in detected.items()
        },
        "not_implemented_note": (
            "Recency/inactivity weighting, opponent-strength adjustment and "
            "explicit statistical uncertainty are not present in the inspected "
            "Agent 2 implementation. They are candidate extensions, not claims."
        ),
        "source_tokens": detected,
    }


def _binary_metrics(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "accuracy": None, "brier": None, "log_loss": None}
    correct = 0
    brier = 0.0
    log_loss = 0.0
    for row in rows:
        p1 = float(row[key])
        outcome = 1.0 if int(row["winner"]) == 1 else 0.0
        correct += int((p1 > 0.5) == (int(row["winner"]) == 1))
        brier += (p1 - outcome) ** 2
        log_loss -= outcome * math.log(max(p1, EPSILON)) + (1 - outcome) * math.log(
            max(1 - p1, EPSILON)
        )
    return {
        "n": len(rows),
        "accuracy": correct / len(rows),
        "brier": brier / len(rows),
        "log_loss": log_loss / len(rows),
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


def _paired_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    *,
    left: str = "a1_p1",
    right: str = "a2_p1",
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "brier_improvement_95": None, "accuracy_improvement_95": None}
    rng = random.Random(seed)
    brier: list[float] = []
    accuracy: list[float] = []
    for _ in range(iterations):
        sample = [rows[rng.randrange(len(rows))] for _ in rows]
        brier.append(
            sum((float(row[left]) - (1 if row["winner"] == 1 else 0)) ** 2 for row in sample)
            / len(sample)
            - sum((float(row[right]) - (1 if row["winner"] == 1 else 0)) ** 2 for row in sample)
            / len(sample)
        )
        accuracy.append(
            sum(int((float(row[left]) > 0.5) == (row["winner"] == 1)) for row in sample)
            / len(sample)
            - sum(int((float(row[right]) > 0.5) == (row["winner"] == 1)) for row in sample)
            / len(sample)
        )
    return {
        "n": len(rows),
        "brier_improvement_95": [_quantile(brier, 0.025), _quantile(brier, 0.975)],
        "accuracy_improvement_95": [_quantile(accuracy, 0.025), _quantile(accuracy, 0.975)],
    }


def evaluate_category(
    rows: Sequence[Mapping[str, Any]],
    category: SpecialistCategory,
    *,
    require_agent2: bool = True,
) -> dict[str, Any]:
    """Evaluate one specialist category with an explicit support gate."""
    selected = [
        row for row in rows
        if int(row.get("winner") or 0) in (1, 2)
        and _number(row, "a1_p1") is not None
        and (not require_agent2 or _number(row, "a2_p1") is not None)
        and category.predicate(row)
    ]
    if not selected:
        return {
            "category": category.code,
            "description": category.description,
            "n": 0,
            "verdict": "INSUFFICIENT_EVIDENCE",
            "available": False,
        }
    a1_metrics = _binary_metrics(selected, "a1_p1")
    a2_metrics = _binary_metrics(selected, "a2_p1") if require_agent2 else None
    paired = _paired_bootstrap(selected) if require_agent2 else None
    a1_only = sum(
        1 for row in selected
        if (float(row["a1_p1"]) > 0.5) == (row["winner"] == 1)
        and (float(row["a2_p1"]) > 0.5) != (row["winner"] == 1)
    ) if require_agent2 else 0
    a2_only = sum(
        1 for row in selected
        if (float(row["a2_p1"]) > 0.5) == (row["winner"] == 1)
        and (float(row["a1_p1"]) > 0.5) != (row["winner"] == 1)
    ) if require_agent2 else 0
    gate = (
        len(selected) >= MIN_CATEGORY_FIXTURES
        and min(a1_only, a2_only) >= MIN_DIRECTION_COUNT
        and paired is not None
        and paired["brier_improvement_95"] is not None
        # Brier improvement is A1 minus A2. A2 is supported only when the
        # entire interval is strictly positive (A2 has the lower Brier).
        and paired["brier_improvement_95"][0] > 0
    )
    return {
        "category": category.code,
        "description": category.description,
        "n": len(selected),
        "a1": a1_metrics,
        "a2": a2_metrics,
        "paired": paired,
        "disagreement": {
            "a1_only_correct": a1_only,
            "a2_only_correct": a2_only,
            "mcnemar_exact": _mcnemar(a1_only, a2_only),
        },
        "gate": {
            "minimum_fixtures": MIN_CATEGORY_FIXTURES,
            "minimum_direction_count": MIN_DIRECTION_COUNT,
            "brier_ci_excludes_zero_in_a2_favour": bool(
                gate
            ),
        },
        "available": True,
        "verdict": (
            "A2_INCREMENTAL_VALUE_SUPPORTED" if gate else "INSUFFICIENT_EVIDENCE"
        ),
    }


def _mcnemar(b: int, c: int) -> dict[str, Any]:
    n = b + c
    if n == 0:
        return {"b": 0, "c": 0, "p_value": 1.0}
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / (2 ** n)
    return {"b": b, "c": c, "p_value": min(1.0, 2 * tail)}


def specialist_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "version": SPECIALIST_AUDIT_VERSION,
        "architecture": audit_architecture(),
        "categories": [
            evaluate_category(rows, category) for category in CATEGORIES
        ],
        "agent3_gate_relaxed": False,
        "grand_slam_bo5_coordination": "PR_389",
        "provider_calls": 0,
        "limitations": [
            "An unavailable category is not evidence against Agent 2; it is a "
            "coverage limit.",
            "Categories were defined before the measured comparison.",
            "The specialist gate requires a paired Brier interval entirely in "
            "Agent 2's favour; a point estimate alone is not enough.",
            "Agent 3 support is never relaxed to increase participation.",
        ],
    }


def rows_from_predictions(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize stored first-issue agent records for this audit."""
    out = []
    for record in records:
        if record.get("winner") not in (1, 2):
            continue
        out.append({
            "match_id": record.get("match_id"),
            "winner": int(record["winner"]),
            "a1_p1": _number(record, "a1_p1"),
            "a2_p1": _number(record, "a2_p1"),
            "p1_matches": _number(record, "p1_matches"),
            "p2_matches": _number(record, "p2_matches"),
            "p1_service_points": _number(record, "p1_service_points"),
            "p2_service_points": _number(record, "p2_service_points"),
            "p1_return_points": _number(record, "p1_return_points"),
            "p2_return_points": _number(record, "p2_return_points"),
        })
    return out
