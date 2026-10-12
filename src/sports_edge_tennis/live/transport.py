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

    def __init__(self, kind: str, message: str, *, category: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        # Optional diagnostic from a fixed allowlist; never provider text.
        self.category = category


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def api_tennis_failure_category(payload: Any) -> str:
    """Classify a failed provider envelope without logging untrusted text.

    Diagnostic only: never changes TransportError.kind or provider failover.
    API-Tennis errors may be strings or nested objects in different endpoints.
    Categories are from a fixed allowlist; never return raw provider payload.
    """
    if not isinstance(payload, dict):
        return "INVALID_ENVELOPE"
    return _classify_failure_payload(payload, keys=("error", "message", "result"))


def _classify_failure_payload(payload: dict[str, Any], *, keys: tuple[str, ...]) -> str:
    values: list[str] = []
    for key in keys:
        item = payload.get(key)
        if isinstance(item, str):
            values.append(item[:512].lower())
        elif isinstance(item, dict):
            for subkey in ("error", "message", "code"):
                value = item.get(subkey)
                if isinstance(value, str):
                    values.append(value[:512].lower())

    message = " ".join(values)
    if any(term in message for term in ("api key", "apikey", "api_key", "invalid key", "unauthorized", "authentication")):
        return "AUTH_OR_KEY"
    if any(term in message for term in ("rate limit", "too many requests", "quota", "request limit", "limit reached", "requests exceeded")):
        return "QUOTA_OR_RATE"
    if any(term in message for term in ("subscription", "plan", "payment", "billing", "expired", "credits")):
        return "ACCOUNT_OR_PLAN"
    return "UNCLASSIFIED"


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
        raise TransportError("PROVIDER_ERROR", f"api-tennis provider reported failure diagnostic={api_tennis_failure_category(data)}")

    result = data.get("result")
    if not isinstance(result, list):
        raise TransportError("PARSE", "api-tennis response missing result list")

    return received_at, result


def fetch_api_tennis_draw(
    api_key: str,
    tournament_key: str,
    tournament_season: int,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = API_TENNIS_BASE_URL,
    clock: callable | None = None,
) -> tuple[datetime, dict[str, Any]]:
    """Fetch one tournament draw for authoritative tournament surface metadata."""
    clock = clock or _utc_now
    params = {
        "method": "get_draw",
        "APIkey": api_key,
        "tournament_key": str(tournament_key),
        "tournament_season": str(tournament_season),
        "include_qualification": "1",
    }
    try:
        resp = requests.get(base_url, params=params, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise TransportError("TIMEOUT", "api-tennis draw request timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise TransportError("NETWORK", "api-tennis draw request failed") from exc

    received_at = clock()
    if resp.status_code != 200:
        raise TransportError(f"HTTP_{resp.status_code}", "api-tennis draw non-200 response")

    try:
        data = resp.json()
    except ValueError as exc:
        raise TransportError("PARSE", "api-tennis draw response was not valid JSON") from exc

    if not isinstance(data, dict) or data.get("success") != 1:
        raise TransportError("PROVIDER_ERROR", "api-tennis draw provider reported failure")

    result = data.get("result")
    if not isinstance(result, dict):
        raise TransportError("PARSE", "api-tennis draw response missing result object")

    return received_at, result


def fetch_api_tennis_h2h(
    api_key: str,
    first_player_key: str,
    second_player_key: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = API_TENNIS_BASE_URL,
    clock: callable | None = None,
) -> tuple[datetime, dict[str, Any]]:
    """Fetch API-Tennis direct H2H and recent-form rows for one player pair.

    This is a single bounded provider request. The caller decides whether to use
    the returned evidence; this transport performs no persistence or model work.
    """
    first_key = str(first_player_key).strip()
    second_key = str(second_player_key).strip()
    if not first_key or not second_key:
        raise ValueError("API-Tennis H2H player keys must be non-empty")
    if first_key == second_key:
        raise ValueError("API-Tennis H2H player keys must be different")

    clock = clock or _utc_now
    params = {
        "method": "get_H2H",
        "APIkey": api_key,
        "first_player_key": first_key,
        "second_player_key": second_key,
    }
    try:
        resp = requests.get(base_url, params=params, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise TransportError("TIMEOUT", "api-tennis H2H request timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise TransportError("NETWORK", "api-tennis H2H request failed") from exc

    received_at = clock()
    if resp.status_code != 200:
        raise TransportError(f"HTTP_{resp.status_code}", "api-tennis H2H non-200 response")

    try:
        data = resp.json()
    except ValueError as exc:
        raise TransportError("PARSE", "api-tennis H2H response was not valid JSON") from exc

    if not isinstance(data, dict) or data.get("success") != 1:
        raise TransportError("PROVIDER_ERROR", "api-tennis H2H provider reported failure")

    result = data.get("result")
    if not isinstance(result, dict):
        raise TransportError("PARSE", "api-tennis H2H response missing result object")

    return received_at, result


def fetch_api_tennis_odds(
    api_key: str,
    start_date: date,
    stop_date: date,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = API_TENNIS_BASE_URL,
    clock: callable | None = None,
) -> tuple[datetime, dict[str, dict[str, Any]]]:
    """Fetch one bounded API-Tennis pre-match odds window.

    The documented ``get_odds`` response is an object keyed by API-Tennis
    match/event key. No retries or polling are performed.
    """
    if stop_date < start_date:
        raise ValueError("stop_date must be >= start_date")
    clock = clock or _utc_now
    params = {
        "method": "get_odds",
        "APIkey": api_key,
        "date_start": start_date.isoformat(),
        "date_stop": stop_date.isoformat(),
    }
    try:
        resp = requests.get(base_url, params=params, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise TransportError("TIMEOUT", "api-tennis odds request timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise TransportError("NETWORK", "api-tennis odds request failed") from exc

    received_at = clock()
    if resp.status_code != 200:
        raise TransportError(f"HTTP_{resp.status_code}", "api-tennis odds non-200 response")

    try:
        data = resp.json()
    except ValueError as exc:
        raise TransportError("PARSE", "api-tennis odds response was not valid JSON") from exc

    if not isinstance(data, dict) or data.get("success") != 1:
        raise TransportError("PROVIDER_ERROR", "api-tennis odds provider reported failure")

    result = data.get("result")
    if result in (None, []):
        return received_at, {}
    if not isinstance(result, dict):
        raise TransportError("PARSE", "api-tennis odds response missing result object")

    normalized: dict[str, dict[str, Any]] = {}
    for event_key, event_payload in result.items():
        if isinstance(event_payload, dict):
            normalized[str(event_key)] = event_payload
    return received_at, normalized


PINNAPI_FAILURE_CATEGORIES = frozenset(
    {
        "AUTH_OR_KEY",
        "QUOTA_OR_RATE",
        "ACCOUNT_OR_PLAN",
        "EDGE_CHALLENGE",
        "HTML_RESPONSE",
        "UNCLASSIFIED",
    }
)


def pinnapi_failure_category(resp: Any) -> str:
    """Classify a non-200 Pinnapi response without retaining provider text.

    Distinguishes an edge/WAF challenge or HTML error page from a JSON account,
    key or quota refusal, so an HTTP_403 can be routed to the right owner.
    Diagnostic only: never changes ``TransportError.kind`` or failover.
    """
    headers = getattr(resp, "headers", None) or {}
    header_names = {str(name).lower() for name in headers}
    content_type = ""
    for name in headers:
        if str(name).lower() == "content-type":
            content_type = str(headers[name]).lower()
    if "cf-mitigated" in header_names:
        return "EDGE_CHALLENGE"
    if "text/html" in content_type:
        return "HTML_RESPONSE"
    try:
        payload = resp.json()
    except (ValueError, AttributeError):
        return "UNCLASSIFIED"
    if not isinstance(payload, dict):
        return "UNCLASSIFIED"
    category = _classify_failure_payload(
        payload, keys=("error", "message", "detail", "result")
    )
    return category if category in PINNAPI_FAILURE_CATEGORIES else "UNCLASSIFIED"


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
        raise TransportError(
            f"HTTP_{resp.status_code}",
            "pinnapi non-200 response",
            category=pinnapi_failure_category(resp),
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise TransportError("PARSE", "pinnapi response was not valid JSON") from exc

    if not isinstance(data, dict):
        raise TransportError("PARSE", "pinnapi response was not a JSON object")

    return received_at, data
