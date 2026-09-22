from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

import requests

from sports_edge_tennis.live.transport import DEFAULT_TIMEOUT, TransportError


ODDSPAPI_BASE_URL = "https://api.oddspapi.io/v4"
ODDSPAPI_TENNIS_SPORT_ID = 12
ODDSPAPI_PLAN_BOOKMAKERS = ("pinnacle+30", "1xbet", "unibet", "bet365")
ODDSPAPI_SHARP_BOOKMAKER = "pinnacle+30"
ODDSPAPI_FIXTURE_STATUS_IDS = frozenset({0, 1, 2, 3})
ODDSPAPI_SPORT_FIXTURE_MAX_WINDOW = timedelta(hours=48)


@dataclass(frozen=True)
class OddspapiTennisFixtureEnvelope:
    fixture_id: str
    tournament_id: int
    participant_1_id: str
    participant_1_name: str
    participant_2_id: str
    participant_2_name: str
    match_start_at: datetime
    betradar_id: str | None
    raw_payload: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def fetch_oddspapi_tennis_fixtures(
    api_key: str,
    *,
    from_at: datetime,
    to_at: datetime,
    status_id: int = 0,
    bookmakers: Sequence[str] = (ODDSPAPI_SHARP_BOOKMAKER,),
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = ODDSPAPI_BASE_URL,
    clock: callable | None = None,
) -> tuple[datetime, list[dict[str, Any]]]:
    if from_at.tzinfo is None or to_at.tzinfo is None:
        raise ValueError("OddsPapi fixture window must be timezone-aware")
    if to_at <= from_at:
        raise ValueError("OddsPapi fixture window must have to_at > from_at")
    if status_id not in ODDSPAPI_FIXTURE_STATUS_IDS:
        raise ValueError("OddsPapi fixture status_id must be one of 0, 1, 2, 3")
    # OddsPapi's sportId + from/to fixture discovery contract requires windows
    # strictly under 48 hours when no tournamentId or participantId is supplied.
    if to_at - from_at >= ODDSPAPI_SPORT_FIXTURE_MAX_WINDOW:
        raise ValueError("OddsPapi sport fixture window must be strictly under 48 hours")
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("OddsPapi API key must be non-empty")
    clock = clock or _utc_now
    params = {
        "apiKey": key,
        "sportId": ODDSPAPI_TENNIS_SPORT_ID,
        "from": _iso_z(from_at),
        "to": _iso_z(to_at),
        "statusId": status_id,
        "hasOdds": "true",
        "bookmakers": ",".join(_clean_bookmakers(bookmakers)),
        "language": "en",
    }
    payload = _get_json(
        f"{base_url.rstrip('/')}/fixtures",
        params=params,
        timeout=timeout,
        label="oddspapi fixtures",
    )
    received_at = clock()
    rows = _fixture_list(payload)
    return received_at, rows


def fetch_oddspapi_tennis_odds_by_tournaments(
    api_key: str,
    *,
    tournament_ids: Sequence[int],
    bookmakers: Sequence[str] = ODDSPAPI_PLAN_BOOKMAKERS,
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = ODDSPAPI_BASE_URL,
    clock: callable | None = None,
) -> tuple[datetime, list[dict[str, Any]]]:
    ids = tuple(dict.fromkeys(int(value) for value in tournament_ids))
    if not ids:
        raise ValueError("OddsPapi tournament_ids must be non-empty")
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("OddsPapi API key must be non-empty")
    clock = clock or _utc_now
    params = {
        "apiKey": key,
        "tournamentIds": ",".join(str(value) for value in ids),
        "bookmakers": ",".join(_clean_bookmakers(bookmakers)),
        "language": "en",
        "verbosity": 3,
        "oddsFormat": "decimal",
    }
    payload = _get_json(
        f"{base_url.rstrip('/')}/odds-by-tournaments",
        params=params,
        timeout=timeout,
        label="oddspapi tournament odds",
    )
    received_at = clock()
    return received_at, _fixture_list(payload)


def merge_oddspapi_fixture_odds(
    fixture_rows: Sequence[Mapping[str, Any]],
    odds_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join odds-board rows to fixture-discovery identity by fixtureId.

    Odds-by-tournaments is optimized for price payloads and must not be relied on
    to repeat every identity field exposed by /v4/fixtures. Discovery remains
    authoritative for participants, start time, tournament, sport, and external
    provider links; the odds row overlays price/status fields.
    """
    discovery: dict[str, dict[str, Any]] = {}
    for row in fixture_rows:
        fixture_id = str(row.get("fixtureId") or "").strip()
        if fixture_id and fixture_id not in discovery:
            discovery[fixture_id] = dict(row)

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in odds_rows:
        fixture_id = str(row.get("fixtureId") or "").strip()
        if not fixture_id or fixture_id in seen:
            continue
        base = discovery.get(fixture_id)
        if base is None:
            continue
        combined = dict(base)
        combined.update(dict(row))
        # Identity fields stay governed by fixture discovery even when an odds
        # response happens to repeat them with a different omission/shape.
        for key in (
            "fixtureId",
            "sportId",
            "tournamentId",
            "participant1Id",
            "participant1Name",
            "participant2Id",
            "participant2Name",
            "startTime",
            "externalProviders",
        ):
            if key in base:
                combined[key] = base[key]
        merged.append(combined)
        seen.add(fixture_id)
    return merged


def parse_oddspapi_tennis_fixture(
    payload: Mapping[str, Any],
) -> OddspapiTennisFixtureEnvelope:
    fixture_id = _required_text(payload.get("fixtureId"), "missing_fixture_id")
    sport_id = _required_int(payload.get("sportId"), "missing_sport_id")
    if sport_id != ODDSPAPI_TENNIS_SPORT_ID:
        raise ValueError("non_tennis_fixture")
    tournament_id = _required_int(payload.get("tournamentId"), "missing_tournament_id")
    participant_1_id = _required_text(payload.get("participant1Id"), "missing_participant_1_id")
    participant_2_id = _required_text(payload.get("participant2Id"), "missing_participant_2_id")
    if participant_1_id == participant_2_id:
        raise ValueError("duplicate_participant_id")
    participant_1_name = _required_text(payload.get("participant1Name"), "missing_participant_1_name")
    participant_2_name = _required_text(payload.get("participant2Name"), "missing_participant_2_name")
    if participant_1_name.casefold() == participant_2_name.casefold():
        raise ValueError("duplicate_participant_name")
    match_start_at = _parse_timestamp(payload.get("startTime"), "invalid_start_time")
    external = payload.get("externalProviders")
    betradar_id = None
    if isinstance(external, Mapping) and external.get("betradarId") not in (None, ""):
        betradar_id = str(external.get("betradarId")).strip() or None
    return OddspapiTennisFixtureEnvelope(
        fixture_id=fixture_id,
        tournament_id=tournament_id,
        participant_1_id=participant_1_id,
        participant_1_name=participant_1_name,
        participant_2_id=participant_2_id,
        participant_2_name=participant_2_name,
        match_start_at=match_start_at,
        betradar_id=betradar_id,
        raw_payload=dict(payload),
    )


def _get_json(url: str, *, params: Mapping[str, Any], timeout: float, label: str) -> Any:
    try:
        response = requests.get(url, params=dict(params), timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise TransportError("TIMEOUT", f"{label} request timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise TransportError("NETWORK", f"{label} request failed") from exc
    if response.status_code != 200:
        if response.status_code == 404 and label == "oddspapi fixtures":
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if (
                isinstance(payload, Mapping)
                and isinstance(payload.get("error"), Mapping)
                and payload["error"].get("code") == "FIXTURE_NOT_FOUND"
            ):
                return []
        raise TransportError(f"HTTP_{response.status_code}", f"{label} returned a non-200 response")
    try:
        return response.json()
    except ValueError as exc:
        raise TransportError("PARSE", f"{label} response was not valid JSON") from exc


def _fixture_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, Mapping)]
    if isinstance(payload, Mapping):
        if payload.get("fixtureId") is not None:
            return [dict(payload)]
        fixtures = payload.get("fixtures")
        if isinstance(fixtures, list):
            return [dict(row) for row in fixtures if isinstance(row, Mapping)]
    raise TransportError("PARSE", "oddspapi response did not contain fixture rows")


def _clean_bookmakers(values: Sequence[str]) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if not cleaned:
        raise ValueError("OddsPapi bookmakers must be non-empty")
    return cleaned


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _required_text(value: Any, reason: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(reason)
    return text


def _required_int(value: Any, reason: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(reason) from exc


def _parse_timestamp(value: Any, reason: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(reason)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(reason) from exc
    if parsed.tzinfo is None:
        raise ValueError(reason)
    return parsed.astimezone(UTC)


__all__ = [
    "ODDSPAPI_BASE_URL",
    "ODDSPAPI_FIXTURE_STATUS_IDS",
    "ODDSPAPI_PLAN_BOOKMAKERS",
    "ODDSPAPI_SHARP_BOOKMAKER",
    "ODDSPAPI_SPORT_FIXTURE_MAX_WINDOW",
    "ODDSPAPI_TENNIS_SPORT_ID",
    "OddspapiTennisFixtureEnvelope",
    "fetch_oddspapi_tennis_fixtures",
    "fetch_oddspapi_tennis_odds_by_tournaments",
    "merge_oddspapi_fixture_odds",
    "parse_oddspapi_tennis_fixture",
]
