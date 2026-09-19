from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sports_edge_tennis.odds.contracts import (
    ProviderBookmakerMarket,
    ProviderObservation,
    ProviderSelection,
)

GOALSERVE_TENNIS_PROVIDER_CODE = "GOALSERVE_TENNIS"
GOALSERVE_TENNIS_ADAPTER_VERSION = "GOALSERVE_TENNIS_MATCH_WINNER_V1"
GOALSERVE_HOME_AWAY_TYPE_ID = "1"
GOALSERVE_HOME_AWAY_TYPE_NAME = "Home/Away"


def new_goalserve_tennis_response_id(*, received_at: datetime | None = None) -> str:
    observed = received_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    return (
        "GOALSERVE_TENNIS_V1:"
        f"{observed.astimezone(timezone.utc).isoformat()}:{uuid.uuid4().hex}"
    )


def build_goalserve_tennis_observation(
    *,
    match_payload: Mapping[str, Any],
    player_1_name: str,
    player_2_name: str,
    player_1_source_id: str,
    player_2_source_id: str,
    received_at: datetime,
    response_id: str,
) -> ProviderObservation:
    """Normalize one Goalserve Tennis match to provider-neutral match-winner markets.

    Goalserve publishes Tennis odds under a Home/Away market (type id 1) with
    selection names 1 and 2. Only complete two-way match-winner books are
    normalized. All other markets remain in the raw payload and therefore
    cannot leak into the current MATCH_WINNER decision engine.

    The adapter accepts the common JSON representation produced from Goalserve's
    XML/JSON Tennis feed. Singleton/list container differences are normalized
    explicitly and all participant/order mismatches fail closed.
    """
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")

    provider_event_id = str(match_payload.get("id") or "").strip()
    if not provider_event_id:
        raise ValueError("Goalserve match id must be present")
    if not player_1_source_id.strip() or not player_2_source_id.strip():
        raise ValueError("Goalserve player source ids must be non-empty")

    players = _as_list(match_payload.get("player"))
    if len(players) != 2:
        raise ValueError("GOALSERVE_TENNIS_PARTICIPANTS_UNRESOLVED")
    if not _same_name(_mapping_value(players[0], "name"), player_1_name):
        raise ValueError("GOALSERVE_TENNIS_PARTICIPANT_ORDER_UNRESOLVED")
    if not _same_name(_mapping_value(players[1], "name"), player_2_name):
        raise ValueError("GOALSERVE_TENNIS_PARTICIPANT_ORDER_UNRESOLVED")

    odds = match_payload.get("odds")
    markets: list[ProviderBookmakerMarket] = []
    provider_observed_at = None
    if isinstance(odds, Mapping):
        provider_observed_at = _parse_epoch_millis(odds.get("ts"))
        for market in _as_list(odds.get("type")):
            if not isinstance(market, Mapping) or not _is_home_away_market(market):
                continue
            for bookmaker in _as_list(market.get("bookmaker")):
                if not isinstance(bookmaker, Mapping):
                    continue
                bookmaker_key = _bookmaker_key(bookmaker)
                if not bookmaker_key:
                    continue
                selections = _home_away_selections(
                    bookmaker.get("odd"),
                    player_1_source_id=player_1_source_id,
                    player_2_source_id=player_2_source_id,
                )
                if len(selections) != 2:
                    continue
                markets.append(
                    ProviderBookmakerMarket(
                        provider_bookmaker_key=bookmaker_key,
                        provider_market_key=(
                            f"{provider_event_id}:{bookmaker_key}:HOME_AWAY"
                        ),
                        market_type="MATCH_WINNER",
                        market_status="OPEN",
                        selections=tuple(selections),
                    )
                )

    return ProviderObservation(
        provider_code=GOALSERVE_TENNIS_PROVIDER_CODE,
        provider_event_id=provider_event_id,
        provider_observation_key=f"{response_id}:EVENT:{provider_event_id}",
        received_at=received_at.astimezone(timezone.utc),
        payload=dict(match_payload),
        adapter_version=GOALSERVE_TENNIS_ADAPTER_VERSION,
        markets=tuple(markets),
        provider_observed_at=provider_observed_at,
        provider_response_id=response_id,
    )


def _is_home_away_market(market: Mapping[str, Any]) -> bool:
    market_id = str(market.get("id") or "").strip()
    market_name = str(market.get("value") or "").strip().casefold()
    return (
        market_id == GOALSERVE_HOME_AWAY_TYPE_ID
        or market_name == GOALSERVE_HOME_AWAY_TYPE_NAME.casefold()
    )


def _bookmaker_key(bookmaker: Mapping[str, Any]) -> str:
    bookmaker_id = str(bookmaker.get("id") or "").strip()
    bookmaker_name = str(bookmaker.get("name") or "").strip()
    if bookmaker_id and bookmaker_name:
        return f"{bookmaker_id}:{bookmaker_name}"
    return bookmaker_id or bookmaker_name


def _home_away_selections(
    raw_odds: Any,
    *,
    player_1_source_id: str,
    player_2_source_id: str,
) -> list[ProviderSelection]:
    selections: list[ProviderSelection] = []
    seen: set[str] = set()
    for raw in _as_list(raw_odds):
        if not isinstance(raw, Mapping):
            continue
        selection_key = str(raw.get("name") or "").strip()
        if selection_key not in {"1", "2"} or selection_key in seen:
            return []
        price = _decimal_price(raw.get("value"))
        if price is None:
            continue
        seen.add(selection_key)
        selections.append(
            ProviderSelection(
                provider_player_id=(
                    player_1_source_id if selection_key == "1" else player_2_source_id
                ),
                decimal_price=price,
                provider_selection_key=selection_key,
            )
        )
    if seen != {"1", "2"}:
        return []
    selections.sort(key=lambda selection: selection.provider_selection_key or "")
    return selections


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _mapping_value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else None


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


def _parse_epoch_millis(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        raw = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not raw.is_finite() or raw <= 0:
        return None
    seconds = raw / Decimal("1000")
    try:
        return datetime.fromtimestamp(float(seconds), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
