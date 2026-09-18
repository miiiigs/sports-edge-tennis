"""Secret-safe read-only Sportradar Tennis v3 client.

This module is intentionally isolated from live prediction, Manager, Market, Veto,
staking, settlement, and canonical ingestion paths. It exists so Sportradar can
be benchmarked alongside the current providers before any production-data role is
considered.

Authentication follows Sportradar's current Tennis v3 contract: the API key is
sent only in the `x-api-key` header, never in a URL or log message.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from collections.abc import Mapping
from typing import Any, Callable
from urllib.parse import quote

import requests


SPORTRADAR_TENNIS_BASE_URL = "https://api.sportradar.com/tennis"
DEFAULT_TIMEOUT = 10.0
_ALLOWED_ACCESS_LEVELS = {"trial", "production"}
_ALLOWED_FORMATS = {"json"}
_ALLOWED_LANGUAGES = {
    "de",
    "en",
    "en_us",
    "es",
    "fr",
    "id",
    "it",
    "ja",
    "ru",
    "th",
    "zh",
    "zht",
}


class SportradarTransportError(RuntimeError):
    """Secret-safe Sportradar request failure."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validated_segment(value: str, *, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return quote(text, safe=":-_")


def _request_json(
    api_key: str,
    resource: str,
    *,
    access_level: str = "trial",
    language: str = "en",
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = SPORTRADAR_TENNIS_BASE_URL,
    clock: Callable[[], datetime] | None = None,
    http_get: Callable[..., Any] = requests.get,
    query_params: Mapping[str, Any] | None = None,
) -> tuple[datetime, dict[str, Any]]:
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("Sportradar API key must be non-empty")
    if access_level not in _ALLOWED_ACCESS_LEVELS:
        raise ValueError("Sportradar access_level must be trial or production")
    if language not in _ALLOWED_LANGUAGES:
        raise ValueError("Unsupported Sportradar language")

    clean_resource = resource.strip("/")
    if not clean_resource:
        raise ValueError("Sportradar resource must be non-empty")

    url = f"{base_url.rstrip('/')}/{access_level}/v3/{language}/{clean_resource}.json"
    headers = {
        "accept": "application/json",
        "x-api-key": key,
    }
    request_kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout}
    if query_params:
        request_kwargs["params"] = dict(query_params)
    try:
        response = http_get(url, **request_kwargs)
    except requests.exceptions.Timeout as exc:
        raise SportradarTransportError(
            "TIMEOUT", "sportradar tennis request timed out"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise SportradarTransportError(
            "NETWORK", "sportradar tennis request failed"
        ) from exc

    received_at = (clock or _utc_now)()
    if response.status_code != 200:
        raise SportradarTransportError(
            f"HTTP_{response.status_code}",
            "sportradar tennis returned a non-200 response",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise SportradarTransportError(
            "PARSE", "sportradar tennis response was not valid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise SportradarTransportError(
            "PARSE", "sportradar tennis response was not a JSON object"
        )
    return received_at, payload


def fetch_competitions(api_key: str, **kwargs: Any) -> tuple[datetime, dict[str, Any]]:
    return _request_json(api_key, "competitions", **kwargs)


def fetch_seasons(api_key: str, **kwargs: Any) -> tuple[datetime, dict[str, Any]]:
    return _request_json(api_key, "seasons", **kwargs)


def fetch_rankings(api_key: str, **kwargs: Any) -> tuple[datetime, dict[str, Any]]:
    return _request_json(api_key, "rankings", **kwargs)


def fetch_competitor_merge_mappings(
    api_key: str, **kwargs: Any
) -> tuple[datetime, dict[str, Any]]:
    return _request_json(api_key, "competitors/merge_mappings", **kwargs)


def fetch_competition_seasons(
    api_key: str,
    competition_id: str,
    **kwargs: Any,
) -> tuple[datetime, dict[str, Any]]:
    competition = _validated_segment(competition_id, name="competition_id")
    return _request_json(api_key, f"competitions/{competition}/seasons", **kwargs)


def fetch_season_info(
    api_key: str,
    season_id: str,
    **kwargs: Any,
) -> tuple[datetime, dict[str, Any]]:
    season = _validated_segment(season_id, name="season_id")
    return _request_json(api_key, f"seasons/{season}/info", **kwargs)


def fetch_daily_summaries(
    api_key: str,
    slate_date: date,
    **kwargs: Any,
) -> tuple[datetime, dict[str, Any]]:
    return _request_json(
        api_key,
        f"schedules/{slate_date.isoformat()}/summaries",
        **kwargs,
    )


def fetch_season_summaries(
    api_key: str,
    season_id: str,
    *,
    start: int = 0,
    **kwargs: Any,
) -> tuple[datetime, dict[str, Any]]:
    if start < 0:
        raise ValueError("Sportradar season summaries start must be non-negative")
    season = _validated_segment(season_id, name="season_id")
    query_params = {"start": start} if start else None
    return _request_json(
        api_key,
        f"seasons/{season}/summaries",
        query_params=query_params,
        **kwargs,
    )


def fetch_sport_event_summary(
    api_key: str,
    sport_event_id: str,
    **kwargs: Any,
) -> tuple[datetime, dict[str, Any]]:
    event_id = _validated_segment(sport_event_id, name="sport_event_id")
    return _request_json(api_key, f"sport_events/{event_id}/summary", **kwargs)


def fetch_sport_event_timeline(
    api_key: str,
    sport_event_id: str,
    **kwargs: Any,
) -> tuple[datetime, dict[str, Any]]:
    event_id = _validated_segment(sport_event_id, name="sport_event_id")
    return _request_json(api_key, f"sport_events/{event_id}/timeline", **kwargs)


__all__ = [
    "SportradarTransportError",
    "fetch_competitions",
    "fetch_seasons",
    "fetch_rankings",
    "fetch_competitor_merge_mappings",
    "fetch_competition_seasons",
    "fetch_season_info",
    "fetch_daily_summaries",
    "fetch_season_summaries",
    "fetch_sport_event_summary",
    "fetch_sport_event_timeline",
]
