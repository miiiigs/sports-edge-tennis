from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sports_edge_tennis.market.contracts import (
    SUPPORTED_ATP_TIERS,
    MarketPolicyError,
    MarketPolicyV1,
)


def resolve_threshold(
    policy: MarketPolicyV1,
    competition_tier: str,
    reference_player_1_price: Decimal | None,
    reference_player_2_price: Decimal | None,
) -> Decimal:
    """Resolve the Type A market edge threshold for a supported competition tier.

    Raises MarketPolicyError for an unsupported tier (the caller maps this to a
    NO_BET / UNSUPPORTED_TIER decision; it must never silently default to 5%).
    """
    if competition_tier in SUPPORTED_ATP_TIERS:
        base = policy.atp_main_min_edge_pct
    elif competition_tier == "CHALLENGER":
        base = policy.challenger_min_edge_pct
    else:
        raise MarketPolicyError(f"unsupported competition tier: {competition_tier!r}")

    if (
        reference_player_1_price is not None
        and reference_player_1_price > policy.long_price_threshold
    ):
        return max(base, policy.long_price_min_edge_pct)
    if (
        reference_player_2_price is not None
        and reference_player_2_price > policy.long_price_threshold
    ):
        return max(base, policy.long_price_min_edge_pct)
    return base


def is_fresh(source_observed_at: datetime, decision_as_of: datetime, ttl_minutes: int) -> bool:
    """Return True when the quote age (decision_as_of - source_observed_at) is within TTL."""
    return (decision_as_of - source_observed_at) <= timedelta(minutes=ttl_minutes)
