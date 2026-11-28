"""Frozen policy semantics only; no real prospective forecast claims."""
from dataclasses import FrozenInstanceError

import pytest

from sports_edge_tennis.validation.a1_selective_performance import apply_frozen_threshold
from sports_edge_tennis.validation.manager_abc_first_issue_ablation import CODES, _policy
from sports_edge_tennis.validation.prospective_manager_shadow_capture import FROZEN
from sports_edge_tennis.validation.prospective_policy_registry import (
    POLICIES, CHECKPOINTS, corrected_a1_decisions, corrected_manager_c_decision,
)


def test_distinct_immutable_candidate_ids_and_checkpoints():
    assert len({p.experiment_id for p in POLICIES.values()}) == 4
    assert tuple(POLICIES) == ("A", "B", "C", "D")
    assert POLICIES["B"].threshold == "0.668060"
    assert POLICIES["D"].threshold == "0.648708"
    assert float(POLICIES["C"].threshold) == FROZEN["C_uncalibrated_mean_floor60"]["threshold"]
    assert CHECKPOINTS == (25, 50, 100, 300, 600, 1000, 1500, 2000)
    with pytest.raises(FrozenInstanceError):
        POLICIES["B"].threshold = "0.5"
    with pytest.raises(TypeError):
        POLICIES["B"] = POLICIES["D"]


@pytest.mark.parametrize("p1", [0.5, 0.6680594, 0.6680596, 0.668060,
                                0.6487074, 0.6487076, 0.648708, 0.66,
                                1-0.668060, 1-0.648708, 0.8, 0.2])
def test_b_d_exact_historical_threshold_semantics(p1):
    primary, secondary = corrected_a1_decisions(status="PREDICTED", p1=p1)
    assert primary.p1 == secondary.p1 == p1
    for decision in (primary, secondary):
        selected = bool(apply_frozen_threshold([{"p": p1}], float(decision.threshold), "p"))
        assert (decision.status == "SELECTED") == selected
        assert decision.side == ((1 if p1 >= 0.5 else 2) if selected else None)


def test_distinct_decisions_and_explicit_missing():
    b, d = corrected_a1_decisions(status="PREDICTED", p1=0.66)
    assert b.status == "BELOW_FROZEN_THRESHOLD"
    assert d.status == "SELECTED"
    for status in ("MISSING", "ABSTAINED"):
        assert all(x.status == status for x in corrected_a1_decisions(status=status, p1=None))
    with pytest.raises(ValueError):
        corrected_a1_decisions(status="MISSING", p1=0.8)


def test_manager_c_uses_corrected_probability_and_frozen_narrow_rule():
    others = {CODES[1]: ("PREDICTED", 1, 0.8),
              CODES[2]: ("ABSTAINED", None, None)}
    result = corrected_manager_c_decision(corrected_a1_status="PREDICTED",
                                          corrected_a1_p1=0.7, other_agents=others)
    expected = _policy({CODES[0]: (1, 0.7), CODES[1]: (1, 0.8)})["C_uncalibrated_mean_floor60"]
    assert result.status == "SELECTED"
    assert (result.side, result.p1) == expected
    assert corrected_manager_c_decision(corrected_a1_status="MISSING",
                                        corrected_a1_p1=None, other_agents=others).status == "MISSING"
    assert corrected_manager_c_decision(corrected_a1_status="PREDICTED",
                                        corrected_a1_p1=0.7, other_agents={}).status == "MISSING"
    assert corrected_manager_c_decision(corrected_a1_status="PREDICTED",
                                        corrected_a1_p1=0.7, other_agents={
        CODES[1]: ("ABSTAINED", None, None),
        CODES[2]: ("ABSTAINED", None, None),
    }).status == "ABSTAINED"
