"""Compatibility normalization for API-Tennis live fixture ingestion.

The frozen Source A adapter intentionally remains strict. This module supplies
only two narrowly-scoped production compatibility steps before handing the
payload to Source A:

1. Blank ``event_status`` values are promoted to ``Not Started`` only when the
   fixture is explicitly non-live, has no winner/final result, and starts in
   the future in UTC.
2. A previously unseen API-Tennis player key is linked to an existing canonical
   Tennis player only when the API-Tennis display name resolves to exactly one
   existing ``TENNIS_DATA`` normalized alias. Ambiguous or unmatched names are
   left untouched, so Source A creates/handles them with its existing strict
   behavior rather than guessing identity.

The second step is required so live fixtures reuse the historical player IDs
that Agents 1-3 were trained/evaluated against instead of silently creating a
fresh player row with no historical evidence.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any, Iterable, Mapping

import psycopg

from sports_edge_tennis.data.normalize import normalize_person_lookup
from sports_edge_tennis.data.sources.api_tennis_live import (
    SOURCE_SYSTEM,
    ApiTennisIngestOutcome,
    ingest_api_tennis_source_a_fixtures,
)


_BLANK_FINAL_RESULTS = {"", "-"}
_NON_LIVE_VALUES = {"0", "false", "no"}
_PLAYER_FIELDS = (
    ("first_player_key", ("event_first_player", "first_player", "first_player_name")),
    ("second_player_key", ("event_second_player", "second_player", "second_player_name")),
)


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


def _first_text(fixture: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = _text(fixture.get(field))
        if value:
            return value
    return ""


def _prelink_api_tennis_players_to_history(
    conn: psycopg.Connection,
    fixtures: Iterable[Mapping[str, Any]],
) -> int:
    """Attach unseen API-Tennis keys to one unambiguous historical player.

    ``player_source_ids`` is append-only, so existing API-Tennis mappings are
    never rewritten here. That is deliberate: a production database that
    already contains incorrect legacy mappings requires an explicit cleanup or
    restore rather than an in-place identity rewrite.
    """
    identities: dict[str, str] = {}
    conflicts: set[str] = set()
    for fixture in fixtures:
        for key_field, name_fields in _PLAYER_FIELDS:
            source_player_id = _text(fixture.get(key_field))
            player_name = _first_text(fixture, name_fields)
            if not source_player_id or not player_name:
                continue
            previous = identities.get(source_player_id)
            if previous is not None and normalize_person_lookup(previous) != normalize_person_lookup(player_name):
                conflicts.add(source_player_id)
                continue
            identities[source_player_id] = player_name

    linked = 0
    for source_player_id, player_name in identities.items():
        if source_player_id in conflicts:
            continue
        existing = conn.execute(
            """
            SELECT player_id
            FROM player_source_ids
            WHERE source_system = %s AND source_player_id = %s
            """,
            (SOURCE_SYSTEM, source_player_id),
        ).fetchone()
        if existing is not None:
            continue

        normalized_name = normalize_person_lookup(player_name)
        candidates = conn.execute(
            """
            SELECT DISTINCT player_id
            FROM player_aliases
            WHERE source_system = 'TENNIS_DATA'
              AND normalized_alias = %s
            ORDER BY player_id
            """,
            (normalized_name,),
        ).fetchall()
        if len(candidates) != 1:
            continue

        cursor = conn.execute(
            """
            INSERT INTO player_source_ids (
                player_id,
                source_system,
                source_player_id
            )
            VALUES (%s, %s, %s)
            ON CONFLICT (source_system, source_player_id) DO NOTHING
            RETURNING player_source_id
            """,
            (candidates[0][0], SOURCE_SYSTEM, source_player_id),
        )
        if cursor.fetchone() is not None:
            linked += 1
    return linked


def ingest_api_tennis_source_a_fixtures_with_upcoming_status(
    conn: psycopg.Connection,
    fixtures: list[dict[str, Any]],
    received_at: datetime,
) -> ApiTennisIngestOutcome:
    _prelink_api_tennis_players_to_history(conn, fixtures)
    normalized = normalize_api_tennis_upcoming_status(fixtures, received_at=received_at)
    return ingest_api_tennis_source_a_fixtures(
        conn,
        normalized,
        received_at=received_at,
    )
