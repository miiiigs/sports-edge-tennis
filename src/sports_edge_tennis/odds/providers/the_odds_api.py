from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sports_edge_tennis.odds.contracts import (
    ProviderBookmakerMarket,
    ProviderObservation,
    ProviderSelection,
)

THE_ODDS_API_PROVIDER_CODE = "THE_ODDS_API"
THE_ODDS_API_TENNIS_ADAPTER_VERSION = "THE_ODDS_API_TENNIS_MATCH_WINNER_V1"
THE_ODDS_API_MATCH_WINNER_MARKET = "h2h"


def new_the_odds_api_response_id(*, received_at: datetime | None = None) -> str:
    observed = received_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    return f"THE_ODDS_API_TENNIS_V1:{observed.astimezone(timezone.utc).isoformat()}:{uuid.uuid4().hex}"


def build_the_odds_api_tennis_observation(
    *,
    event_payload: Mapping[str, Any],
    player_1_name: str,
    player_2_name: str,
    player_1_source_id: str,
    player_2_source_id: str,
    received_at: datetime,
    response_id: str,
) -> ProviderObservation:
    """Normalize one The Odds API tennis event payload to match-winner markets.

    Participant matching fails closed: both provider names must resolve exactly
    after lightweight normalization to the expected match players. The emitted
    selections preserve provider event, bookmaker, market and raw payload
    lineage while using provider player ids for downstream identity resolution.
    """
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    provider_event_id = str(event_payload.get("id") or "").strip()
    if not provider_event_id:
        raise ValueError("The Odds API event id must be present")
    if not _same_name(event_payload.get("home_team"), player_1_name) or not _same_name(
        event_payload.get("away_team"), player_2_name
    ):
        raise ValueError("THE_ODDS_API_TENNIS_PARTICIPANT_MATCH_UNRESOLVED")

    markets: list[ProviderBookmakerMarket] = []
    provider_observed_at = _parse_datetime(event_payload.get("last_update"))
    for bookmaker in event_payload.get("bookmakers") or []:
        bookmaker_key = str(bookmaker.get("key") or "").strip()
        if not bookmaker_key:
            continue
        bookmaker_observed_at = _parse_datetime(bookmaker.get("last_update"))
        if bookmaker_observed_at is not None:
            provider_observed_at = max(
                provider_observed_at or bookmaker_observed_at, bookmaker_observed_at
            )
        for market in bookmaker.get("markets") or []:
            if str(market.get("key") or "").strip() != THE_ODDS_API_MATCH_WINNER_MARKET:
                continue
            market_observed_at = _parse_datetime(market.get("last_update"))
            if market_observed_at is not None:
                provider_observed_at = max(
                    provider_observed_at or market_observed_at, market_observed_at
                )
            selections = _match_winner_selections(
                market.get("outcomes") or (),
                player_1_name=player_1_name,
                player_2_name=player_2_name,
                player_1_source_id=player_1_source_id,
                player_2_source_id=player_2_source_id,
            )
            if len(selections) != 2:
                continue
            markets.append(
                ProviderBookmakerMarket(
                    provider_bookmaker_key=bookmaker_key,
                    provider_market_key=f"{provider_event_id}:{bookmaker_key}:h2h",
                    market_type="MATCH_WINNER",
                    market_status="OPEN",
                    selections=tuple(selections),
                )
            )

    return ProviderObservation(
        provider_code=THE_ODDS_API_PROVIDER_CODE,
        provider_event_id=provider_event_id,
        provider_observation_key=f"{response_id}:EVENT:{provider_event_id}",
        received_at=received_at.astimezone(timezone.utc),
        payload=dict(event_payload),
        adapter_version=THE_ODDS_API_TENNIS_ADAPTER_VERSION,
        markets=tuple(markets),
        provider_observed_at=provider_observed_at,
        provider_response_id=response_id,
    )


def _match_winner_selections(
    outcomes: Any,
    *,
    player_1_name: str,
    player_2_name: str,
    player_1_source_id: str,
    player_2_source_id: str,
) -> list[ProviderSelection]:
    selections: list[ProviderSelection] = []
    seen: set[str] = set()
    for outcome in outcomes:
        name = outcome.get("name") if isinstance(outcome, Mapping) else None
        price = _decimal_price(outcome.get("price") if isinstance(outcome, Mapping) else None)
        if price is None:
            continue
        if _same_name(name, player_1_name):
            key = "PLAYER_1"
            provider_player_id = player_1_source_id
        elif _same_name(name, player_2_name):
            key = "PLAYER_2"
            provider_player_id = player_2_source_id
        else:
            continue
        if key in seen:
            return []
        seen.add(key)
        selections.append(
            ProviderSelection(
                provider_player_id=provider_player_id,
                decimal_price=price,
                provider_selection_key=str(name),
            )
        )
    return selections


def _same_name(left: Any, right: str) -> bool:
    return _name_key(str(left or "")) == _name_key(right)


def _name_key(value: str) -> str:
    return " ".join(value.strip().casefold().replace(".", "").split())


def _decimal_price(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not parsed.is_finite() or parsed <= Decimal("1"):
        return None
    return parsed


def _parse_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
