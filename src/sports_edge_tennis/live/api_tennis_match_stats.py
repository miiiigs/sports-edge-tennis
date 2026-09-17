"""API-Tennis completed-match statistics bridge for Agent 2 history.

API-Tennis returns per-player match statistics inline with ``get_fixtures``. This
module converts only reproducible match-period serve-point rows into the canonical
append-only ``match_stat_observations`` ledger. It never backdates visibility: both
``source_observed_at`` and ``as_of`` are the local receive timestamp.

The parser fails closed for Agent 2's core point history. A player pair is persisted
only when both players expose first-serve and second-serve point totals, allowing
service points played and return points won to be derived without inventing values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

import psycopg

from sports_edge_tennis.data.contracts import MatchStatLine
from sports_edge_tennis.data.hashing import source_hash
from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.data.sources.api_tennis_live import SOURCE_SYSTEM, ApiTennisIngestedMatch


STAT_DATASET = "api_tennis_completed_match_stats"
STAT_ADAPTER_VERSION = "API_TENNIS_MATCH_STATS_V1"


@dataclass(frozen=True)
class ParsedPlayerPointStats:
    player_key: str
    first_serve_points_won: int
    first_serve_points_total: int
    second_serve_points_won: int
    second_serve_points_total: int
    aces: int | None = None
    double_faults: int | None = None
    break_points_saved: int | None = None
    break_points_faced: int | None = None

    @property
    def service_points_played(self) -> int:
        return self.first_serve_points_total + self.second_serve_points_total

    @property
    def service_points_won(self) -> int:
        return self.first_serve_points_won + self.second_serve_points_won


@dataclass(frozen=True)
class ParsedMatchPointStats:
    first: ParsedPlayerPointStats
    second: ParsedPlayerPointStats


@dataclass(frozen=True)
class StatsPersistOutcome:
    eligible_matches: int
    inserted_rows: int
    existing_rows: int
    skipped_matches: int


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and text != "-" else None


def _name(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("_", " ").split())


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _stat_rows(fixture: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = fixture.get("statistics")
    if not isinstance(raw, list):
        return []
    return [
        row
        for row in raw
        if isinstance(row, Mapping) and _name(row.get("stat_period")) == "match"
    ]


def _parse_player(
    rows: Iterable[Mapping[str, Any]], player_key: str
) -> ParsedPlayerPointStats | None:
    first_won = first_total = second_won = second_total = None
    aces = double_faults = break_points_saved = break_points_faced = None

    for row in rows:
        if _text(row.get("player_key")) != player_key:
            continue
        stat_name = _name(row.get("stat_name"))
        if stat_name == "1st serve points won":
            first_won = _int(row.get("stat_won"))
            first_total = _int(row.get("stat_total"))
        elif stat_name == "2nd serve points won":
            second_won = _int(row.get("stat_won"))
            second_total = _int(row.get("stat_total"))
        elif stat_name == "aces":
            aces = _int(row.get("stat_value"))
        elif stat_name == "double faults":
            double_faults = _int(row.get("stat_value"))
        elif stat_name in {"break points saved", "break point saved"}:
            break_points_saved = _int(row.get("stat_won"))
            break_points_faced = _int(row.get("stat_total"))

    required = (first_won, first_total, second_won, second_total)
    if any(value is None for value in required):
        return None
    assert first_won is not None and first_total is not None
    assert second_won is not None and second_total is not None
    if first_won > first_total or second_won > second_total:
        return None
    if first_total + second_total <= 0:
        return None

    return ParsedPlayerPointStats(
        player_key=player_key,
        first_serve_points_won=first_won,
        first_serve_points_total=first_total,
        second_serve_points_won=second_won,
        second_serve_points_total=second_total,
        aces=aces,
        double_faults=double_faults,
        break_points_saved=break_points_saved,
        break_points_faced=break_points_faced,
    )


def parse_api_tennis_match_point_stats(
    fixture: Mapping[str, Any],
) -> ParsedMatchPointStats | None:
    first_key = _text(fixture.get("first_player_key"))
    second_key = _text(fixture.get("second_player_key"))
    if first_key is None or second_key is None or first_key == second_key:
        return None
    rows = _stat_rows(fixture)
    if not rows:
        return None
    first = _parse_player(rows, first_key)
    second = _parse_player(rows, second_key)
    if first is None or second is None:
        return None
    return ParsedMatchPointStats(first=first, second=second)


def _line(player: ParsedPlayerPointStats, opponent: ParsedPlayerPointStats) -> MatchStatLine:
    return_points_won = opponent.service_points_played - opponent.service_points_won
    if return_points_won < 0:
        raise ValueError("derived return points won cannot be negative")
    return MatchStatLine(
        first_serve_points_won=player.first_serve_points_won,
        second_serve_points_won=player.second_serve_points_won,
        service_points_played=player.service_points_played,
        return_points_won=return_points_won,
        aces=player.aces,
        double_faults=player.double_faults,
        break_points_saved=player.break_points_saved,
        break_points_faced=player.break_points_faced,
    )


def _fixture_map(fixtures: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    mapped: dict[str, Mapping[str, Any]] = {}
    for fixture in fixtures:
        event_key = _text(fixture.get("event_key"))
        if event_key is not None:
            mapped[event_key] = fixture
    return mapped


def _start_ingest_run(conn: psycopg.Connection, received_at: datetime) -> int:
    return int(
        conn.execute(
            """
            INSERT INTO ingest_runs (
                source_system, source_dataset, started_at, status, source_version
            ) VALUES (%s, %s, %s, 'RUNNING', %s)
            RETURNING ingest_run_id
            """,
            (SOURCE_SYSTEM, STAT_DATASET, received_at, STAT_ADAPTER_VERSION),
        ).fetchone()[0]
    )


def _existing_semantic_stat(
    conn: psycopg.Connection,
    *,
    match_id: int,
    player_id: int,
    line: MatchStatLine,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM match_stat_observations
        WHERE match_id = %s
          AND player_id = %s
          AND source_system = %s
          AND observation_kind = 'RAW_SOURCE'
          AND first_serve_points_won IS NOT DISTINCT FROM %s
          AND second_serve_points_won IS NOT DISTINCT FROM %s
          AND service_points_played IS NOT DISTINCT FROM %s
          AND return_points_won IS NOT DISTINCT FROM %s
          AND aces IS NOT DISTINCT FROM %s
          AND double_faults IS NOT DISTINCT FROM %s
          AND break_points_saved IS NOT DISTINCT FROM %s
          AND break_points_faced IS NOT DISTINCT FROM %s
        LIMIT 1
        """,
        (
            match_id,
            player_id,
            SOURCE_SYSTEM,
            line.first_serve_points_won,
            line.second_serve_points_won,
            line.service_points_played,
            line.return_points_won,
            line.aces,
            line.double_faults,
            line.break_points_saved,
            line.break_points_faced,
        ),
    ).fetchone()
    return row is not None


def persist_api_tennis_match_stats(
    conn: psycopg.Connection,
    *,
    fixtures: Iterable[Mapping[str, Any]],
    ingested_matches: tuple[ApiTennisIngestedMatch, ...],
    received_at: datetime,
) -> StatsPersistOutcome:
    by_event = _fixture_map(fixtures)
    eligible: list[tuple[ApiTennisIngestedMatch, ParsedMatchPointStats]] = []
    skipped = 0
    for ingested in ingested_matches:
        fixture = by_event.get(ingested.source_match_id)
        if fixture is None:
            skipped += 1
            continue
        parsed = parse_api_tennis_match_point_stats(fixture)
        if parsed is None:
            skipped += 1
            continue
        eligible.append((ingested, parsed))

    if not eligible:
        return StatsPersistOutcome(0, 0, 0, skipped)

    ingest_run_id = _start_ingest_run(conn, received_at)
    repository = HistoricalRepository(conn, ingest_run_id)
    inserted = 0
    existing = 0
    for ingested, parsed in eligible:
        pairs = (
            (ingested.player_1_id, parsed.first, parsed.second),
            (ingested.player_2_id, parsed.second, parsed.first),
        )
        for player_id, player_stats, opponent_stats in pairs:
            line = _line(player_stats, opponent_stats)
            if _existing_semantic_stat(
                conn,
                match_id=ingested.match_id,
                player_id=player_id,
                line=line,
            ):
                existing += 1
                continue
            event_key = ingested.source_match_id
            normalized_payload = {
                "event_key": event_key,
                "player_key": player_stats.player_key,
                "first_serve_points_won": line.first_serve_points_won,
                "second_serve_points_won": line.second_serve_points_won,
                "service_points_played": line.service_points_played,
                "return_points_won": line.return_points_won,
                "aces": line.aces,
                "double_faults": line.double_faults,
                "break_points_saved": line.break_points_saved,
                "break_points_faced": line.break_points_faced,
            }
            record_hash = source_hash(SOURCE_SYSTEM, normalized_payload)
            did_insert = repository.insert_match_stat(
                match_id=ingested.match_id,
                player_id=player_id,
                source_system=SOURCE_SYSTEM,
                source_record_id=f"{event_key}:{player_stats.player_key}:match_stats",
                source_record_hash=record_hash,
                line=line,
                source_observed_at=received_at,
                as_of=received_at,
            )
            inserted += int(did_insert)
            existing += int(not did_insert)

    conn.execute(
        """
        UPDATE ingest_runs
        SET finished_at = %s,
            status = 'COMPLETED',
            rows_seen = %s,
            rows_inserted = %s,
            rows_rejected = 0,
            error_summary = '[]'::jsonb
        WHERE ingest_run_id = %s
        """,
        (received_at, len(eligible) * 2, inserted, ingest_run_id),
    )
    return StatsPersistOutcome(len(eligible), inserted, existing, skipped)


__all__ = [
    "ParsedMatchPointStats",
    "ParsedPlayerPointStats",
    "StatsPersistOutcome",
    "parse_api_tennis_match_point_stats",
    "persist_api_tennis_match_stats",
]
