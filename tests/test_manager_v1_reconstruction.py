from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sports_edge_tennis.manager.contracts import ManagerPolicyV1
from sports_edge_tennis.validation.manager_v1_reconstruction import reconstruct_manager_v1

AS_OF = datetime(2026, 9, 15, tzinfo=UTC)
POLICY = ManagerPolicyV1(
    manager_policy_version_id=1,
    policy_key="tennis_manager_consensus",
    policy_version="v1",
    three_agent_max_probability_spread=Decimal("0.05"),
    agent3_abstention_max_probability_spread=Decimal("0.05"),
    min_active_agent_probability=Decimal("0.50"),
    allow_agent3_abstention=True,
)


@dataclass
class _Agent12:
    prediction_status: str = "PREDICTED"
    selection_side: int | None = 1
    win_probability: Decimal | None = Decimal("0.60")
    model_version: str = "m"
    feature_version: str = "f"
    config_version: str = "c"
    parameter_schema_version: str = "p"


@dataclass
class _Agent3:
    prediction_status: str = "PREDICTED"
    selected_side: int | None = 1
    selected_probability: Decimal | None = Decimal("0.58")
    model_version: str = "m"
    feature_version: str = "f"
    config_version: str = "c"
    parameter_schema_version: str = "p"


def _run(a1, a2, a3):
    return reconstruct_manager_v1(
        match_id=1,
        prediction_run_id=1,
        agent1=a1,
        agent2=a2,
        agent3=a3,
        policy=POLICY,
        as_of=AS_OF,
    )


def test_three_agent_consensus() -> None:
    result = _run(_Agent12(), _Agent12(win_probability=Decimal("0.62")), _Agent3())
    assert result.manager_status == "CONSENSUS"
    assert result.selected_side == 1
    assert result.active_agent_count == 3
    assert result.reason_code == "NONE"


def test_agent3_abstention_path_consensus() -> None:
    abstained = _Agent3(prediction_status="ABSTAINED", selected_side=None, selected_probability=None)
    result = _run(_Agent12(), _Agent12(win_probability=Decimal("0.58")), abstained)
    assert result.manager_status == "CONSENSUS"
    assert result.active_agent_count == 2
    assert result.abstaining_agent_count == 1


def test_directional_disagreement_is_no_bet() -> None:
    result = _run(_Agent12(), _Agent12(selection_side=2), _Agent3())
    assert result.manager_status == "NO_BET"
    assert result.reason_code == "DIRECTIONAL_DISAGREEMENT"


def test_two_abstentions_is_no_bet() -> None:
    a3 = _Agent3(prediction_status="ABSTAINED", selected_side=None, selected_probability=None)
    result = _run(_Agent12(prediction_status="ABSTAINED", selection_side=None, win_probability=None), _Agent12(), a3)
    assert result.manager_status == "NO_BET"
    assert result.reason_code == "TWO_OR_MORE_ABSTENTIONS"
