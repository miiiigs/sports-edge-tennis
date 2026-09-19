from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from sports_edge_tennis.live.transport import DEFAULT_TIMEOUT, TransportError


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


def iter_goalserve_tennis_matches(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Extract Tennis match objects from Goalserve's nested score/feed envelope.

    JSON serializers differ on singleton vs list containers, so this traversal
    intentionally supports either without guessing participant identities.
    """
    matches: list[dict[str, Any]] = []

    def visit(value: Any, parent_key: str | None = None) -> None:
        if isinstance(value, Mapping):
            if parent_key == "match" and "id" in value:
                matches.append(dict(value))
                return
            for key, child in value.items():
                if key == "match":
                    for item in _as_list(child):
                        if isinstance(item, Mapping) and "id" in item:
                            matches.append(dict(item))
                else:
                    visit(child, str(key))
        elif isinstance(value, list):
            for item in value:
                visit(item, parent_key)

    visit(payload)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for match in matches:
        match_id = str(match.get("id") or "").strip()
        if not match_id or match_id in seen:
            continue
        seen.add(match_id)
        unique.append(match)
    return tuple(unique)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]
