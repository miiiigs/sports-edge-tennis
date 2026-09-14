from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sports_edge_tennis.veto.integration import _mark_decision_vetoed


@dataclass(frozen=True)
class _DecisionStub:
    decision_status: str = "BET"
    reason_code: str = "NONE"
    staking_rule_version: str | None = "QUARTER_KELLY_CAP_2_V1"
    staking_probability: Decimal | None = Decimal("0.60")
    staking_decimal_price: Decimal | None = Decimal("2.00")
    full_kelly_fraction: Decimal | None = Decimal("0.20")
    quarter_kelly_fraction: Decimal | None = Decimal("0.05")
    suggested_stake_fraction: Decimal | None = Decimal("0.02")


def test_veto_clears_executable_stake_suggestion():
    vetoed = _mark_decision_vetoed(_DecisionStub())

    assert vetoed.decision_status == "VETOED"
    assert vetoed.reason_code == "VETO_TRIGGERED"
    assert vetoed.staking_rule_version is None
    assert vetoed.staking_probability is None
    assert vetoed.staking_decimal_price is None
    assert vetoed.full_kelly_fraction is None
    assert vetoed.quarter_kelly_fraction is None
    assert vetoed.suggested_stake_fraction is None
