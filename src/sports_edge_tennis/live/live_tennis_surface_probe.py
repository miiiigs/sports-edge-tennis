"""Read-only Live Tennis API surface benchmark for unresolved production targets.

The provider exposes upcoming fixtures with an explicit surface field and documents
team-tie fixtures (Davis Cup/BJK Cup/United Cup) as first-class rows. This module is
observational only: it never persists provider IDs, surfaces, or identity mappings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Callable

import psycopg
import requests

from sports_edge_tennis.live.sportradar_benchmark import (
    CanonicalGapTarget,
    deterministic_name_keys,
    load_latest_gap_targets,
)

BASE_URL = "https://api.livetennisapi.com/api/public/v1"
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_PAGES = 2
MAX_PAGES = 5
DEFAULT_MAX_START_DELTA_MINUTES = 180
DEFAULT_TIMEOUT_SECONDS = 10.0


class LiveTennisApiError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class LiveTennisFixtureCandidate:
    fixture_id: int
    tournament: str
    player_1_name: str
    player_2_name: str
    start_at: datetime
    raw_surface: str | None
    canonical_surface: str | None
    start_delta_minutes: int


@dataclass(frozen=True)
class LiveTennisSurfaceLink:
    status: str
    candidates: tuple[LiveTennisFixtureCandidate, ...]


def normalize_surface(value: Any) -> str | None:
    raw = str(value or "").strip().casefold()
    if raw == "hard":
        return "HARD"
    if raw == "clay":
        return "CLAY"
    if raw == "grass":
        return "GRASS"
    if raw == "carpet":
        return "CARPET"
    return None


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _fixture_start(row: Mapping[str, Any]) -> datetime | None:
    raw_start = str(row.get("start_time") or "").strip()
    raw_date = str(row.get("event_date") or "").strip()
    if not raw_start:
        return None

    candidate = raw_start
    if "T" not in raw_start:
        if not raw_date:
            return None
        candidate = f"{raw_date}T{raw_start}"

    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _name_match(left: str, right: str) -> bool:
    return bool(deterministic_name_keys(left) & deterministic_name_keys(right))


def _pair_match(target: CanonicalGapTarget, row: Mapping[str, Any]) -> bool:
    first = str(row.get("player1_name") or "").strip()
    second = str(row.get("player2_name") or "").strip()
    if not first or not second:
        return False
    same_order = _name_match(target.player_1_name, first) and _name_match(
        target.player_2_name, second
    )
    reverse_order = _name_match(target.player_1_name, second) and _name_match(
        target.player_2_name, first
    )
    return same_order or reverse_order


def link_surface_target(
    target: CanonicalGapTarget,
    fixtures: Sequence[Mapping[str, Any]],
    *,
    max_start_delta_minutes: int = DEFAULT_MAX_START_DELTA_MINUTES,
) -> LiveTennisSurfaceLink:
    if max_start_delta_minutes < 0:
        raise ValueError("max_start_delta_minutes must be non-negative")

    pair_matches: list[LiveTennisFixtureCandidate] = []
    in_window: list[LiveTennisFixtureCandidate] = []
    for row in fixtures:
        if not _pair_match(target, row):
            continue
        start_at = _fixture_start(row)
        if start_at is None:
            continue
        delta = int(abs((start_at - target.match_start_at).total_seconds()) // 60)
        raw_surface = str(row.get("surface") or "").strip() or None
        candidate = LiveTennisFixtureCandidate(
            fixture_id=int(row.get("id")),
            tournament=str(row.get("tournament") or "").strip(),
            player_1_name=str(row.get("player1_name") or "").strip(),
            player_2_name=str(row.get("player2_name") or "").strip(),
            start_at=start_at,
            raw_surface=raw_surface,
            canonical_surface=normalize_surface(raw_surface),
            start_delta_minutes=delta,
        )
        pair_matches.append(candidate)
        if delta <= max_start_delta_minutes:
            in_window.append(candidate)

    if not pair_matches:
        return LiveTennisSurfaceLink("UNRESOLVED", ())
    if not in_window:
        return LiveTennisSurfaceLink("TIME_MISMATCH", tuple(pair_matches))
    if len(in_window) != 1:
        return LiveTennisSurfaceLink("AMBIGUOUS", tuple(in_window))

    candidate = in_window[0]
    if candidate.canonical_surface is None:
        return LiveTennisSurfaceLink("NULL_SURFACE", (candidate,))
    return LiveTennisSurfaceLink("EXACT_SURFACE", (candidate,))


def fetch_fixture_page(
    api_key: str,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    get: Callable[..., Any] = requests.get,
) -> tuple[datetime, dict[str, Any]]:
    if not api_key.strip():
        raise ValueError("Live Tennis API key must be non-empty")
    if not 1 <= limit <= 100:
        raise ValueError("fixture page limit must be between 1 and 100")
    if offset < 0:
        raise ValueError("fixture page offset must be non-negative")

    try:
        response = get(
            f"{BASE_URL}/fixtures",
            params={"limit": limit, "offset": offset},
            headers={"X-API-Key": api_key},
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        raise LiveTennisApiError("TRANSPORT", "Live Tennis API transport failed") from exc

    if response.status_code == 429:
        raise LiveTennisApiError("HTTP_429", "Live Tennis API rate limited")
    if response.status_code in {401, 403}:
        raise LiveTennisApiError("AUTH", "Live Tennis API authorization failed")
    if response.status_code != 200:
        raise LiveTennisApiError(
            f"HTTP_{response.status_code}",
            f"Live Tennis API returned HTTP {response.status_code}",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise LiveTennisApiError("INVALID_JSON", "Live Tennis API returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise LiveTennisApiError("INVALID_PAYLOAD", "Live Tennis API payload was not an object")
    return datetime.now(UTC), dict(payload)


def fetch_fixture_pages(
    api_key: str,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_size: int = DEFAULT_PAGE_SIZE,
    fetch_page: Callable[..., tuple[datetime, dict[str, Any]]] = fetch_fixture_page,
) -> tuple[datetime, tuple[Mapping[str, Any], ...], int]:
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")

    latest_received = datetime.now(UTC)
    fixtures: list[Mapping[str, Any]] = []
    calls = 0
    for page in range(max_pages):
        received_at, payload = fetch_page(
            api_key,
            limit=page_size,
            offset=page * page_size,
        )
        latest_received = max(latest_received, received_at)
        calls += 1
        page_rows = _rows(payload.get("data"))
        fixtures.extend(page_rows)
        if len(page_rows) < page_size:
            break
    return latest_received, tuple(fixtures), calls


def run_probe(
    conn: psycopg.Connection,
    *,
    api_key: str,
    target_date: date,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_size: int = DEFAULT_PAGE_SIZE,
    fetch_pages: Callable[..., tuple[datetime, tuple[Mapping[str, Any], ...], int]] = fetch_fixture_pages,
) -> int:
    prediction_run_id, run_as_of, targets = load_latest_gap_targets(
        conn,
        target_date=target_date,
    )
    surface_targets = tuple(
        target for target in targets if target.surface_status == "UNRESOLVED"
    )
    if prediction_run_id is None or run_as_of is None:
        print(
            f"LIVE_TENNIS_SURFACE_PROBE_BLOCKED reason=NO_COMPLETED_RUN date={target_date.isoformat()} writes=0",
            flush=True,
        )
        return 0

    received_at, fixtures, calls = fetch_pages(
        api_key,
        max_pages=max_pages,
        page_size=page_size,
    )
    print(
        "LIVE_TENNIS_SURFACE_PROBE_START "
        f"prediction_run_id={prediction_run_id} targets={len(surface_targets)} "
        f"fixtures={len(fixtures)} requests={calls} received_at={received_at.isoformat()} writes=0",
        flush=True,
    )

    counts: dict[str, int] = {}
    for target in surface_targets:
        result = link_surface_target(target, fixtures)
        counts[result.status] = counts.get(result.status, 0) + 1
        candidate = result.candidates[0] if len(result.candidates) == 1 else None
        print(
            "LIVE_TENNIS_SURFACE_LINK "
            f"match_id={target.match_id} status={result.status} "
            f"fixture_id={candidate.fixture_id if candidate else '-'} "
            f"tournament={candidate.tournament!r if candidate else '-'} "
            f"surface={candidate.canonical_surface if candidate and candidate.canonical_surface else '-'} "
            f"raw_surface={candidate.raw_surface if candidate and candidate.raw_surface else '-'} "
            f"start_delta_min={candidate.start_delta_minutes if candidate else '-'} "
            "mapping_changed=0",
            flush=True,
        )

    counts_text = " ".join(
        f"{status.lower()}={count}" for status, count in sorted(counts.items())
    ) or "none=0"
    print(
        "LIVE_TENNIS_SURFACE_PROBE_SUMMARY "
        f"prediction_run_id={prediction_run_id} targets={len(surface_targets)} "
        f"fixtures={len(fixtures)} requests={calls} {counts_text} writes=0",
        flush=True,
    )
    return 0
