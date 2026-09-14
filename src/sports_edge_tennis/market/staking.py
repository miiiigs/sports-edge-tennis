from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sports_edge_tennis.market.contracts import MarketExecutionError


STAKING_RULE_VERSION = "QUARTER_KELLY_CAP_2_V1"
STAKE_FRACTION_CAP = Decimal("0.02")
STAKE_FRACTION_QUANTIZER = Decimal("0.000001")


@dataclass(frozen=True)
class StakeSuggestionV1:
    staking_rule_version: str
    staking_probability: Decimal
    staking_decimal_price: Decimal
    full_kelly_fraction: Decimal
    quarter_kelly_fraction: Decimal
    suggested_stake_fraction: Decimal


def quantize_stake_fraction(value: Decimal) -> Decimal:
    return value.quantize(STAKE_FRACTION_QUANTIZER)


def quarter_kelly_stake_fraction(
    *,
    probability: Decimal,
    decimal_price: Decimal,
) -> StakeSuggestionV1:
    if not isinstance(probability, Decimal) or not isinstance(decimal_price, Decimal):
        raise MarketExecutionError("staking inputs must be Decimal values")
    if probability < 0 or probability > 1:
        raise MarketExecutionError("staking probability must be within [0, 1]")
    if decimal_price <= 1:
        raise MarketExecutionError("staking decimal_price must be greater than 1")

    full_kelly = ((probability * decimal_price) - Decimal("1")) / (decimal_price - Decimal("1"))
    quarter_kelly = Decimal("0.25") * full_kelly
    stake_fraction = min(max(quarter_kelly, Decimal("0")), STAKE_FRACTION_CAP)

    return StakeSuggestionV1(
        staking_rule_version=STAKING_RULE_VERSION,
        staking_probability=probability,
        staking_decimal_price=decimal_price,
        full_kelly_fraction=quantize_stake_fraction(full_kelly),
        quarter_kelly_fraction=quantize_stake_fraction(quarter_kelly),
        suggested_stake_fraction=quantize_stake_fraction(stake_fraction),
    )
