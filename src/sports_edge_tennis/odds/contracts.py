from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal


NORMALIZER_VERSION = "live_odds_normalizer_v1"

ObservationTimeBasis = Literal["PROVIDER_TIME", "RECEIVE_TIME"]
NormalizationClass = Literal["SHARP", "OFFERED", "RAW_ONLY"]
MarketStatus = Literal["OPEN", "SUSPENDED", "CLOSED", "UNKNOWN"]
ProviderMarketType = Literal["MATCH_WINNER"]
ResolutionStatus = Literal[
    "NORMALIZED",
    "RAW_ONLY",
    "UNMATCHED_EVENT",
    "IDENTITY_CONFLICT",
    "UNKNOWN_PARTICIPANT",
    "UNKNOWN_BOOKMAKER",
    "RAW_ONLY_BOOKMAKER",
    "INVALID_MARKET",
    "INVALID_PRICE",
    "INVALID_STATUS",
    "NORMALIZATION_ERROR",
]


class LiveOddsError(ValueError):
    """Raised when Live Odds V1 receives an invalid provider-neutral object."""


class LiveOddsIntegrityError(RuntimeError):
    """Raised when persisted lineage would disagree with deterministic normalization."""


@dataclass(frozen=True)
class ProviderSelection:
    provider_player_id: str | None
    decimal_price: Decimal
    canonical_player_id: int | None = None
    provider_selection_key: str | None = None

    def __post_init__(self) -> None:
        has_provider_player_id = self.provider_player_id is not None and bool(self.provider_player_id.strip())
        has_canonical_player_id = self.canonical_player_id is not None
        if has_provider_player_id == has_canonical_player_id:
            raise LiveOddsError("selection must provide exactly one identity path")
        if self.provider_player_id is not None and not self.provider_player_id.strip():
            raise LiveOddsError("provider_player_id must be non-empty")
        if self.canonical_player_id is not None and self.canonical_player_id <= 0:
            raise LiveOddsError("canonical_player_id must be positive")
        if self.decimal_price <= Decimal("1"):
            raise LiveOddsError("decimal_price must be greater than 1")


@dataclass(frozen=True)
class ProviderBookmakerMarket:
    provider_bookmaker_key: str
    provider_market_key: str
    market_type: ProviderMarketType
    market_status: MarketStatus
    selections: tuple[ProviderSelection, ...]

    def __post_init__(self) -> None:
        if not self.provider_bookmaker_key.strip():
            raise LiveOddsError("provider_bookmaker_key must be non-empty")
        if not self.provider_market_key.strip():
            raise LiveOddsError("provider_market_key must be non-empty")
        if not self.selections:
            raise LiveOddsError("market must contain at least one selection")


@dataclass(frozen=True)
class ProviderObservation:
    provider_code: str
    provider_event_id: str
    provider_observation_key: str
    received_at: datetime
    payload: dict[str, Any]
    adapter_version: str
    markets: tuple[ProviderBookmakerMarket, ...]
    provider_observed_at: datetime | None = None
    provider_request_id: str | None = None
    provider_response_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("provider_code", "provider_event_id", "provider_observation_key", "adapter_version"):
            if not getattr(self, field_name).strip():
                raise LiveOddsError(f"{field_name} must be non-empty")
        if self.received_at.tzinfo is None:
            raise LiveOddsError("received_at must be timezone-aware")
        if self.provider_observed_at is not None and self.provider_observed_at.tzinfo is None:
            raise LiveOddsError("provider_observed_at must be timezone-aware when provided")
        if self.provider_observed_at is not None and self.received_at < self.provider_observed_at:
            raise LiveOddsError("received_at cannot be before provider_observed_at")

    @property
    def observation_time_basis(self) -> ObservationTimeBasis:
        return "PROVIDER_TIME" if self.provider_observed_at is not None else "RECEIVE_TIME"

    @property
    def observation_time(self) -> datetime:
        value = self.provider_observed_at if self.provider_observed_at is not None else self.received_at
        return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class RawObservationRecord:
    raw_odds_observation_id: int
    ingest_run_id: int
    inserted: bool
    provider_code: str
    provider_event_id: str
    provider_observation_key: str
    provider_request_id: str | None
    provider_response_id: str | None
    provider_observed_at: datetime | None
    received_at: datetime
    observation_time: datetime
    observation_time_basis: ObservationTimeBasis
    payload_hash: str
    payload_json: dict[str, Any]
    adapter_version: str
    normalizer_version: str


@dataclass(frozen=True)
class BookmakerMapping:
    provider_bookmaker_mapping_id: int
    provider_code: str
    provider_bookmaker_key: str
    mapping_version: int
    bookmaker_code: str
    normalization_class: NormalizationClass
    effective_from: datetime


@dataclass(frozen=True)
class MatchVersionContext:
    match_id: int
    match_version_id: int
    player_1_id: int
    player_2_id: int
    match_start_at: datetime | None
    tour_scope: str
    competition_tier: str
    match_status: str


@dataclass(frozen=True)
class NormalizedOddsQuote:
    source_system: Literal["SHARP", "OFFERED"]
    bookmaker_code: str
    selection_type: Literal["PLAYER_1", "PLAYER_2"]
    decimal_price: Decimal
    market_status: MarketStatus
    source_snapshot_id: str
    source_record_hash: str
    source_observed_at: datetime
    as_of: datetime
    raw_odds_observation_id: int
    provider_bookmaker_mapping_id: int


@dataclass
class LiveOddsReconciliation:
    raw_observations_seen: int = 0
    raw_inserted: int = 0
    raw_existing: int = 0
    normalized_inserted: int = 0
    normalized_existing: int = 0
    raw_only: int = 0
    unmatched_event: int = 0
    identity_conflict: int = 0
    unknown_participant: int = 0
    unknown_bookmaker: int = 0
    raw_only_bookmaker: int = 0
    invalid_market: int = 0
    invalid_price: int = 0
    invalid_status: int = 0
    failed_groups: int = 0
    details: list[dict[str, object]] = field(default_factory=list)

    @property
    def terminal_status(self) -> str:
        if self.failed_groups and self.normalized_inserted == 0 and self.normalized_existing == 0:
            return "FAILED"
        if self.failed_groups or self.raw_only or self.unmatched_event or self.identity_conflict:
            return "PARTIAL"
        return "COMPLETED"
