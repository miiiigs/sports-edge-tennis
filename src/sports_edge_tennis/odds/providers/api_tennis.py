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

API_TENNIS_PROVIDER_CODE = "API_TENNIS"
API_TENNIS_ODDS_ADAPTER_VERSION = "API_TENNIS_OFFERED_ODDS_V1"
API_TENNIS_MATCH_WINNER_MARKET = "Home/Away"


def new_api_tennis_odds_response_id(*, received_at: datetime | None = None) -> str:
    observed = received_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    return f"API_TENNIS_ODDS_V1:{observed.astimezone(timezone.utc).isoformat()}:{uuid.uuid4().hex}"


def build_api_tennis_offered_observation(
    *,
    provider_event_id: str,
    event_payload: Mapping[str, Any],
    first_player_source_id: str,
    second_player_source_id: str,
    received_at: datetime,
    response_id: str,
) -> ProviderObservation:
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    if not provider_event_id.strip():
        raise ValueError("provider_event_id must be non-empty")
    if not first_player_source_id.strip() or not second_player_source_id.strip():
        raise ValueError("API-Tennis player source ids must be non-empty")

    home_away = event_payload.get(API_TENNIS_MATCH_WINNER_MARKET)
    markets: list[ProviderBookmakerMarket] = []
    if isinstance(home_away, Mapping):
        home = home_away.get("Home")
        away = home_away.get("Away")
        if isinstance(home, Mapping) and isinstance(away, Mapping):
            bookmaker_keys = sorted(set(home).intersection(away), key=lambda value: str(value).casefold())
            for raw_key in bookmaker_keys:
                bookmaker_key = str(raw_key).strip()
                if not bookmaker_key:
                    continue
                home_price = _decimal_price(home.get(raw_key))
                away_price = _decimal_price(away.get(raw_key))
                if home_price is None or away_price is None:
                    continue
                markets.append(
                    ProviderBookmakerMarket(
                        provider_bookmaker_key=bookmaker_key,
                        provider_market_key=f"MATCH_WINNER:{provider_event_id}:{bookmaker_key}",
                        market_type="MATCH_WINNER",
                        market_status="OPEN",
                        selections=(
                            ProviderSelection(
                                provider_player_id=first_player_source_id,
                                decimal_price=home_price,
                                provider_selection_key="Home",
                            ),
                            ProviderSelection(
                                provider_player_id=second_player_source_id,
                                decimal_price=away_price,
                                provider_selection_key="Away",
                            ),
                        ),
                    )
                )

    payload = dict(event_payload)
    return ProviderObservation(
        provider_code=API_TENNIS_PROVIDER_CODE,
        provider_event_id=provider_event_id,
        provider_observation_key=f"{response_id}:EVENT:{provider_event_id}",
        received_at=received_at.astimezone(timezone.utc),
        payload=payload,
        adapter_version=API_TENNIS_ODDS_ADAPTER_VERSION,
        markets=tuple(markets),
        provider_observed_at=None,
        provider_response_id=response_id,
    )


def _decimal_price(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not parsed.is_finite() or parsed <= Decimal("1"):
        return None
    return parsed
