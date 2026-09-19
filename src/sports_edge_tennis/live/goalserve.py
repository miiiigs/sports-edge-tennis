from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from typing import Any, Mapping

import requests

from sports_edge_tennis.live.transport import DEFAULT_TIMEOUT, TransportError


@dataclass(frozen=True)
class GoalserveTennisMatchEnvelope:
    category_id: str | None
    category_name: str
    provider_event_id: str
    match_start_at: datetime
    player_1_id: str
    player_1_name: str
    player_2_id: str
    player_2_name: str
    match_payload: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch_goalserve_tennis_json(
    feed_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    clock: callable | None = None,
) -> tuple[datetime, dict[str, Any]]:
    """Fetch one Goalserve Tennis JSON feed from the account-specific URL.

    Goalserve credentials may be embedded in the account URL, so the URL is
    never copied into errors or logs by this transport. Production activation
    must therefore inject the URL through a secret environment variable.
    """
    if not str(feed_url).strip():
        raise ValueError("Goalserve Tennis feed URL must be non-empty")
    clock = clock or _utc_now
    try:
        response = requests.get(feed_url, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise TransportError("TIMEOUT", "goalserve tennis request timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise TransportError("NETWORK", "goalserve tennis request failed") from exc

    received_at = clock()
    if response.status_code != 200:
        raise TransportError(
            f"HTTP_{response.status_code}",
            "goalserve tennis returned a non-200 response",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise TransportError(
            "PARSE",
            "goalserve tennis response was not valid JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise TransportError(
            "PARSE",
            "goalserve tennis response was not a JSON object",
        )
    return received_at, payload


def iter_goalserve_tennis_envelopes(
    payload: Mapping[str, Any],
) -> tuple[GoalserveTennisMatchEnvelope, ...]:
    """Extract provider events with category context and UTC start times."""
    envelopes: list[GoalserveTennisMatchEnvelope] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            categories = value.get("category")
            if categories is not None:
                for category in _as_list(categories):
                    if not isinstance(category, Mapping):
                        continue
                    category_id = str(category.get("id") or "").strip() or None
                    category_name = str(category.get("name") or "").strip()
                    for raw_match in _as_list(category.get("match")):
                        if not isinstance(raw_match, Mapping):
                            continue
                        try:
                            envelope = _match_envelope(
                                raw_match,
                                category_id=category_id,
                                category_name=category_name,
                            )
                        except ValueError:
                            continue
                        if envelope.provider_event_id in seen:
                            continue
                        seen.add(envelope.provider_event_id)
                        envelopes.append(envelope)
            for key, child in value.items():
                if key != "category":
                    visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return tuple(envelopes)


def iter_goalserve_tennis_matches(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    return tuple(envelope.match_payload for envelope in iter_goalserve_tennis_envelopes(payload))


def _match_envelope(
    match: Mapping[str, Any],
    *,
    category_id: str | None,
    category_name: str,
) -> GoalserveTennisMatchEnvelope:
    event_id = str(match.get("id") or "").strip()
    if not event_id:
        raise ValueError("missing Goalserve event id")
    raw_date = str(match.get("date") or "").strip()
    raw_time = str(match.get("time") or "").strip()
    if not raw_date or not raw_time:
        raise ValueError("missing Goalserve event time")
    try:
        day = datetime.strptime(raw_date, "%d.%m.%Y").date()
        time_part = datetime.strptime(raw_time, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("invalid Goalserve UTC event time") from exc
    start_at = datetime.combine(day, time_part, tzinfo=UTC)

    players = [item for item in _as_list(match.get("player")) if isinstance(item, Mapping)]
    if len(players) != 2:
        raise ValueError("Goalserve event must have exactly two players")
    p1_id = str(players[0].get("id") or "").strip()
    p2_id = str(players[1].get("id") or "").strip()
    p1_name = str(players[0].get("name") or "").strip()
    p2_name = str(players[1].get("name") or "").strip()
    if not p1_id or not p2_id or not p1_name or not p2_name or p1_id == p2_id:
        raise ValueError("invalid Goalserve player identity")

    return GoalserveTennisMatchEnvelope(
        category_id=category_id,
        category_name=category_name,
        provider_event_id=event_id,
        match_start_at=start_at,
        player_1_id=p1_id,
        player_1_name=p1_name,
        player_2_id=p2_id,
        player_2_name=p2_name,
        match_payload=dict(match),
    )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]
