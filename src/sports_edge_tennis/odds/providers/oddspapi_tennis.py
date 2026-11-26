from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sports_edge_tennis.live.oddspapi import ODDSPAPI_PLAN_BOOKMAKERS
from sports_edge_tennis.odds.contracts import ProviderBookmakerMarket, ProviderObservation, ProviderSelection


ODDSPAPI_PROVIDER_CODE = "ODDSPAPI"
ODDSPAPI_ADAPTER_VERSION = "ODDSPAPI_TENNIS_MATCH_WINNER_V2"
# Validated against 11,087 stored tennis payloads (sportId=12): every payload
# carries market "121" with outcomes "121"/"122". The published tutorial's
# "171"/"172" is Counter-Strike moneyline in the current catalogue and appears
# in zero tennis payloads. ``resolve_winner_market`` therefore accepts an
# explicit catalogue rather than hardcoding either tutorial value.
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
    books = event_payload.get("bookmakerOdds")
    if isinstance(books, Mapping):
        for slug, raw_book in books.items():
            slug = str(slug).strip()
            if slug not in allowed or not isinstance(raw_book, Mapping):
                continue
            raw_markets = raw_book.get("markets")
            if not isinstance(raw_markets, Mapping):
                continue
            resolved = resolve_winner_market(raw_markets)
            if resolved is None:
                continue
            market_id, outcome_ids, raw_market = resolved
            selections = _winner_selections(
                raw_market, p1_id=p1_id, p2_id=p2_id, outcome_ids=outcome_ids)
            if len(selections) != 2:
                continue
            suspended = bool(raw_book.get("suspended")) or not bool(raw_market.get("marketActive", True))
            market_status = "SUSPENDED" if suspended else "OPEN"
            markets.append(
                ProviderBookmakerMarket(
                    provider_bookmaker_key=slug,
                    provider_market_key=f"{fixture_id}:{slug}:{market_id}",
                    market_type="MATCH_WINNER",
                    market_status=market_status,
                    selections=tuple(selections),
                )
            )

    # OddsPapi is polled once per decision cycle. Its per-outcome ``changedAt``
    # and the fixture ``updatedAt`` mark when a price last changed, not when the
    # market was observed. Treating a last-change time as the observation time
    # makes a stable, still-offered price look stale, and the embedded timestamp
    # also freezes snapshot deduplication on the first observation of a price.
    # The response is therefore a RECEIVE_TIME observation, consistent with the
    # other polled provider adapters. ``changedAt`` remains in the raw payload
    # for audit only.
    provider_observed_at = None
    raw_payload = dict(event_payload)
    if isinstance(books, Mapping):
        raw_payload["bookmakerOdds"] = {
            str(slug): dict(raw_book)
            for slug, raw_book in books.items()
            if str(slug).strip() in allowed and isinstance(raw_book, Mapping)
        }

    return ProviderObservation(
        provider_code=ODDSPAPI_PROVIDER_CODE,
        provider_event_id=fixture_id,
        provider_observation_key=f"{response_id}:EVENT:{fixture_id}",
        received_at=received_at.astimezone(UTC),
        payload=raw_payload,
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
    resolved = resolve_winner_market(raw_markets)
    if resolved is None:
        return None
    _market_id, outcome_ids, raw_market = resolved
    if not bool(raw_market.get("marketActive", True)):
        return None
    prices = []
    for outcome_id in outcome_ids:
        price_obj = _player_zero(raw_market, outcome_id)
        if price_obj is None or not bool(price_obj.get("active")):
            return None
        price = _decimal_price(price_obj.get("price"))
        if price is None:
            return None
        prices.append(price)
    return prices[0], prices[1]


def resolve_winner_market(
    raw_markets: Mapping[str, Any],
    *,
    catalogue_market_id: str | None = None,
    catalogue_outcome_ids: tuple[str, str] | None = None,
    allow_structural_fallback: bool = False,
) -> tuple[str, tuple[str, str], Mapping[str, Any]] | None:
    """Resolve the tennis match-winner market without tutorial hardcoding.

    A caller may pass a catalogue entry validated from ``GET /v4/markets``.
    Without one, only the empirically observed tennis key ``121`` is accepted.
    Generic two-outcome guessing is off by default so a Counter-Strike or
    another sport's market cannot be silently normalized as tennis.
    """
    normalized = {str(key): value for key, value in raw_markets.items()
                  if isinstance(value, Mapping)}
    candidates: list[tuple[str, tuple[str, str], Mapping[str, Any]]] = []
    if catalogue_market_id is not None:
        market_id = str(catalogue_market_id)
        market = normalized.get(market_id)
        if market is None:
            return None
        outcomes = market.get("outcomes")
        if not isinstance(outcomes, Mapping):
            return None
        keys = tuple(str(key) for key in outcomes)
        if catalogue_outcome_ids is not None:
            pair = tuple(str(value) for value in catalogue_outcome_ids)
            if len(pair) != 2 or any(key not in keys for key in pair):
                return None
            return market_id, pair, market
        if len(keys) != 2:
            return None
        return market_id, keys, market
    preferred = normalized.get(ODDSPAPI_MATCH_WINNER_MARKET_ID)
    if preferred is not None:
        outcomes = preferred.get("outcomes")
        if isinstance(outcomes, Mapping) and len(outcomes) == 2:
            return (ODDSPAPI_MATCH_WINNER_MARKET_ID,
                    tuple(str(key) for key in outcomes), preferred)
    if not allow_structural_fallback:
        return None
    for market_id, market in normalized.items():
        outcomes = market.get("outcomes")
        if not isinstance(outcomes, Mapping) or len(outcomes) != 2:
            continue
        keys = tuple(str(key) for key in outcomes)
        if all(isinstance((outcomes.get(key) or {}).get("players"), Mapping)
               for key in keys):
            candidates.append((market_id, keys, market))
    return candidates[0] if len(candidates) == 1 else None


def _winner_selections(raw_market: Mapping[str, Any], *, p1_id: str, p2_id: str,
                       outcome_ids: tuple[str, str] = (
                           ODDSPAPI_PLAYER_1_OUTCOME_ID,
                           ODDSPAPI_PLAYER_2_OUTCOME_ID),
                       require_active: bool = True) -> list[ProviderSelection]:
    selections: list[ProviderSelection] = []
    for outcome_id, player_id in zip(outcome_ids, (p1_id, p2_id)):
        price_obj = _player_zero(raw_market, outcome_id)
        if price_obj is None:
            return []
        if require_active and not bool(price_obj.get("active")):
            return []
        price = _decimal_price(price_obj.get("price"))
        if price is None:
            return []
        selections.append(
            ProviderSelection(
                provider_player_id=player_id,
                decimal_price=price,
                provider_selection_key=str(outcome_id),
            )
        )
    return selections


def _player_zero(raw_market: Mapping[str, Any], outcome_id: str) -> Mapping[str, Any] | None:
    """Return the live ``players["0"]`` dict, accepting numeric JSON keys.

    A list is the historical shape and must never be silently interpreted as a
    current price; historical reconstruction has its own selector below.
    """
    outcomes = raw_market.get("outcomes")
    if not isinstance(outcomes, Mapping):
        return None
    normalized = {str(key): value for key, value in outcomes.items()}
    outcome = normalized.get(str(outcome_id))
    if not isinstance(outcome, Mapping):
        return None
    players = outcome.get("players")
    if not isinstance(players, Mapping):
        return None
    players = {str(key): value for key, value in players.items()}
    value = players.get("0")
    return value if isinstance(value, Mapping) else None


def select_historical_state(
    entries: Any,
    cutoff: datetime,
    *,
    required: bool = True,
) -> dict[str, Any] | None:
    """Select the latest historical entry at or before ``cutoff``.

    The latest entry decides the state. An inactive latest entry is a
    suspension; it never falls back to an earlier active price. Entries without
    a parseable ``createdAt`` are rejected rather than assumed fresh.
    """
    if cutoff.tzinfo is None:
        raise ValueError("historical cutoff must be timezone-aware")
    if not isinstance(entries, list) or not entries:
        if required:
            return None
        return {"state": "MISSING", "entry": None, "created_at": None,
                "price": None, "active": None}
    parsed = []
    for position, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            return None
        created_at = _parse_timestamp(entry.get("createdAt"))
        price = _decimal_price(entry.get("price"))
        if created_at is None or price is None:
            return None
        if created_at <= cutoff:
            parsed.append((created_at, position, entry, price))
    if not parsed:
        if required:
            return None
        return {"state": "MISSING", "entry": None, "created_at": None,
                "price": None, "active": None}
    created_at, position, entry, price = max(parsed, key=lambda item: (item[0], item[1]))
    active = bool(entry.get("active"))
    return {
        "state": "ACTIVE" if active else "SUSPENDED",
        "entry": entry, "created_at": created_at, "price": price,
        "active": active, "position": position,
        "bookmaker_changed_at": _parse_timestamp(entry.get("bookmakerChangedAt")),
        "provider_changed_at": _parse_timestamp(entry.get("changedAt")),
    }


def reconstruct_historical_winner(
    payload: Mapping[str, Any],
    *,
    cutoff: datetime,
    bookmakers: Sequence[str] = ODDSPAPI_PLAN_BOOKMAKERS,
    catalogue_market_id: str | None = None,
    catalogue_outcome_ids: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return one correctly oriented, latest-at-cutoff state per book.

    This is a pure reconstruction. It does not persist, does not claim the
    provider time is our acquisition time, and does not promote an earlier
    active price when the latest state at the cutoff is suspended.
    """
    fixture_id = str(payload.get("fixtureId") or "").strip()
    p1_id = str(payload.get("participant1Id") or "").strip()
    p2_id = str(payload.get("participant2Id") or "").strip()
    if not fixture_id or not p1_id or not p2_id or p1_id == p2_id:
        raise ValueError("invalid OddsPapi fixture identity")
    raw_books = payload.get("bookmakers")
    if not isinstance(raw_books, Mapping):
        raise ValueError("historical payload must use the bookmakers key")
    allowed = {str(value).strip() for value in bookmakers if str(value).strip()}
    out = []
    for slug, book in raw_books.items():
        slug = str(slug).strip()
        if slug not in allowed or not isinstance(book, Mapping):
            continue
        raw_markets = book.get("markets")
        if not isinstance(raw_markets, Mapping):
            continue
        resolved = resolve_winner_market(
            raw_markets, catalogue_market_id=catalogue_market_id,
            catalogue_outcome_ids=catalogue_outcome_ids)
        if resolved is None:
            continue
        market_id, outcome_ids, market = resolved
        states = []
        for outcome_id, player_id in zip(outcome_ids, (p1_id, p2_id)):
            outcomes = {str(key): value for key, value in (market.get("outcomes") or {}).items()}
            raw_outcome = outcomes.get(str(outcome_id))
            if not isinstance(raw_outcome, Mapping):
                states = []
                break
            players = raw_outcome.get("players")
            entries = (
                {str(key): value for key, value in players.items()}.get("0")
                if isinstance(players, Mapping) else None)
            state = select_historical_state(entries, cutoff)
            if state is None:
                states = []
                break
            state.update({"outcome_id": str(outcome_id), "player_id": player_id})
            states.append(state)
        if len(states) != 2:
            continue
        active_states = [state for state in states if state["active"]]
        status = "OPEN" if len(active_states) == 2 else "SUSPENDED"
        out.append({
            "bookmaker": slug,
            "market_id": market_id,
            "outcome_ids": [state["outcome_id"] for state in states],
            "participant_ids": [state["player_id"] for state in states],
            "prices": [state["price"] for state in states],
            "states": [state["state"] for state in states],
            "market_status": status,
            "reconstructed_at": max(state["created_at"] for state in states),
            "states_detail": states,
            "participant_orientation": "PARTICIPANT_1_THEN_2",
        })
    return out


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _decimal_price(value: Any) -> Decimal | None:
    try:
        price = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    if not price.is_finite() or price <= Decimal("1"):
        return None
    return price


__all__ = [
    "ODDSPAPI_ADAPTER_VERSION",
    "ODDSPAPI_MATCH_WINNER_MARKET_ID",
    "ODDSPAPI_PLAYER_1_OUTCOME_ID",
    "ODDSPAPI_PLAYER_2_OUTCOME_ID",
    "ODDSPAPI_PROVIDER_CODE",
    "build_oddspapi_tennis_observation",
    "extract_oddspapi_match_winner_prices",
    "new_oddspapi_response_id",
    "reconstruct_historical_winner",
    "resolve_winner_market",
    "select_historical_state",
]
