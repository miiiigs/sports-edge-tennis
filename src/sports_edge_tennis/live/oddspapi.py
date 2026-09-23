from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping, Sequence

import requests

from sports_edge_tennis.live.transport import DEFAULT_TIMEOUT, TransportError


ODDSPAPI_BASE_URL = "https://api.oddspapi.io/v4"
ODDSPAPI_TENNIS_SPORT_ID = 12
ODDSPAPI_PLAN_BOOKMAKERS = ("pinnacle+30", "1xbet", "unibet", "bet365")
ODDSPAPI_SHARP_BOOKMAKER = "pinnacle+30"
# /v4/odds-by-tournaments accepts exactly one `bookmaker` and at most five
# `tournamentIds` per request.
MAX_ODDSPAPI_TOURNAMENTS_PER_REQUEST = 5


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
    bookmakers: Sequence[str] = (ODDSPAPI_SHARP_BOOKMAKER,),
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = ODDSPAPI_BASE_URL,
    clock: callable | None = None,
    ledger: Any | None = None,
) -> tuple[datetime, list[dict[str, Any]]]:
    if from_at.tzinfo is None or to_at.tzinfo is None:
        raise ValueError("OddsPapi fixture window must be timezone-aware")
    if to_at <= from_at:
        raise ValueError("OddsPapi fixture window must have to_at > from_at")
    if to_at - from_at >= timedelta(days=10):
        raise ValueError("OddsPapi sport fixture window must be under 10 days")
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("OddsPapi API key must be non-empty")
    clock = clock or _utc_now
    params = {
        "apiKey": key,
        "sportId": ODDSPAPI_TENNIS_SPORT_ID,
        "from": _iso_z(from_at),
        "to": _iso_z(to_at),
        "statusId": 0,
        "hasOdds": "true",
        "bookmakers": ",".join(_clean_bookmakers(bookmakers)),
        "language": "en",
    }
    payload = _get_json(
        f"{base_url.rstrip('/')}/fixtures",
        params=params,
        timeout=timeout,
        label="oddspapi fixtures",
        ledger=ledger,
    )
    received_at = clock()
    rows = _fixture_list(payload)
    return received_at, rows


def fetch_oddspapi_tennis_odds_by_tournaments(
    api_key: str,
    *,
    tournament_ids: Sequence[int],
    bookmaker: str = ODDSPAPI_SHARP_BOOKMAKER,
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = ODDSPAPI_BASE_URL,
    clock: callable | None = None,
    ledger: Any | None = None,
) -> tuple[datetime, list[dict[str, Any]]]:
    ids = tuple(dict.fromkeys(int(value) for value in tournament_ids))
    if not ids:
        raise ValueError("OddsPapi tournament_ids must be non-empty")
    if len(ids) > MAX_ODDSPAPI_TOURNAMENTS_PER_REQUEST:
        raise ValueError(
            "OddsPapi odds-by-tournaments accepts at most "
            f"{MAX_ODDSPAPI_TOURNAMENTS_PER_REQUEST} tournament ids per request"
        )
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("OddsPapi API key must be non-empty")
    book = str(bookmaker or "").strip()
    if not book:
        raise ValueError("OddsPapi bookmaker must be non-empty")
    clock = clock or _utc_now
    params = {
        "apiKey": key,
        "tournamentIds": ",".join(str(value) for value in ids),
        "bookmaker": book,
        "language": "en",
        "verbosity": 3,
        "oddsFormat": "decimal",
    }
    payload = _get_json(
        f"{base_url.rstrip('/')}/odds-by-tournaments",
        params=params,
        timeout=timeout,
        label="oddspapi tournament odds",
        ledger=ledger,
        bookmaker=book,
        chunk_size=len(ids),
    )
    received_at = clock()
    return received_at, _fixture_list(payload)


def fetch_oddspapi_tennis_odds_for_books(
    api_key: str,
    *,
    tournament_ids: Sequence[int],
    bookmakers: Sequence[str] = ODDSPAPI_PLAN_BOOKMAKERS,
    chunk_size: int = MAX_ODDSPAPI_TOURNAMENTS_PER_REQUEST,
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = ODDSPAPI_BASE_URL,
    clock: callable | None = None,
    ledger: Any | None = None,
) -> tuple[datetime, list[dict[str, Any]], int]:
    """Fetch odds-by-tournaments for several bookmakers within the endpoint limits.

    Returns ``(received_at, rows, requests_used)``. The caller is responsible for
    pacing between calls; this helper only enforces the per-request limits.
    """
    ids = tuple(dict.fromkeys(int(value) for value in tournament_ids))
    if not ids:
        raise ValueError("OddsPapi tournament_ids must be non-empty")
    size = min(max(1, int(chunk_size)), MAX_ODDSPAPI_TOURNAMENTS_PER_REQUEST)
    rows: list[dict[str, Any]] = []
    requests_used = 0
    received_at: datetime | None = None
    first = True
    for book in _clean_bookmakers(bookmakers):
        for offset in range(0, len(ids), size):
            if not first:
                # OddsPapi enforces a short endpoint cooldown; pace explicitly.
                time.sleep(1.0)
            first = False
            chunk = ids[offset : offset + size]
            received_at, chunk_rows = fetch_oddspapi_tennis_odds_by_tournaments(
                api_key,
                tournament_ids=chunk,
                bookmaker=book,
                timeout=timeout,
                base_url=base_url,
                clock=clock,
                ledger=ledger,
            )
            requests_used += 1
            rows.extend(chunk_rows)
    return received_at or (clock or _utc_now)(), rows, requests_used


def merge_oddspapi_odds_books(
    fixture_rows: Sequence[Mapping[str, Any]],
    odds_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Combine per-book odds rows onto fixture-discovery identity.

    ``odds-by-tournaments`` returns one row per (bookmaker, tournament) batch, so
    the same fixture appears multiple times with different ``bookmakerOdds``.
    Identity stays governed by discovery; book payloads are merged.
    """
    discovery: dict[str, dict[str, Any]] = {}
    for row in fixture_rows:
        fixture_id = str(row.get("fixtureId") or "").strip()
        if fixture_id and fixture_id not in discovery:
            discovery[fixture_id] = dict(row)

    identity_keys = {
        "fixtureId",
        "sportId",
        "tournamentId",
        "participant1Id",
        "participant1Name",
        "participant2Id",
        "participant2Name",
        "startTime",
        "externalProviders",
    }
    merged: dict[str, dict[str, Any]] = {}
    for row in odds_rows:
        fixture_id = str(row.get("fixtureId") or "").strip()
        base = discovery.get(fixture_id)
        if not fixture_id or base is None:
            continue
        target = merged.setdefault(fixture_id, dict(base))
        for key, value in row.items():
            if key == "bookmakerOdds" or key in identity_keys:
                continue
            target[key] = value
        books = row.get("bookmakerOdds")
        if isinstance(books, Mapping):
            existing = target.get("bookmakerOdds")
            if not isinstance(existing, dict):
                existing = {}
                target["bookmakerOdds"] = existing
            for slug, book_payload in books.items():
                existing[str(slug)] = book_payload
    return list(merged.values())


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


_RATE_LIMIT_WAIT_RE = re.compile(r"wait\s+([0-9]*\.?[0-9]+)\s*second", re.IGNORECASE)


def _rate_limit_wait(response: requests.Response) -> float:
    headers = getattr(response, "headers", None)
    retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
    if retry_after:
        try:
            return max(0.0, min(5.0, float(retry_after)))
        except ValueError:
            pass
    try:
        payload = response.json()
    except ValueError:
        payload = None
    details = ""
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            details = str(error.get("details") or "")
        else:
            details = str(error or "")
    match = _RATE_LIMIT_WAIT_RE.search(details)
    if match:
        try:
            return max(0.0, min(5.0, float(match.group(1)) + 0.1))
        except ValueError:
            pass
    return 1.1


def _get_json(
    url: str,
    *,
    params: Mapping[str, Any],
    timeout: float,
    label: str,
    ledger: Any | None = None,
    bookmaker: str | None = None,
    chunk_size: int | None = None,
) -> Any:
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        ledger_id: int | None = None
        if ledger is not None:
            ledger_id = ledger.reserve(
                endpoint=label,
                bookmaker=bookmaker,
                chunk_size=chunk_size,
                retry_sequence=attempt - 1,
            )
        try:
            response = requests.get(url, params=dict(params), timeout=timeout)
        except requests.exceptions.Timeout as exc:
            if ledger_id is not None:
                ledger.record(ledger_id, success=False, error_kind="TIMEOUT", retry_sequence=attempt - 1)
            raise TransportError("TIMEOUT", f"{label} request timed out") from exc
        except requests.exceptions.RequestException as exc:
            if ledger_id is not None:
                ledger.record(ledger_id, success=False, error_kind="NETWORK", retry_sequence=attempt - 1)
            raise TransportError("NETWORK", f"{label} request failed") from exc
        if response.status_code == 200:
            if ledger_id is not None:
                ledger.record(ledger_id, success=True, http_status=200, retry_sequence=attempt - 1)
            try:
                return response.json()
            except ValueError as exc:
                raise TransportError("PARSE", f"{label} response was not valid JSON") from exc
        if attempt < max_attempts and (
            response.status_code == 429 or 500 <= response.status_code < 600
        ):
            # OddsPapi enforces a short per-endpoint cooldown; honour it rather
            # than failing the batch, but stay bounded. Each retry is a billable
            # attempt and is counted separately.
            if ledger_id is not None:
                ledger.record(
                    ledger_id,
                    success=False,
                    http_status=response.status_code,
                    error_kind=f"HTTP_{response.status_code}",
                    retry_sequence=attempt - 1,
                )
            time.sleep(_rate_limit_wait(response) if response.status_code == 429 else 1.0)
            continue
        if ledger_id is not None:
            ledger.record(
                ledger_id,
                success=False,
                http_status=response.status_code,
                error_kind=f"HTTP_{response.status_code}",
                retry_sequence=attempt - 1,
            )
        raise TransportError(
            f"HTTP_{response.status_code}", f"{label} returned a non-200 response"
        )
    raise TransportError("HTTP_RETRY_EXHAUSTED", f"{label} retries exhausted")


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
    "ODDSPAPI_PLAN_BOOKMAKERS",
    "ODDSPAPI_SHARP_BOOKMAKER",
    "ODDSPAPI_TENNIS_SPORT_ID",
    "OddspapiTennisFixtureEnvelope",
    "fetch_oddspapi_tennis_fixtures",
    "fetch_oddspapi_tennis_odds_by_tournaments",
    "merge_oddspapi_fixture_odds",
    "parse_oddspapi_tennis_fixture",
]
