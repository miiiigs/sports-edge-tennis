from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

MARKET_POLICY_KEY = "tennis_market_value"
MARKET_POLICY_VERSION = "v1"

MARKET_PRECISION = 60
MARKET_MODEL_VERSION = "market_value_v1"
MARKET_FEATURE_VERSION = "type_a"

_PRICE_QUANTIZER = Decimal("0.000001")
_PROBABILITY_QUANTIZER = Decimal("0.000001")

MarketDecisionStatus = Literal["BET", "NO_BET"]

MarketReasonCode = Literal[
    "NONE",
    "NOT_MANAGER_ELIGIBLE",
    "MISSING_MARKET",
    "INCOMPLETE_MARKET",
    "STALE_MARKET",
    "MARKET_SUSPENDED",
    "CROSSED_MARKET",
    "INVALID_PRICE",
    "NO_MODEL_EDGE_VS_SHARP",
    "EDGE_BELOW_THRESHOLD",
    "UNSUPPORTED_TIER",
    "MISSING_SHARP_REFERENCE",
]

SnapshotRole = Literal[
    "REFERENCE_PLAYER_1",
    "REFERENCE_PLAYER_2",
    "OFFERED_SELECTION",
]

CANONICAL_ROLE_ORDER: tuple[SnapshotRole, ...] = (
    "REFERENCE_PLAYER_1",
    "REFERENCE_PLAYER_2",
    "OFFERED_SELECTION",
)

SUPPORTED_ATP_TIERS = frozenset(
    {"ATP_250", "ATP_500", "MASTERS_1000", "GRAND_SLAM"}
)

SUPPORTED_TIERS = SUPPORTED_ATP_TIERS | {"CHALLENGER"}


class MarketValueError(ValueError):
    """Base error for the Market/Value engine."""


class MarketPolicyError(MarketValueError):
    """Raised when a Market policy invariant is violated."""


class MarketExecutionError(MarketValueError):
    """Raised when a Market evaluation execution/invariant fails (not a NO_BET)."""


class MarketIdempotencyMismatchError(MarketValueError):
    """Raised when persisted Market truth differs from the computed canonical slate."""


class MarketSlateError(MarketValueError):
    """Raised for inconsistent whole-slate Market reconciliation."""


def quantize_price(value: Decimal) -> Decimal:
    return value.quantize(_PRICE_QUANTIZER)


def quantize_probability(value: Decimal) -> Decimal:
    return value.quantize(_PROBABILITY_QUANTIZER)


@dataclass(frozen=True)
class MarketPolicyV1:
    market_policy_version_id: int
    policy_key: str
    policy_version: str
    atp_main_min_edge_pct: Decimal
    challenger_min_edge_pct: Decimal
    long_price_threshold: Decimal
    long_price_min_edge_pct: Decimal
    default_devig_method: str
    sharp_reference_policy_code: str
    odds_max_staleness_minutes: int
    type_b_enabled: bool


@dataclass(frozen=True)
class SharpReferenceSpec:
    source_system: str
    bookmaker_code: str


@dataclass(frozen=True)
class PersistedManagerConsensusRow:
    manager_consensus_result_id: int
    prediction_run_id: int
    match_id: int
    manager_policy_version_id: int
    manager_status: str
    selected_side: int | None
    consensus_probability: Decimal | None
    active_agent_count: int
    abstaining_agent_count: int
    probability_spread: Decimal | None
    reason_code: str
    as_of: datetime


@dataclass(frozen=True)
class OddsSnapshotV1:
    odds_snapshot_id: int
    match_id: int
    source_system: str
    bookmaker_code: str
    market_type: str
    selection_type: str
    decimal_price: Decimal
    market_status: str
    booksum: Decimal | None
    source_snapshot_id: str | None
    source_observed_at: datetime
    as_of: datetime


@dataclass(frozen=True)
class MarketDecisionV1:
    manager_consensus_result_id: int
    prediction_run_id: int
    match_id: int
    market_policy_version_id: int
    decision_as_of: datetime
    decision_status: MarketDecisionStatus
    reason_code: MarketReasonCode
    selected_side: int | None
    consensus_probability: Decimal | None
    edge_pct: Decimal | None
    min_acceptable_odds: Decimal | None
    market_decimal_price: Decimal | None
    sharp_reference_price: Decimal | None
    price_source_system: str | None
    price_bookmaker_code: str | None
    devig_method: str | None
    devig_fair_odds: Decimal | None
    active_agent_count: int
    abstaining_agent_count: int
    probability_spread: Decimal | None
    lineage: tuple[tuple[SnapshotRole, int], ...]
    snapshot_lineage_hash: str


@dataclass(frozen=True)
class MarketSlateOutcome:
    prediction_run_id: int
    manager_result_count: int
    new_decision_count: int
    existing_decision_count: int
    qualified_bet_count: int
    status: Literal["PERSISTED", "IDEMPOTENT_SUCCESS", "EMPTY_SLATE"]
