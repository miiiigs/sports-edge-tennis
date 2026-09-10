from decimal import Decimal

from sports_edge_tennis.market.contracts import MarketPolicyV1, SUPPORTED_ATP_TIERS
from sports_edge_tennis.market.policy import resolve_threshold


def _policy() -> MarketPolicyV1:
    return MarketPolicyV1(
        market_policy_version_id=1,
        policy_key="tennis_market_value",
        policy_version="v1",
        atp_main_min_edge_pct=Decimal("0.05"),
        challenger_min_edge_pct=Decimal("0.08"),
        long_price_threshold=Decimal("4.00"),
        long_price_min_edge_pct=Decimal("0.08"),
        default_devig_method="POWER",
        sharp_reference_policy_code="PINNACLE_SHARP",
        odds_max_staleness_minutes=15,
        type_b_enabled=False,
    )


def test_generic_atp_tour_uses_locked_atp_main_threshold():
    assert "ATP_TOUR" in SUPPORTED_ATP_TIERS
    assert resolve_threshold(
        _policy(),
        "ATP_TOUR",
        Decimal("2.00"),
        Decimal("2.00"),
    ) == Decimal("0.05")


def test_generic_atp_tour_keeps_long_price_eight_percent_rule():
    assert resolve_threshold(
        _policy(),
        "ATP_TOUR",
        Decimal("4.20"),
        Decimal("1.25"),
    ) == Decimal("0.08")
