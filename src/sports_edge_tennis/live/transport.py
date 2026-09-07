"""Minimal concrete HTTP transports for the Live MVP providers.

No retries, no polling, bounded timeouts, secret-safe error handling. The
API-Tennis key is carried as the documented ``APIkey`` query parameter and the
pinnapi key as the documented ``x-portal-apikey`` header. Neither is ever
included in an exception message or persisted by this module.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import requests


API_TENNIS_BASE_URL = "https://api.api-tennis.com/tennis/"
PINNAPI_BASE_URL = "https://pinnapi.com"

DEFAULT_TIMEOUT = 10.0


class TransportError(RuntimeError):
    """Secret-safe transport failure.

    ``kind`` is a stable classifier (e.g. ``TIMEOUT``, ``HTTP_401``,
    ``NON_200``, ``PARSE``, ``PROVIDER_ERROR``). ``message`` never contains a
    credential or a credential-bearing URL.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch_api_tennis_fixtures(
    api_key: str,
    slate_date: date,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = API_TENNIS_BASE_URL,
    clock: callable | None = None,
) -> tuple[datetime, list[dict[str, Any]]]:
    clock = clock or _utc_now
    date_str = slate_date.isoformat()
    params = {
        "method": "get_fixtures",
        "APIkey": api_key,
        "date_start": date_str,
        "date_stop": date_str,
        "timezone": "UTC",
    }
    try:
        resp = requests.get(base_url, params=params, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise TransportError("TIMEOUT", "api-tennis request timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise TransportError("NETWORK", "api-tennis request failed") from exc

    received_at = clock()
    if resp.status_code != 200:
        raise TransportError(f"HTTP_{resp.status_code}", "api-tennis non-200 response")

    try:
        data = resp.json()
    except ValueError as exc:
        raise TransportError("PARSE", "api-tennis response was not valid JSON") from exc

    if not isinstance(data, dict) or data.get("success") != 1:
        raise TransportError("PROVIDER_ERROR", "api-tennis provider reported failure")

    result = data.get("result")
    if not isinstance(result, list):
        raise TransportError("PARSE", "api-tennis response missing result list")

    return received_at, result


def fetch_pinnapi_board(
    pinnapi_key: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = PINNAPI_BASE_URL,
    clock: callable | None = None,
) -> tuple[datetime, dict[str, Any]]:
    clock = clock or _utc_now
    url = f"{base_url}/kit/v1/markets"
    params = {"sport_id": "2", "event_type": "prematch"}
    headers = {"x-portal-apikey": pinnapi_key}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise TransportError("TIMEOUT", "pinnapi request timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise TransportError("NETWORK", "pinnapi request failed") from exc

    received_at = clock()
    if resp.status_code != 200:
        raise TransportError(f"HTTP_{resp.status_code}", "pinnapi non-200 response")

    try:
        data = resp.json()
    except ValueError as exc:
        raise TransportError("PARSE", "pinnapi response was not valid JSON") from exc

    if not isinstance(data, dict):
        raise TransportError("PARSE", "pinnapi response was not a JSON object")

    return received_at, data
