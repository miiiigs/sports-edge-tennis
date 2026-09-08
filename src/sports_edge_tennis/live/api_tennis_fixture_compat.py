"""Compatibility normalization for API-Tennis upcoming fixture status.

API-Tennis may return a blank ``event_status`` for prematch fixtures. The
canonical Source A adapter intentionally rejects blank/unknown statuses, so this
module supplies a narrowly-scoped production compatibility layer: only fixtures
with explicit non-live evidence, no winner/final result, and a future UTC start
are promoted to the existing ``Not Started`` provider status before Source A
parsing. Everything else remains untouched and therefore fails closed in the
frozen adapter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any, Iterable, Mapping

import psycopg

from sports_edge_tennis.data.sources.api_tennis_live import (
    ApiTennisIngestOutcome,
    ingest_api_tennis_source_a_fixtures,
)


_BLANK_FINAL_RESULTS = {"", "-"}
_NON_LIVE_VALUES = {"0", "false", "no"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _future_start_utc(fixture: Mapping[str, Any], received_at: datetime) -> bool:
    event_date = _text(fixture.get("event_date"))
    event_time = _text(fixture.get("event_time"))
    if not event_date or not event_time:
        return False
    try:
        parsed_date = date.fromisoformat(event_date)
        normalized_time = event_time if len(event_time.split(":")) != 2 else f"{event_time}:00"
        parsed_time = time.fromisoformat(normalized_time)
    except ValueError:
        return False
    if parsed_time.tzinfo is not None:
        return False
    if received_at.tzinfo is None:
        return False
    start_at = datetime.combine(parsed_date, parsed_time, tzinfo=UTC)
    return start_at > received_at.astimezone(UTC)


def _is_safe_blank_upcoming_fixture(
    fixture: Mapping[str, Any],
    *,
    received_at: datetime,
) -> bool:
    if _text(fixture.get("event_status")):
        return False

    event_live = _text(fixture.get("event_live")).lower()
    if event_live not in _NON_LIVE_VALUES:
        return False

    if _text(fixture.get("event_winner")):
        return False

    final_result = _text(fixture.get("event_final_result"))
    if final_result not in _BLANK_FINAL_RESULTS:
        return False

    return _future_start_utc(fixture, received_at)


def normalize_api_tennis_upcoming_status(
    fixtures: Iterable[Mapping[str, Any]],
    *,
    received_at: datetime,
) -> list[dict[str, Any]]:
    """Promote only safely identified blank-status future fixtures to scheduled."""
    normalized: list[dict[str, Any]] = []
    for fixture in fixtures:
        row = dict(fixture)
        if _is_safe_blank_upcoming_fixture(row, received_at=received_at):
            row["event_status"] = "Not Started"
        normalized.append(row)
    return normalized


def ingest_api_tennis_source_a_fixtures_with_upcoming_status(
    conn: psycopg.Connection,
    fixtures: list[dict[str, Any]],
    received_at: datetime,
) -> ApiTennisIngestOutcome:
    normalized = normalize_api_tennis_upcoming_status(fixtures, received_at=received_at)
    return ingest_api_tennis_source_a_fixtures(
        conn,
        normalized,
        received_at=received_at,
    )
