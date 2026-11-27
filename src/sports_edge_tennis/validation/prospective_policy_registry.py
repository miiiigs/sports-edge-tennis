"""Canonical prospective policy registry, not an execution or activation gate.

Historical research letters belong to their original experiment namespaces.
The four prospective IDs are new, immutable identifiers; never rename an
existing persisted experiment or substitute an original A1 probability for a
corrected forecast. This module does not load model data or execute an agent.
"""
from dataclasses import dataclass
from types import MappingProxyType

from sports_edge_tennis.validation.a1_selective_performance import (
    apply_frozen_threshold, confidence, selection_side,
)
from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES, _available_agents, _policy,
)

MANAGER_RULE = "C_uncalibrated_mean_floor60"
CHECKPOINTS = (25, 50, 100, 300, 600, 1000, 1500, 2000)
MIN_CALENDAR_DAYS = 30
MIN_FIRST_ISSUED_SETTLED = 200


@dataclass(frozen=True)
class Policy:
    candidate: str
    experiment_id: str
    selection_policy: str
    threshold: str | None


POLICIES = MappingProxyType({
    "A": Policy("A", "prospective-production-as-issued-v1",
                "existing_runner_manager_v1", None),
    "B": Policy("B", "prospective-corrected-a1-primary-v1",
                "a1_selective_frozen_six_decimal_v1", "0.668060"),
    "C": Policy("C", "prospective-corrected-manager-c-narrow-v1",
                "manager_c_uncalibrated_mean_floor60_narrow_v1", "0.7070145"),
    "D": Policy("D", "prospective-corrected-a1-secondary-v1",
                "a1_selective_frozen_six_decimal_v1", "0.648708"),
})


@dataclass(frozen=True)
class Decision:
    experiment_id: str
    status: str
    p1: float | None
    side: int | None
    threshold: str | None


def _decision(policy, status, p1=None, side=None):
    return Decision(policy.experiment_id, status, p1, side, policy.threshold)


def corrected_a1_decisions(*, status: str, p1: float | None):
    """Both thresholds consume one corrected output; no replay or model call."""
    if status in ("MISSING", "ABSTAINED"):
        if p1 is not None:
            raise ValueError("non-forecast cannot carry a probability")
        return tuple(_decision(POLICIES[c], status) for c in ("B", "D"))
    if status != "PREDICTED" or type(p1) not in (float, int):
        raise ValueError("invalid corrected forecast")
    confidence(p1)  # Validate using the historical routine.
    results = []
    for c in ("B", "D"):
        policy = POLICIES[c]
        selected = bool(apply_frozen_threshold([{"p1": p1}], float(policy.threshold), "p1"))
        results.append(_decision(policy, "SELECTED" if selected else "BELOW_FROZEN_THRESHOLD",
                                 p1, selection_side(p1) if selected else None))
    return tuple(results)


def corrected_manager_c_decision(*, corrected_a1_status, corrected_a1_p1, other_agents):
    """Apply the already-frozen narrow Manager C rule to explicit agent inputs.

    other_agents uses the existing historical raw (status, side, confidence)
    contract. Missing and abstaining A1 are fail-closed; never use original A1
    as a fallback. Missing other agent rows are not silently treated as valid.
    All inputs are still unqualified until the actual runtime lineage is proven.
    """
    policy = POLICIES["C"]
    expected = set(CODES[1:])
    if not isinstance(other_agents, dict) or set(other_agents) - expected:
        raise ValueError("only explicit Agent 2/3 inputs permitted")
    a1 = corrected_a1_decisions(status=corrected_a1_status, p1=corrected_a1_p1)
    if corrected_a1_status != "PREDICTED":
        return _decision(policy, a1[0].status)
    if set(other_agents) != expected:
        return _decision(policy, "MISSING")
    active = _available_agents(other_agents)
    p1 = corrected_a1_p1
    if not 0 < p1 < 1:
        raise ValueError("Manager C probability must be strictly between zero and one")
    values = {CODES[0]: (selection_side(p1), p1)}
    values.update(active)
    pick = _policy(values)[MANAGER_RULE]
    if pick is None:
        return _decision(policy, "ABSTAINED")
    side, probability = pick
    if max(probability, 1 - probability) + 1e-12 < float(policy.threshold):
        return _decision(policy, "BELOW_FROZEN_THRESHOLD", probability)
    return _decision(policy, "SELECTED", probability, side)
