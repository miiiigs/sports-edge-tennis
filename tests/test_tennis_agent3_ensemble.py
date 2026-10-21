from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from research.a2_calibration_shadow.calibrators import CalibrationModel
from research.tennis_agent3_ensemble_20261021.analysis import analyze
from research.tennis_agent3_ensemble_20261021.ensemble_shadow import (
    TEMPERATURE_SLOPE,
    evaluate_rows,
    summarize,
)
from research.tennis_agent3_ensemble_20261021.policy import (
    AGENT_1,
    AGENT_2,
    AGENT_3,
    AgentVote,
    PolicyConfig,
    simulate_manager,
)
from sports_edge_tennis.data.prediction_persistence import (
    PersistedPredictiveAgentPrediction,
)
from sports_edge_tennis.manager.consensus import evaluate_consensus
from sports_edge_tennis.manager.contracts import ManagerPolicyV1

AS_OF = datetime(2026, 10, 21, tzinfo=timezone.utc)


def _policy(**overrides) -> PolicyConfig:
    values = {
        "policy_version": "v1",
        "allow_agent3_abstention": True,
        "three_agent_max_probability_spread": Decimal("0.08"),
        "agent3_abstention_max_probability_spread": Decimal("0.06"),
        "min_active_agent_probability": Decimal("0.50"),
    }
    values.update(overrides)
    return PolicyConfig(**values)


def _votes(a1, a2, a3) -> dict[str, AgentVote]:
    def vote(code, value):
        status, side, probability = value
        return AgentVote(code, status, side, probability)

    return {
        AGENT_1: vote(AGENT_1, a1),
        AGENT_2: vote(AGENT_2, a2),
        AGENT_3: vote(AGENT_3, a3),
    }


def _persisted(code, value) -> PersistedPredictiveAgentPrediction:
    status, side, probability = value
    return PersistedPredictiveAgentPrediction(
        prediction_id=1,
        prediction_run_id=1,
        match_id=1,
        agent_code=code,
        prediction_status=status,
        selection_side=side,
        win_probability=probability,
        probability_representation=(
            {
                AGENT_1: "agent1_raw_probability_v1",
                AGENT_2: "agent2_raw_probability_v1",
                AGENT_3: "agent3_raw_probability_v1",
            }[code]
            if status == "PREDICTED"
            else None
        ),
        model_version="test",
        feature_version="test",
        config_version="test",
        parameter_schema_version="test",
        agent_config_version_id=1,
        as_of=AS_OF,
    )


@pytest.mark.parametrize(
    "case",
    [
        ("all_agree", (("PREDICTED", 1, Decimal("0.70")), ("PREDICTED", 1, Decimal("0.72")), ("PREDICTED", 1, Decimal("0.68"))), _policy()),
        ("three_spread", (("PREDICTED", 1, Decimal("0.90")), ("PREDICTED", 1, Decimal("0.70")), ("PREDICTED", 1, Decimal("0.60"))), _policy()),
        ("a3_abstain", (("PREDICTED", 1, Decimal("0.70")), ("PREDICTED", 1, Decimal("0.72")), ("ABSTAINED", None, None)), _policy()),
        ("a3_abstain_spread", (("PREDICTED", 1, Decimal("0.70")), ("PREDICTED", 1, Decimal("0.80")), ("ABSTAINED", None, None)), _policy()),
        ("a2_abstain", (("PREDICTED", 1, Decimal("0.70")), ("ABSTAINED", None, None), ("PREDICTED", 1, Decimal("0.72"))), _policy()),
        ("a1_abstain", (("ABSTAINED", None, None), ("PREDICTED", 1, Decimal("0.70")), ("PREDICTED", 1, Decimal("0.72"))), _policy()),
        ("two_abstain", (("PREDICTED", 1, Decimal("0.70")), ("ABSTAINED", None, None), ("ABSTAINED", None, None)), _policy()),
        ("directional", (("PREDICTED", 1, Decimal("0.70")), ("PREDICTED", 2, Decimal("0.72")), ("PREDICTED", 1, Decimal("0.68"))), _policy()),
        ("below_floor", (("PREDICTED", 1, Decimal("0.49")), ("PREDICTED", 1, Decimal("0.72")), ("PREDICTED", 1, Decimal("0.68"))), _policy()),
        ("v2_a3_abstain", (("PREDICTED", 1, Decimal("0.70")), ("PREDICTED", 1, Decimal("0.72")), ("ABSTAINED", None, None)), _policy(policy_version="v2", allow_agent3_abstention=False)),
    ],
)
def test_simulated_policy_matches_production(case):
    name, values, policy = case
    votes = _votes(*values)
    decision = simulate_manager(votes, policy=policy)
    production_policy = ManagerPolicyV1(
        manager_policy_version_id=1,
        policy_key="tennis_manager_consensus",
        policy_version=policy.policy_version,
        three_agent_max_probability_spread=policy.three_agent_max_probability_spread,
        agent3_abstention_max_probability_spread=policy.agent3_abstention_max_probability_spread,
        min_active_agent_probability=policy.min_active_agent_probability,
        allow_agent3_abstention=policy.allow_agent3_abstention,
    )
    production = evaluate_consensus(
        [
            _persisted(AGENT_1, values[0]),
            _persisted(AGENT_2, values[1]),
            _persisted(AGENT_3, values[2]),
        ],
        match_id=1,
        prediction_run_id=1,
        policy=production_policy,
        as_of=AS_OF,
    )
    assert decision.manager_status == production.manager_status, name
    assert decision.selected_side == production.selected_side, name
    assert decision.reason_code == production.reason_code, name
    assert decision.active_agent_count == production.active_agent_count, name
    assert decision.abstaining_agent_count == production.abstaining_agent_count, name
    assert decision.probability_spread == production.probability_spread, name
    assert decision.consensus_probability == production.consensus_probability, name


def test_temperature_calibration_preserves_side_and_is_monotone():
    model = CalibrationModel(
        name="temperature",
        kind="temperature",
        parameters={"slope": TEMPERATURE_SLOPE},
    )
    values = [0.50, 0.55, 0.60, 0.70, 0.80, 0.90, 0.99]
    calibrated = [model.predict(value) for value in values]
    assert calibrated == sorted(calibrated)
    assert all(0.5 <= value <= 1.0 for value in calibrated)
    assert all(value < original for value, original in zip(calibrated, values) if original > 0.5)
    assert model.predict(0.5) == pytest.approx(0.5)


def _row(
    *,
    match_id: int,
    a1: tuple[str, int | None, float | None],
    a2: tuple[str, int | None, float | None],
    a3: tuple[str, int | None, float | None],
    winner_side: int | None,
    outcome_status: str = "COMPLETED",
) -> dict:
    return {
        "match_id": match_id,
        "prediction_run_id": match_id,
        "manager_as_of": AS_OF.isoformat(),
        "surface": "HARD",
        "competition_tier": "ATP_TOUR",
        "policy_version": "v1",
        "allow_agent3_abstention": True,
        "three_agent_max_probability_spread": "0.08",
        "agent3_abstention_max_probability_spread": "0.06",
        "min_active_agent_probability": "0.50",
        "a1_status": a1[0],
        "a1_side": a1[1],
        "a1_probability": a1[2],
        "a2_status": a2[0],
        "a2_side": a2[1],
        "a2_probability": a2[2],
        "a3_status": a3[0],
        "a3_side": a3[1],
        "a3_probability": a3[2],
        "outcome_status": outcome_status,
        "winner_side": winner_side,
    }


def test_shadow_records_gate_flips_and_selection_changes():
    rows = [
        # Baseline NO_BET on the 0.06 A3-abstention spread; calibrated A2
        # compresses the spread and creates a new consensus on side 1.
        _row(
            match_id=1,
            a1=("PREDICTED", 1, 0.66),
            a2=("PREDICTED", 1, 0.80),
            a3=("ABSTAINED", None, None),
            winner_side=1,
        ),
        # Baseline consensus on side 1; calibrated A2 is the minimum and
        # widens the spread past the 0.06 gate, losing the consensus.
        _row(
            match_id=2,
            a1=("PREDICTED", 1, 0.65),
            a2=("PREDICTED", 1, 0.60),
            a3=("ABSTAINED", None, None),
            winner_side=1,
        ),
        # Three active; calibration cannot rescue the locked 0.08 spread here.
        _row(
            match_id=3,
            a1=("PREDICTED", 1, 0.90),
            a2=("PREDICTED", 1, 0.80),
            a3=("PREDICTED", 1, 0.54),
            winner_side=1,
        ),
    ]
    evaluated = evaluate_rows(rows)
    assert evaluated[0]["gate_flip"] == "NEW_CONSENSUS"
    assert evaluated[1]["gate_flip"] == "LOST_CONSENSUS"
    assert evaluated[0]["selection_changed"] is False
    assert evaluated[1]["selection_changed"] is False
    assert all(row["selection_changed"] is False for row in evaluated)
    summary = summarize(evaluated)
    assert summary["decisions"]["selection_changes"] == 0
    assert summary["decisions"]["gate_flips"]["NEW_CONSENSUS"] == 1
    assert summary["decisions"]["gate_flips"]["LOST_CONSENSUS"] == 1
    assert summary["new_consensus"]["accuracy"] == 1.0
    assert summary["lost_consensus"]["baseline_wins"] == 1


def test_shadow_decisions_do_not_read_outcomes():
    base = _row(
        match_id=10,
        a1=("PREDICTED", 1, 0.66),
        a2=("PREDICTED", 1, 0.80),
        a3=("ABSTAINED", None, None),
        winner_side=1,
    )
    other = dict(base, winner_side=2, outcome_status="RETIRED")
    first = evaluate_rows([base])[0]
    second = evaluate_rows([other])[0]
    for key in (
        "baseline_status",
        "baseline_side",
        "baseline_probability",
        "baseline_spread",
        "baseline_reason",
        "candidate_status",
        "candidate_side",
        "candidate_probability",
        "candidate_spread",
        "candidate_reason",
        "gate_flip",
    ):
        assert first[key] == second[key]


def test_a3_abstention_classification_and_contribution():
    evaluations = [
        {
            "prediction_id": 1,
            "match_id": 1,
            "prediction_run_id": 1,
            "as_of": "2026-10-01T00:00:00+00:00",
            "a3_status": "ABSTAINED",
            "a3_side": None,
            "a3_probability": None,
            "a3_model_version": "agent3_intransitivity_v1",
            "a3_abstain_reason": "INSUFFICIENT_MATCHUP_SUPPORT",
            "a1_status": "PREDICTED",
            "a1_side": 1,
            "a1_probability": "0.70",
            "a2_status": "PREDICTED",
            "a2_side": 1,
            "a2_probability": "0.65",
            "surface": "HARD",
            "competition_tier": "ATP_TOUR",
            "outcome_status": "COMPLETED",
            "winner_side": 1,
        },
        {
            "prediction_id": 2,
            "match_id": 2,
            "prediction_run_id": 2,
            "as_of": "2026-10-02T00:00:00+00:00",
            "a3_status": "PREDICTED",
            "a3_side": 2,
            "a3_probability": "0.58",
            "a3_model_version": "agent3_intransitivity_v1",
            "a3_abstain_reason": None,
            "a1_status": "PREDICTED",
            "a1_side": 2,
            "a1_probability": "0.61",
            "a2_status": "PREDICTED",
            "a2_side": 2,
            "a2_probability": "0.66",
            "surface": "CLAY",
            "competition_tier": "CHALLENGER",
            "outcome_status": "COMPLETED",
            "winner_side": 2,
        },
    ]
    diagnostics = [
        {
            "match_id": 1,
            "evaluated_at": "2026-10-01T00:00:00+00:00",
            "identity_status": "RESOLVED",
            "direct_raw_support": 0,
            "direct_effective_support": "0",
            "raw_shared_opponents": 10,
            "eligible_shared_opponents": 0,
            "q_count": "0",
            "support_path": None,
            "gate_failed": "INSUFFICIENT_MATCHUP_SUPPORT",
            "final_status": "ABSTAINED",
            "surface": "HARD",
        },
        {
            "match_id": 2,
            "evaluated_at": "2026-10-02T00:00:00+00:00",
            "identity_status": "RESOLVED",
            "direct_raw_support": 3,
            "direct_effective_support": "2.7",
            "raw_shared_opponents": 4,
            "eligible_shared_opponents": 4,
            "q_count": "6",
            "support_path": "DIRECT_PLUS_NETWORK",
            "gate_failed": None,
            "final_status": "PREDICTED",
            "surface": "CLAY",
        },
    ]
    ensemble = [
        {
            **_row(
                match_id=2,
                a1=("PREDICTED", 2, 0.61),
                a2=("PREDICTED", 2, 0.66),
                a3=("PREDICTED", 2, 0.58),
                winner_side=2,
            ),
                "manager_status": "NO_BET",
                "manager_reason_code": "THREE_AGENT_SPREAD_EXCEEDED",
                "manager_probability_spread": "0.08",
                "active_agent_count": 3,
            }
    ]
    result = analyze(
        a3_evaluations=evaluations,
        a3_diagnostics=diagnostics,
        ensemble_rows=ensemble,
    )
    assert result["coverage"]["evaluation_rows"] == 2
    assert result["coverage"]["unique_matches"] == 2
    assert result["coverage"]["unique_predicted_matches"] == 1
    assert result["abstentions"]["diagnostics"]["gate_decomposition"]["direct_raw_zero"] == 1
    assert result["abstentions"]["diagnostics"]["gate_decomposition"]["direct_gate_pass"] == 1
    assert result["contribution"]["scored_matches"] == 1
    assert result["contribution"]["wins"] == 1
    assert result["contribution"]["three_active"]["a3_side_equals_a1"] == 1
    assert result["contribution"]["three_active"]["a3_side_equals_a1_rate"] == 1.0


def test_forward_shadow_append_is_idempotent():
    from research.tennis_agent3_ensemble_20261021.forward_shadow import append_new

    row = evaluate_rows(
        [
            _row(
                match_id=20,
                a1=("PREDICTED", 1, 0.66),
                a2=("PREDICTED", 1, 0.80),
                a3=("ABSTAINED", None, None),
                winner_side=1,
            )
        ]
    )[0]
    new_rows, duplicates = append_new([row], [])
    assert len(new_rows) == 1
    assert duplicates == 0
    again, duplicates = append_new([row], new_rows)
    assert again == []
    assert duplicates == 1
