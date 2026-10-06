"""Presentation-layer translation of persisted backend truth to client wording.

Pure functions: no database access, no model recalculation. These map internal
codes to the language shown to a client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

from sports_edge_tennis.dashboard.timezone import (
    DEFAULT_TIMEZONE,
    DashboardTimezone,
    format_dashboard_time,
)

STATE_LABELS = {
    "MODEL_CONSENSUS": "Consensus",
    "NO_BET_MODEL": "No Bet — Model Filter",
    "SHARP_AVAILABLE": "Pinnacle Available",
    "SHARP_UNAVAILABLE": "Pinnacle Unavailable",
    "RECONCILIATION_FAILED": "Price Match Unavailable",
    "AWAITING_OFFERED_PRICE": "Awaiting Bookmaker Price",
    "AWAITING_MARKET": "Awaiting Market Decision",
    "BET": "Bet",
    "VETOED": "Vetoed",
    "NO_BET_MARKET": "No Bet · Market Filter",
    "PREDICTION_PENDING": "Prediction Pending",
}

REASON_EXPLANATIONS = {
    "TWO_OR_MORE_ABSTENTIONS": "Not enough model agreement.",
    "UNSUPPORTED_ABSTENTION_CONFIGURATION": "Not enough model agreement.",
    "DIRECTIONAL_DISAGREEMENT": "Models disagreed on the winner.",
    "ACTIVE_PROBABILITY_BELOW_MINIMUM": "Confidence threshold was not met.",
    "THREE_AGENT_SPREAD_EXCEEDED": "Model confidence was too inconsistent.",
    "AGENT3_ABSTENTION_SPREAD_EXCEEDED": "Primary models agreed, but confidence spread was too wide.",
    "NONE": None,
}

MARKET_REASON_EXPLANATIONS = {
    "NOT_MANAGER_ELIGIBLE": "Model consensus was not eligible for market evaluation.",
    "UNSUPPORTED_TIER": "Tournament tier is outside the current market policy.",
    "MISSING_SHARP_REFERENCE": "A complete Pinnacle reference price was not available.",
    "INCOMPLETE_MARKET": "The sharp match-winner market was incomplete.",
    "INVALID_PRICE": "The available market price was invalid.",
    "MARKET_SUSPENDED": "The market was not open.",
    "CROSSED_MARKET": "The sharp market failed the pricing sanity check.",
    "STALE_MARKET": "The available market price was too old.",
    "NO_MODEL_EDGE_VS_SHARP": "The model pick remains valid, but the sharp market does not currently confirm a positive price edge.",
    "MISSING_MARKET": "The model pick remains valid, but no fresh offered bookmaker price is available yet.",
    "EDGE_BELOW_THRESHOLD": "The model pick remains valid, but the current offered price is below the stricter value-bet threshold.",
    "PROBABILITY_FLOOR": "The model pick remains valid, but bets require at least 60% model win probability.",
    "VETO_TRIGGERED": "The pick qualified on model and price, but a safety-veto condition blocked publication.",
    "NONE": None,
}

TIER_LABELS = {
    "GRAND_SLAM": "Grand Slam",
    "MASTERS_1000": "ATP Masters 1000",
    "ATP_TOUR": "ATP Tour",
    "ATP_500": "ATP 500",
    "ATP_250": "ATP 250",
    "CHALLENGER": "Challenger",
}


def state_label(internal_state: str | None) -> str:
    return STATE_LABELS.get(internal_state or "", internal_state or "Unknown")


def reason_explanation(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    return REASON_EXPLANATIONS.get(reason_code)


def market_reason_explanation(reason_code: str | None) -> str | None:
    if reason_code is None:
        return None
    return MARKET_REASON_EXPLANATIONS.get(reason_code)


def tier_label(competition_tier: str | None) -> str:
    return TIER_LABELS.get(competition_tier or "", competition_tier or "—")


def client_state(
    manager_status: str | None,
    sharp_available: bool,
    decision_status: str | None = None,
) -> str:
    """Preserve the existing dashboard execution-state contract."""
    if manager_status != "CONSENSUS":
        return "NO_BET_MODEL"
    if decision_status == "BET":
        return "BET"
    if decision_status == "VETOED":
        return "VETOED"
    if decision_status == "NO_BET":
        return "NO_BET_MARKET"
    if sharp_available:
        return "AWAITING_OFFERED_PRICE"
    return "SHARP_UNAVAILABLE"


def recommendation_classification(
    manager_status: str | None,
    decision_status: str | None = None,
) -> str:
    """Classify model confidence separately from market value and safety.

    A VETOED decision means the model and price qualified but the downstream
    kill-only safety layer blocked publication. It must never be presented as a
    price-pending or actionable pick.
    """
    if manager_status != "CONSENSUS":
        return "NO_BET"
    if decision_status == "BET":
        return "HIGH_CONFIDENCE_VALUE"
    if decision_status == "VETOED":
        return "VETOED"
    if decision_status == "NO_BET":
        return "HIGH_CONFIDENCE_PICK"
    return "PRICE_PENDING"


def _as_decimal(value: Decimal | float) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def percent(probability: Decimal | None) -> str:
    """Render a model probability at one decimal, truncated (never rounded up).

    Truncation is deliberate: it keeps the displayed value on the same side of
    the 60% threshold as the raw ``Decimal`` used for classification. A value
    below the threshold can never round up to ``60.0%``, and a qualifying value
    can never round down below it. The raw probability is never altered.
    """
    if probability is None:
        return "—"
    hundredths = (_as_decimal(probability) * 100).quantize(
        Decimal("0.1"), rounding=ROUND_DOWN
    )
    return f"{hundredths:.1f}%"


def whole_percent(probability: Decimal | None) -> str:
    """Render a model probability as a whole, truncated percent for display.

    Presentation-only. The stored/API probability is never changed. Truncation
    keeps the displayed value on the same side of the 60% threshold as the raw
    Decimal (a qualifying 0.6000 renders ``60%``; 0.5999 renders ``59%``).
    """
    if probability is None:
        return "—"
    whole = (_as_decimal(probability) * 100).quantize(
        Decimal("1"), rounding=ROUND_DOWN
    )
    return f"{whole:.0f}%"


def precise_percent(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def side_name(selection_side: int | None, player_1: str, player_2: str) -> str | None:
    if selection_side == 1:
        return player_1
    if selection_side == 2:
        return player_2
    return None


def system_status(latest_activity) -> tuple[str, str]:
    if latest_activity is None:
        return ("no-data", "No data")
    age = (datetime.now(timezone.utc) - latest_activity).total_seconds()
    if age < 24 * 3600:
        return ("live", "Live data")
    return ("stale", "Stale")


def start_time(
    scheduled_start, tz: DashboardTimezone | None = None
) -> str:
    """Display a scheduled start in the selected zone (UTC by default)."""
    return format_dashboard_time(scheduled_start, tz or DEFAULT_TIMEZONE)
