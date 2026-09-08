"""Presentation-layer translation of persisted backend truth to client wording.

Pure functions: no database access, no model recalculation. These map internal
codes to the language shown to a client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

# Internal client-state → human label (primary view).
STATE_LABELS = {
    "MODEL_CONSENSUS": "Consensus",
    "NO_BET_MODEL": "No Bet — Model Filter",
    "SHARP_AVAILABLE": "Pinnacle Available",
    "SHARP_UNAVAILABLE": "Pinnacle Unavailable",
    "RECONCILIATION_FAILED": "Price Match Unavailable",
    "AWAITING_OFFERED_PRICE": "Awaiting Bookmaker Price",
    "AWAITING_MARKET": "Awaiting Market Decision",
    "BET": "Bet",
    "NO_BET_MARKET": "No Bet — Market Filter",
    "PREDICTION_PENDING": "Prediction Pending",
}

# Manager reason code → short human explanation. Presentation only.
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
    "NO_MODEL_EDGE_VS_SHARP": "The model did not beat the sharp fair probability.",
    "MISSING_MARKET": "No fresh offered bookmaker price was available.",
    "EDGE_BELOW_THRESHOLD": "The offered price did not meet the required value threshold.",
    "NONE": None,
}

# Canonical competition-tier display names.
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
    """Derive the client-facing state from persisted Manager/Market truth only."""
    if decision_status == "BET":
        return "BET"
    if manager_status != "CONSENSUS":
        return "NO_BET_MODEL"
    if decision_status == "NO_BET":
        return "NO_BET_MARKET"
    if sharp_available:
        return "AWAITING_OFFERED_PRICE"
    return "SHARP_UNAVAILABLE"


def percent(probability: Decimal | None) -> str:
    if probability is None:
        return "—"
    return f"{float(probability) * 100:.0f}%"


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
    """Return (css-class, label) for the header status indicator."""
    if latest_activity is None:
        return ("no-data", "No data")
    age = (datetime.now(timezone.utc) - latest_activity).total_seconds()
    if age < 24 * 3600:
        return ("live", "Live data")
    return ("stale", "Stale")


def agreement_text(active: int, abstaining: int) -> str:
    if abstaining == 0 and active == 3:
        return "3/3 AGREED"
    if abstaining == 1 and active == 2:
        return "2/3 AGREED · 1 ABSTAINED"
    return f"{active}/3 ACTIVE"


def start_time(scheduled_start) -> str:
    if scheduled_start is None:
        return "—"
    return scheduled_start.strftime("%H:%M UTC")
