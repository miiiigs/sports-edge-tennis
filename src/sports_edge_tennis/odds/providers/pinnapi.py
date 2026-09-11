"""Offline pinnapi Source B adapter: board parsing and raw observation construction.

This module parses a deterministic pinnapi prematch board payload and classifies
valid top-level Tennis Match Winner events. It performs NO network I/O.

Verified provider endpoint shape (not invoked here):

    GET /kit/v1/markets?sport_id=2&event_type=prematch

returns a BOARD of Tennis events. One successful board response is one immutable
board observation identity.

Board payload shape accepted here (official pinnapi event contract)::

    {
      "events": [
        {
          "event_id": "1635768302",
          "parent_id": null,
          "event_type": "prematch",
          "league_id": "202301",
          "league_name": "ATP Challenger Mallorca - SF",
          "starts": "2026-09-05T13:10:00Z",
          "home": "Mark Lajal",
          "away": "Felix Balshaw",
          "periods": {
            "num_0": {
              "status": "open",
              "description": "Game",          # ignored for classification
              "money_line": {"home": 1.5682, "away": 2.55, "draw": null}
            }
          }
        }
      ]
    }

The official contract exposes top-level ``home`` / ``away`` participant fields,
where ``home`` corresponds directly to ``money_line.home`` and ``away`` to
``money_line.away``. The start time is parsed from ``starts`` (preferred) or
``start_ts`` (fallback alias). ``periods.num_0.description`` is never used for
classification.

A top-level Tennis Match Winner event is normalization-eligible only if it is
prematch, has no parent, carries no child suffix (``(Games)`` / ``(Sets)``),
has an open ``num_0`` period with a two-sided money_line of valid decimal odds
greater than 1, and a null draw. Everything else is rejected (fail-closed).

The parser is provider-oriented: it never assigns ``canonical_player_id`` and
never creates PINNAPI player source ids. Canonical orientation is produced later
by the frozen Step 2 reconciliation layer.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from sports_edge_tennis.odds.contracts import (
    ProviderBookmakerMarket,
    ProviderObservation,
    ProviderSelection,
)
from sports_edge_tennis.odds.reconciliation import (
    OrientedPinnapiMatch,
    PinnapiEventEvidence,
)


PINNAPI_PROVIDER_CODE = "PINNAPI"
PINNAPI_ADAPTER_VERSION = "PINNAPI_SOURCE_B_V1"
PINNAPI_BOOKMAKER_KEY = "pinnacle"
PINNAPI_BOARD_IDENTITY_VERSION = "PINNAPI_BOARD_V1"

_ACCEPTED_PERIOD_STATUSES = {"open", "accepted"}
_CHILD_SUFFIX_RE = re.compile(r"\((Games|Sets)\)\s*$", flags=re.IGNORECASE)


class PinnapiBoardError(ValueError):
    """Raised when a pinnapi board payload cannot be safely parsed."""


@dataclass(frozen=True)
class PinnapiBoardEvent:
    """A normalization-eligible top-level Tennis Match Winner event."""

    provider_event_id: str
    home_name: str
    away_name: str
    scheduled_start: datetime
    league_id: str | None
    league_name: str | None
    home_decimal_price: Decimal
    away_decimal_price: Decimal
    market_status: str
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class PinnapiRejectedEvent:
    """A board event that must not normalize as a standard Match Winner."""

    event_id: str | None
    reason: str


@dataclass(frozen=True)
class PinnapiBoardParseResult:
    board_identity: str
    received_at: datetime
    events: tuple[PinnapiBoardEvent, ...]
    rejected_events: tuple[PinnapiRejectedEvent, ...]

    def raw_observations(self) -> tuple[ProviderObservation, ...]:
        return tuple(
            to_pinnapi_raw_observation(
                event,
                board_identity=self.board_identity,
                received_at=self.received_at,
            )
            for event in self.events
        )

    def event_evidences(self) -> tuple[PinnapiEventEvidence, ...]:
        return tuple(to_pinnapi_event_evidence(event) for event in self.events)


def new_pinnapi_board_observation_id() -> str:
    """Return a fresh, unique identifier for one successful board response."""
    return str(uuid.uuid4())


def pinnapi_board_identity(*, received_at: datetime, board_observation_id: str) -> str:
    if received_at.tzinfo is None:
        raise PinnapiBoardError("received_at must be timezone-aware")
    if not board_observation_id.strip():
        raise PinnapiBoardError("board_observation_id must be non-empty")
    return (
        f"{PINNAPI_BOARD_IDENTITY_VERSION}:"
        f"{received_at.astimezone(timezone.utc).isoformat()}:"
        f"{board_observation_id.strip()}"
    )


def pinnapi_event_observation_key(*, board_identity: str, provider_event_id: str) -> str:
    return f"{board_identity}:EVENT:{provider_event_id}"


def parse_pinnapi_board(
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    board_observation_id: str,
) -> PinnapiBoardParseResult:
    if not isinstance(payload, Mapping):
        raise PinnapiBoardError("board_payload_must_be_mapping")
    if received_at.tzinfo is None:
        raise PinnapiBoardError("received_at must be timezone-aware")
    received_at_utc = received_at.astimezone(timezone.utc)
    board_identity = pinnapi_board_identity(
        received_at=received_at_utc,
        board_observation_id=board_observation_id,
    )

    raw_events = payload.get("events")
    if raw_events is None:
        raw_events = []
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        raise PinnapiBoardError("events_must_be_a_list")

    events: list[PinnapiBoardEvent] = []
    rejected: list[PinnapiRejectedEvent] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, Mapping):
            rejected.append(PinnapiRejectedEvent(event_id=None, reason="invalid_event_entry"))
            continue
        try:
            events.append(_parse_event(raw_event))
        except PinnapiBoardError as exc:
            rejected.append(
                PinnapiRejectedEvent(event_id=_safe_event_id(raw_event), reason=str(exc))
            )

    return PinnapiBoardParseResult(
        board_identity=board_identity,
        received_at=received_at_utc,
        events=tuple(events),
        rejected_events=tuple(rejected),
    )


def to_pinnapi_event_evidence(event: PinnapiBoardEvent) -> PinnapiEventEvidence:
    return PinnapiEventEvidence(
        provider_event_id=event.provider_event_id,
        home_name=event.home_name,
        away_name=event.away_name,
        scheduled_start=event.scheduled_start,
        league_id=event.league_id,
        league_name=event.league_name,
        parent_id=None,
        event_type="prematch",
    )


def to_pinnapi_raw_observation(
    event: PinnapiBoardEvent,
    *,
    board_identity: str,
    received_at: datetime,
) -> ProviderObservation:
    return ProviderObservation(
        provider_code=PINNAPI_PROVIDER_CODE,
        provider_event_id=event.provider_event_id,
        provider_observation_key=pinnapi_event_observation_key(
            board_identity=board_identity,
            provider_event_id=event.provider_event_id,
        ),
        received_at=received_at,
        payload=event.raw_payload,
        adapter_version=PINNAPI_ADAPTER_VERSION,
        markets=(),
        provider_observed_at=None,
        provider_response_id=board_identity,
    )


def build_pinnapi_market(
    event: PinnapiBoardEvent,
    orientation: OrientedPinnapiMatch,
) -> ProviderBookmakerMarket:
    """Construct canonical PINNAPI selections using the reconciled orientation.

    The parser never assigns canonical_player_id on its own. This boundary takes
    the already-reconciled Step 2 orientation and emits provider selections whose
    canonical ids come from that orientation.
    """
    if orientation.provider_event_id != event.provider_event_id:
        raise PinnapiBoardError("orientation_event_id_mismatch")
    home_selection = ProviderSelection(
        provider_player_id=None,
        decimal_price=event.home_decimal_price,
        canonical_player_id=orientation.home_canonical_player_id,
    )
    away_selection = ProviderSelection(
        provider_player_id=None,
        decimal_price=event.away_decimal_price,
        canonical_player_id=orientation.away_canonical_player_id,
    )
    return ProviderBookmakerMarket(
        provider_bookmaker_key=PINNAPI_BOOKMAKER_KEY,
        provider_market_key="num_0_moneyline",
        market_type="MATCH_WINNER",
        market_status=event.market_status,
        selections=(home_selection, away_selection),
    )


def _parse_event(raw_event: Mapping[str, Any]) -> PinnapiBoardEvent:
    event_id = _required_event_id(raw_event)

    event_type = _normalize_token(_optional_text(raw_event.get("event_type")) or "prematch")
    if event_type != "prematch":
        raise PinnapiBoardError("non_prematch")

    parent_id = raw_event.get("parent_id")
    if parent_id is not None and _optional_text(parent_id):
        raise PinnapiBoardError("child_parent_id")

    home_name = _optional_text(raw_event.get("home"))
    away_name = _optional_text(raw_event.get("away"))
    if home_name is None or away_name is None:
        raise PinnapiBoardError("missing_participant")
    if home_name == away_name:
        raise PinnapiBoardError("duplicate_participant")
    if _has_child_suffix(home_name) or _has_child_suffix(away_name):
        raise PinnapiBoardError("child_suffix")

    scheduled_start = _parse_start_field(raw_event)
    if scheduled_start is None:
        raise PinnapiBoardError("invalid_start")

    league_id = _optional_text(raw_event.get("league_id"))
    league_name = _optional_text(raw_event.get("league_name"))

    money_line = _money_line(raw_event)
    home_price = _side_price(money_line, "home")
    away_price = _side_price(money_line, "away")
    if money_line.get("draw") is not None:
        raise PinnapiBoardError("three_way_structure")

    return PinnapiBoardEvent(
        provider_event_id=event_id,
        home_name=home_name,
        away_name=away_name,
        scheduled_start=scheduled_start,
        league_id=league_id,
        league_name=league_name,
        home_decimal_price=home_price,
        away_decimal_price=away_price,
        market_status="OPEN",
        raw_payload=dict(raw_event),
    )


def _required_event_id(raw_event: Mapping[str, Any]) -> str:
    value = raw_event.get("event_id")
    event_id = _optional_text(value)
    if event_id is None:
        raise PinnapiBoardError("missing_event_id")
    return event_id


def _parse_start_field(raw_event: Mapping[str, Any]) -> datetime | None:
    starts = _parse_start(raw_event.get("starts"))
    start_ts = _parse_start(raw_event.get("start_ts"))
    if starts is not None and start_ts is not None:
        if starts != start_ts:
            raise PinnapiBoardError("start_alias_conflict")
        return starts
    if starts is not None:
        return starts
    if start_ts is not None:
        return start_ts
    return None


def _money_line(raw_event: Mapping[str, Any]) -> Mapping[str, Any]:
    periods = raw_event.get("periods")
    if not isinstance(periods, Mapping):
        raise PinnapiBoardError("missing_moneyline")
    num_0 = periods.get("num_0")
    if not isinstance(num_0, Mapping):
        raise PinnapiBoardError("missing_moneyline")

    status = _normalize_token(_optional_text(num_0.get("status")) or "")
    if status not in _ACCEPTED_PERIOD_STATUSES:
        raise PinnapiBoardError("period_not_open")

    money_line = num_0.get("money_line")
    if not isinstance(money_line, Mapping):
        raise PinnapiBoardError("missing_moneyline")
    return money_line


def _side_price(money_line: Mapping[str, Any], side: str) -> Decimal:
    if side not in money_line or money_line[side] is None:
        raise PinnapiBoardError(f"missing_side:{side}")
    price = _parse_price(money_line[side])
    if price is None:
        raise PinnapiBoardError(f"invalid_price:{side}")
    return price


def _parse_price(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not price.is_finite():
        return None
    if price <= Decimal("1"):
        return None
    return price


def _parse_start(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _has_child_suffix(value: str) -> bool:
    return bool(_CHILD_SUFFIX_RE.search(value.strip()))


def _safe_event_id(raw_event: Mapping[str, Any]) -> str | None:
    return _optional_text(raw_event.get("event_id"))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_token(value: str) -> str:
    return value.strip().casefold()
