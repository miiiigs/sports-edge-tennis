from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sports_edge_tennis.live.oddspapi import ODDSPAPI_PLAN_BOOKMAKERS
from sports_edge_tennis.odds.contracts import ProviderBookmakerMarket, ProviderObservation, ProviderSelection


ODDSPAPI_PROVIDER_CODE = "ODDSPAPI"
ODDSPAPI_ADAPTER_VERSION = "ODDSPAPI_TENNIS_MATCH_WINNER_V1"
ODDSPAPI_MATCH_WINNER_MARKET_ID = "121"
ODDSPAPI_PLAYER_1_OUTCOME_ID = "121"
ODDSPAPI_PLAYER_2_OUTCOME_ID = "122"


def new_oddspapi_response_id(*, received_at: datetime | None = None) -> str:
    observed = received_at or datetime.now(UTC)
    if observed.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    return f"ODDSPAPI_TENNIS_V1:{observed.astimezone(UTC).isoformat()}:{uuid.uuid4().hex}"


def build_oddspapi_tennis_observation(
    *,
    event_payload: Mapping[str, Any],
    received_at: datetime,
    response_id: str,
    allowed_bookmakers: Sequence[str] = ODDSPAPI_PLAN_BOOKMAKERS,
) -> ProviderObservation:
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    fixture_id = str(event_payload.get("fixtureId") or "").strip()
    p1_id = str(event_payload.get("participant1Id") or "").strip()
    p2_id = str(event_payload.get("participant2Id") or "").strip()
    if not fixture_id or not p1_id or not p2_id or p1_id == p2_id:
        raise ValueError("invalid OddsPapi fixture identity")

    allowed = {str(value).strip() for value in allowed_bookmakers if str(value).strip()}
    markets: list[ProviderBookmakerMarket] = []
    observed_times: list[datetime] = []
    books = event_payload.get("bookmakerOdds")
    if isinstance(books, Mapping):
        for slug, raw_book in books.items():
            slug = str(slug).strip()
            if slug not in allowed or not isinstance(raw_book, Mapping):
                continue
            raw_markets = raw_book.get("markets")
            if not isinstance(raw_markets, Mapping):
                continue
            raw_market = raw_markets.get(ODDSPAPI_MATCH_WINNER_MARKET_ID)
            if not isinstance(raw_market, Mapping):
                continue
            selections, timestamps = _winner_selections(raw_market, p1_id=p1_id, p2_id=p2_id)
            if len(selections) != 2:
                continue
            observed_times.extend(timestamps)
            suspended = bool(raw_book.get("suspended")) or not bool(raw_market.get("marketActive", True))
            market_status = "SUSPENDED" if suspended else "OPEN"
            markets.append(
                ProviderBookmakerMarket(
                    provider_bookmaker_key=slug,
                    provider_market_key=f"{fixture_id}:{slug}:{ODDSPAPI_MATCH_WINNER_MARKET_ID}",
                    market_type="MATCH_WINNER",
                    market_status=market_status,
                    selections=tuple(selections),
                )
            )

    provider_observed_at = max(observed_times) if observed_times else _parse_timestamp(event_payload.get("updatedAt"))
    if provider_observed_at is not None and provider_observed_at > received_at.astimezone(UTC):
        provider_observed_at = None
    return ProviderObservation(
        provider_code=ODDSPAPI_PROVIDER_CODE,
        provider_event_id=fixture_id,
        provider_observation_key=f"{response_id}:EVENT:{fixture_id}",
        received_at=received_at.astimezone(UTC),
        payload=dict(event_payload),
        adapter_version=ODDSPAPI_ADAPTER_VERSION,
        markets=tuple(markets),
        provider_observed_at=provider_observed_at,
        provider_response_id=response_id,
    )


def extract_oddspapi_match_winner_prices(event_payload: Mapping[str, Any], *, bookmaker: str) -> tuple[Decimal, Decimal] | None:
    books = event_payload.get("bookmakerOdds")
    if not isinstance(books, Mapping):
        return None
    raw_book = books.get(bookmaker)
    if not isinstance(raw_book, Mapping) or bool(raw_book.get("suspended")):
        return None
    raw_markets = raw_book.get("markets")
    if not isinstance(raw_markets, Mapping):
        return None
    raw_market = raw_markets.get(ODDSPAPI_MATCH_WINNER_MARKET_ID)
    if not isinstance(raw_market, Mapping) or not bool(raw_market.get("marketActive", True)):
        return None
    prices = []
    for outcome_id in (ODDSPAPI_PLAYER_1_OUTCOME_ID, ODDSPAPI_PLAYER_2_OUTCOME_ID):
        price_obj = _player_zero(raw_market, outcome_id)
        if price_obj is None or not bool(price_obj.get("active")):
            return None
        price = _decimal_price(price_obj.get("price"))
        if price is None:
            return None
        prices.append(price)
    return prices[0], prices[1]


def _winner_selections(raw_market: Mapping[str, Any], *, p1_id: str, p2_id: str):
    selections: list[ProviderSelection] = []
    timestamps: list[datetime] = []
    for outcome_id, player_id in ((ODDSPAPI_PLAYER_1_OUTCOME_ID, p1_id), (ODDSPAPI_PLAYER_2_OUTCOME_ID, p2_id)):
        price_obj = _player_zero(raw_market, outcome_id)
        if price_obj is None or not bool(price_obj.get("active")):
            return [], []
        price = _decimal_price(price_obj.get("price"))
        if price is None:
            return [], []
        changed_at = _parse_timestamp(price_obj.get("changedAt")) or _parse_timestamp(price_obj.get("bookmakerChangedAt"))
        if changed_at is not None:
            timestamps.append(changed_at)
        selections.append(
            ProviderSelection(
                provider_player_id=player_id,
                decimal_price=price,
                provider_selection_key=outcome_id,
            )
        )
    return selections, timestamps


def _player_zero(raw_market: Mapping[str, Any], outcome_id: str) -> Mapping[str, Any] | None:
    outcomes = raw_market.get("outcomes")
    if not isinstance(outcomes, Mapping):
        return None
    outcome = outcomes.get(outcome_id)
    if not isinstance(outcome, Mapping):
        return None
    players = outcome.get("players")
    if not isinstance(players, Mapping):
        return None
    value = players.get("0")
    return value if isinstance(value, Mapping) else None


def _decimal_price(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not price.is_finite() or price <= Decimal("1"):
        return None
    return price


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


__all__ = [
    "ODDSPAPI_ADAPTER_VERSION",
    "ODDSPAPI_MATCH_WINNER_MARKET_ID",
    "ODDSPAPI_PLAYER_1_OUTCOME_ID",
    "ODDSPAPI_PLAYER_2_OUTCOME_ID",
    "ODDSPAPI_PROVIDER_CODE",
    "build_oddspapi_tennis_observation",
    "extract_oddspapi_match_winner_prices",
    "new_oddspapi_response_id",
]
