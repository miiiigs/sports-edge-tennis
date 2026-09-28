from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Iterable, Mapping

import psycopg

from ..cross_provider_reconciliation import CrossProviderIdentityAmbiguous
from ..hashing import source_hash
from ..normalize import collapse_whitespace
from ..repository import HistoricalRepository, MatchLocator

SOURCE_SYSTEM = "API_TENNIS"
SOURCE_DATASET = "api_tennis_scheduled_fixtures"
ADAPTER_VERSION = "API_TENNIS_SOURCE_A_V1"

_SUPPORTED_COMPETITION_TIERS = {
    "GRAND_SLAM",
    "MASTERS_1000",
    "ATP_TOUR",
    "ATP_500",
    "ATP_250",
    "CHALLENGER",
}
_SURFACE_MAP = {
    "hard": "HARD",
    "clay": "CLAY",
    "grass": "GRASS",
    "carpet": "CARPET",
}
_STATUS_MAP = {
    "not started": "SCHEDULED",
    "notstarted": "SCHEDULED",
    "scheduled": "SCHEDULED",
    "upcoming": "SCHEDULED",
    "postponed": "POSTPONED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "walkover": "WALKOVER",
    "retired": "RETIRED",
    "finished": "COMPLETED",
    "completed": "COMPLETED",
}
_ATP_MAIN_TYPE_TOKENS = {
    "atp singles",
    "atp main singles",
    "atp men singles",
    "atp mens singles",
    "atp men's singles",
}
_CHALLENGER_TYPE_TOKENS = {
    "atp challenger singles",
    "atp challenger men singles",
    "atp challenger mens singles",
    "atp challenger men's singles",
    "challenger singles",
    "challenger men singles",
    "challenger mens singles",
    "challenger men's singles",
}


class ApiTennisSourceAError(ValueError):
    """Raised when an API-Tennis scheduled fixture cannot be safely normalized."""


@dataclass(frozen=True)
class ApiTennisFixtureRecord:
    source_match_id: str
    tournament_source_id: str
    first_player_source_id: str
    second_player_source_id: str
    first_player_name: str
    second_player_name: str
    tournament_name: str
    event_type_type: str
    match_start_at: datetime
    match_status: str
    tour_scope: str
    competition_tier: str
    season_year: int
    edition_name: str
    tournament_start_date: date
    source_observed_at: datetime
    as_of: datetime
    source_record_hash: str
    source_version_hash: str
    source_record_locator: str
    raw_fields: dict[str, Any]
    surface: str | None = None
    round_code: str | None = None
    best_of_sets: int = 3
    qualifying_flag: bool = False


@dataclass(frozen=True)
class ApiTennisIngestedMatch:
    source_match_id: str
    match_id: int
    match_version_id: int
    player_1_id: int
    player_2_id: int
    match_status: str


@dataclass(frozen=True)
class ApiTennisIngestOutcome:
    rows_seen: int
    rows_ingested: int
    rows_rejected: int
    status: str
    matches: tuple[ApiTennisIngestedMatch, ...] = ()
    rejected_rows: tuple[dict[str, object], ...] = ()


def parse_api_tennis_fixtures(
    fixtures: Iterable[Mapping[str, Any]],
    *,
    received_at: datetime,
) -> tuple[list[ApiTennisFixtureRecord], list[dict[str, object]]]:
    records: list[ApiTennisFixtureRecord] = []
    rejected_rows: list[dict[str, object]] = []
    observed_received_at = _aware_utc(received_at, field_name="received_at")
    for row_number, fixture in enumerate(fixtures, start=1):
        try:
            records.append(_parse_fixture(fixture, row_number=row_number, received_at=observed_received_at))
        except ApiTennisSourceAError as exc:
            rejected_rows.append(
                {
                    "row_number": row_number,
                    "event_key": _safe_str(fixture.get("event_key")),
                    "reason": str(exc),
                }
            )
    return records, rejected_rows


def ingest_api_tennis_source_a_fixtures(
    conn: psycopg.Connection,
    fixtures: Iterable[Mapping[str, Any]],
    *,
    received_at: datetime,
    dry_run: bool = False,
) -> ApiTennisIngestOutcome:
    fixture_list = list(fixtures)
    if not fixture_list:
        return ApiTennisIngestOutcome(
            rows_seen=0,
            rows_ingested=0,
            rows_rejected=0,
            status="COMPLETED",
        )
    records, rejected_rows = parse_api_tennis_fixtures(fixture_list, received_at=received_at)
    if dry_run:
        status = _ingest_status(success_count=len(records), rejected_count=len(rejected_rows))
        return ApiTennisIngestOutcome(
            rows_seen=len(fixture_list),
            rows_ingested=0,
            rows_rejected=len(rejected_rows),
            status=status,
            rejected_rows=tuple(rejected_rows),
        )

    ingest_run_id = _start_ingest_run(conn, received_at=_aware_utc(received_at, field_name="received_at"))
    repository = HistoricalRepository(conn, ingest_run_id)
    ingested: list[ApiTennisIngestedMatch] = []
    try:
        for record in records:
            # Serialize identity resolution for this provider event across workers.
            # The source-id unique index stays authoritative; this only prevents two
            # workers from both deciding to create a canonical match for one event.
            lock_key = _event_identity_lock_key(record.source_match_id)
            conn.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
            try:
                _ensure_existing_event_identity(conn, record)
                locator = _persist_fixture(repository, record)
            except (ApiTennisSourceAError, CrossProviderIdentityAmbiguous) as exc:
                rejected_rows.append(
                    {
                        "event_key": record.source_match_id,
                        "reason": str(exc),
                    }
                )
                continue
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
            ingested.append(
                ApiTennisIngestedMatch(
                    source_match_id=record.source_match_id,
                    match_id=locator.match_id,
                    match_version_id=locator.match_version_id,
                    player_1_id=locator.player_1_id,
                    player_2_id=locator.player_2_id,
                    match_status=record.match_status,
                )
            )
        status = _ingest_status(success_count=len(ingested), rejected_count=len(rejected_rows))
        _finish_ingest_run(
            conn,
            ingest_run_id=ingest_run_id,
            status=status,
            rows_seen=len(fixture_list),
            rows_inserted=len(ingested),
            rows_rejected=len(rejected_rows),
            error_summary=rejected_rows,
        )
    except Exception:
        _finish_ingest_run(
            conn,
            ingest_run_id=ingest_run_id,
            status="FAILED",
            rows_seen=len(fixture_list),
            rows_inserted=len(ingested),
            rows_rejected=len(rejected_rows),
            error_summary=rejected_rows,
        )
        raise

    return ApiTennisIngestOutcome(
        rows_seen=len(fixture_list),
        rows_ingested=len(ingested),
        rows_rejected=len(rejected_rows),
        status=status,
        matches=tuple(ingested),
        rejected_rows=tuple(rejected_rows),
    )


def _ingest_status(*, success_count: int, rejected_count: int) -> str:
    if rejected_count == 0:
        return "COMPLETED"
    if success_count == 0:
        return "FAILED"
    return "PARTIAL"


def _event_identity_lock_key(source_match_id: str) -> int:
    """Signed 64-bit advisory-lock key for one provider event identity."""
    digest = zlib.crc32(f"{SOURCE_SYSTEM}:{source_match_id}".encode()) & 0xFFFFFFFF
    return digest - 0x100000000 if digest >= 0x80000000 else digest


def _parse_fixture(
    fixture: Mapping[str, Any],
    *,
    row_number: int,
    received_at: datetime,
) -> ApiTennisFixtureRecord:
    source_match_id = _required_text(fixture, "event_key")
    tournament_source_id = _required_text(fixture, "tournament_key")
    first_player_source_id = _required_text(fixture, "first_player_key")
    second_player_source_id = _required_text(fixture, "second_player_key")
    if first_player_source_id == second_player_source_id:
        raise ApiTennisSourceAError("duplicate_player_source_id")

    first_player_name = _required_text_any(fixture, ("event_first_player", "first_player", "first_player_name"))
    second_player_name = _required_text_any(
        fixture, ("event_second_player", "second_player", "second_player_name")
    )
    tournament_name = _required_text_any(fixture, ("tournament_name", "event_tournament_name"))
    event_type_type = _required_text(fixture, "event_type_type")
    tour_scope, competition_tier = _classify_event_type(event_type_type, fixture)
    match_status = _match_status(_required_text(fixture, "event_status"))
    match_start_at = _match_start_at(
        event_date=_required_text(fixture, "event_date"),
        event_time=_required_text(fixture, "event_time"),
    )
    source_observed_at = _source_observed_at(fixture, received_at)
    surface = _surface(_optional_text_any(fixture, ("surface", "event_surface")))
    round_code = _optional_text_any(fixture, ("round", "event_round", "round_code"))
    best_of_sets = _best_of_sets(fixture.get("best_of_sets") or fixture.get("event_best_of_sets"))
    qualifying_flag = _qualifying_flag(fixture)
    raw_fields = dict(fixture)
    hash_fields = {
        "event_key": source_match_id,
        "tournament_key": tournament_source_id,
        "first_player_key": first_player_source_id,
        "second_player_key": second_player_source_id,
        "first_player_name": first_player_name,
        "second_player_name": second_player_name,
        "tournament_name": tournament_name,
        "event_type_type": event_type_type,
        "match_start_at": match_start_at,
        "match_status": match_status,
        "surface": surface,
        "round_code": round_code,
        "best_of_sets": best_of_sets,
        "qualifying_flag": qualifying_flag,
    }
    record_hash = source_hash(SOURCE_SYSTEM, hash_fields)
    return ApiTennisFixtureRecord(
        source_match_id=source_match_id,
        tournament_source_id=tournament_source_id,
        first_player_source_id=first_player_source_id,
        second_player_source_id=second_player_source_id,
        first_player_name=first_player_name,
        second_player_name=second_player_name,
        tournament_name=tournament_name,
        event_type_type=event_type_type,
        match_start_at=match_start_at,
        match_status=match_status,
        tour_scope=tour_scope,
        competition_tier=competition_tier,
        season_year=match_start_at.year,
        edition_name=f"{tournament_name} {match_start_at.year}",
        tournament_start_date=match_start_at.date(),
        source_observed_at=source_observed_at,
        as_of=source_observed_at,
        source_record_hash=record_hash,
        source_version_hash=record_hash,
        source_record_locator=f"api_tennis:event_key:{source_match_id}:row:{row_number}",
        raw_fields=raw_fields,
        surface=surface,
        round_code=round_code,
        best_of_sets=best_of_sets,
        qualifying_flag=qualifying_flag,
    )


def _persist_fixture(
    repository: HistoricalRepository,
    record: ApiTennisFixtureRecord,
) -> MatchLocator:
    player_1_id = repository.create_player_with_source_id(
        source_system=SOURCE_SYSTEM,
        source_player_id=record.first_player_source_id,
        canonical_name=record.first_player_name,
        observed_at=record.source_observed_at,
    )
    player_2_id = repository.create_player_with_source_id(
        source_system=SOURCE_SYSTEM,
        source_player_id=record.second_player_source_id,
        canonical_name=record.second_player_name,
        observed_at=record.source_observed_at,
    )
    tournament_id = repository.get_or_create_tournament(
        source_system=SOURCE_SYSTEM,
        source_tournament_id=record.tournament_source_id,
        canonical_name=record.tournament_name,
        tour_scope=record.tour_scope,
        competition_tier=record.competition_tier,
    )
    edition_id = repository.get_or_create_tournament_edition(
        tournament_id=tournament_id,
        season_year=record.season_year,
        edition_name=record.edition_name,
        surface=record.surface,
        start_date=record.tournament_start_date,
        end_date=None,
    )
    return repository.upsert_match_version(
        tournament_edition_id=edition_id,
        source_system=SOURCE_SYSTEM,
        source_match_id=record.source_match_id,
        source_record_hash=record.source_record_hash,
        source_record_locator=record.source_record_locator,
        source_file_name=SOURCE_DATASET,
        source_version_hash=record.source_version_hash,
        player_1_id=player_1_id,
        player_2_id=player_2_id,
        match_start_at=record.match_start_at,
        tour_scope=record.tour_scope,
        competition_tier=record.competition_tier,
        surface=record.surface,
        round_code=record.round_code,
        best_of_sets=record.best_of_sets,
        qualifying_flag=record.qualifying_flag,
        match_status=record.match_status,
        source_observed_at=record.source_observed_at,
        as_of=record.as_of,
    )


def _ensure_existing_event_identity(
    conn: psycopg.Connection,
    record: ApiTennisFixtureRecord,
) -> None:
    existing = conn.execute(
        """
        SELECT
            msi.match_id,
            latest.player_1_id,
            latest.player_2_id,
            te.tournament_id
        FROM match_source_ids msi
        JOIN matches m ON m.match_id = msi.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN LATERAL (
            SELECT player_1_id, player_2_id
            FROM match_versions mv
            WHERE mv.match_id = msi.match_id
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
            LIMIT 1
        ) latest ON TRUE
        WHERE msi.source_system = %s
          AND msi.source_match_id = %s
        ORDER BY msi.match_source_id DESC
        LIMIT 1
        """,
        (SOURCE_SYSTEM, record.source_match_id),
    ).fetchone()
    if existing is None:
        return

    _match_id, existing_player_1_id, existing_player_2_id, existing_tournament_id = existing
    player_1_id = _existing_api_tennis_player_id(conn, record.first_player_source_id)
    player_2_id = _existing_api_tennis_player_id(conn, record.second_player_source_id)
    tournament_id = _existing_api_tennis_tournament_id(conn, record.tournament_source_id)

    if player_1_id != existing_player_1_id:
        raise ApiTennisSourceAError("event_identity_conflict:player_1")
    if player_2_id != existing_player_2_id:
        raise ApiTennisSourceAError("event_identity_conflict:player_2")
    if tournament_id != existing_tournament_id:
        raise ApiTennisSourceAError("event_identity_conflict:tournament")


def _existing_api_tennis_player_id(conn: psycopg.Connection, source_player_id: str) -> int | None:
    row = conn.execute(
        """
        SELECT player_id
        FROM player_source_ids
        WHERE source_system = %s AND source_player_id = %s
        """,
        (SOURCE_SYSTEM, source_player_id),
    ).fetchone()
    return None if row is None else row[0]


def _existing_api_tennis_tournament_id(conn: psycopg.Connection, source_tournament_id: str) -> int | None:
    row = conn.execute(
        """
        SELECT tournament_id
        FROM tournament_source_ids
        WHERE source_system = %s AND source_tournament_id = %s
        """,
        (SOURCE_SYSTEM, source_tournament_id),
    ).fetchone()
    return None if row is None else row[0]


def _classify_event_type(event_type_type: str, fixture: Mapping[str, Any]) -> tuple[str, str]:
    normalized = _normalize_token(event_type_type)
    if _contains_out_of_scope_token(normalized):
        raise ApiTennisSourceAError("out_of_scope_event_type")
    explicit_tier = _optional_text_any(fixture, ("competition_tier", "event_competition_tier"))
    if normalized in _CHALLENGER_TYPE_TOKENS:
        return "CHALLENGER", "CHALLENGER"
    if normalized in _ATP_MAIN_TYPE_TOKENS:
        if explicit_tier:
            tier = explicit_tier.strip().upper().replace(" ", "_")
            if tier not in _SUPPORTED_COMPETITION_TIERS or tier == "CHALLENGER":
                raise ApiTennisSourceAError("unsupported_competition_tier")
            return "ATP_MAIN", tier
        return "ATP_MAIN", "ATP_TOUR"
    raise ApiTennisSourceAError("unsupported_event_type")


def _contains_out_of_scope_token(value: str) -> bool:
    return any(
        token in value
        for token in (
            "wta",
            "women",
            "doubles",
            "mixed",
            "itf",
            "junior",
            "exhibition",
        )
    )


def _match_status(value: str) -> str:
    normalized = _normalize_token(value)
    status = _STATUS_MAP.get(normalized)
    if status is None:
        raise ApiTennisSourceAError("unsupported_event_status")
    return status


def _match_start_at(*, event_date: str, event_time: str) -> datetime:
    try:
        parsed_date = date.fromisoformat(event_date)
    except ValueError as exc:
        raise ApiTennisSourceAError("invalid_event_date") from exc
    normalized_time = event_time.strip()
    if len(normalized_time.split(":")) == 2:
        normalized_time = f"{normalized_time}:00"
    try:
        parsed_time = time.fromisoformat(normalized_time)
    except ValueError as exc:
        raise ApiTennisSourceAError("invalid_event_time") from exc
    if parsed_time.tzinfo is not None:
        raise ApiTennisSourceAError("event_time_must_be_utc_without_offset")
    return datetime.combine(parsed_date, parsed_time, tzinfo=UTC)


def _source_observed_at(_fixture: Mapping[str, Any], received_at: datetime) -> datetime:
    return received_at


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ApiTennisSourceAError(f"{field_name}_must_be_timezone_aware")
    return value.astimezone(UTC)


def _surface(value: str | None) -> str | None:
    if value is None:
        return None
    surface = _SURFACE_MAP.get(_normalize_token(value))
    if surface is None:
        raise ApiTennisSourceAError("unsupported_surface")
    return surface


def _best_of_sets(value: Any) -> int:
    if value in (None, ""):
        return 3
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ApiTennisSourceAError("invalid_best_of_sets") from exc
    if parsed not in {3, 5}:
        raise ApiTennisSourceAError("unsupported_best_of_sets")
    return parsed


def _qualifying_flag(fixture: Mapping[str, Any]) -> bool:
    value = fixture.get("qualifying_flag")
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        round_code = _optional_text_any(fixture, ("round", "event_round", "round_code"))
        return bool(round_code and round_code.strip().upper().startswith("Q"))
    if isinstance(value, str):
        normalized = _normalize_token(value)
        if normalized in {"true", "t", "yes", "y", "1"}:
            return True
        if normalized in {"false", "f", "no", "n", "0"}:
            return False
    raise ApiTennisSourceAError("invalid_qualifying_flag")


def _start_ingest_run(conn: psycopg.Connection, *, received_at: datetime) -> int:
    return conn.execute(
        """
        INSERT INTO ingest_runs (
            source_system,
            source_dataset,
            started_at,
            status,
            source_version
        )
        VALUES (%s, %s, %s, 'RUNNING', %s)
        RETURNING ingest_run_id
        """,
        (SOURCE_SYSTEM, SOURCE_DATASET, received_at, ADAPTER_VERSION),
    ).fetchone()[0]


def _finish_ingest_run(
    conn: psycopg.Connection,
    *,
    ingest_run_id: int,
    status: str,
    rows_seen: int,
    rows_inserted: int,
    rows_rejected: int,
    error_summary: list[dict[str, object]],
) -> None:
    conn.execute(
        """
        UPDATE ingest_runs
        SET finished_at = %s,
            status = %s,
            rows_seen = %s,
            rows_inserted = %s,
            rows_rejected = %s,
            error_summary = %s::jsonb
        WHERE ingest_run_id = %s
        """,
        (
            datetime.now(UTC),
            status,
            rows_seen,
            rows_inserted,
            rows_rejected,
            json.dumps(error_summary),
            ingest_run_id,
        ),
    )


def _required_text(fixture: Mapping[str, Any], field_name: str) -> str:
    value = _optional_text(fixture, field_name)
    if value is None:
        raise ApiTennisSourceAError(f"missing_required_field:{field_name}")
    return value


def _required_text_any(fixture: Mapping[str, Any], field_names: tuple[str, ...]) -> str:
    value = _optional_text_any(fixture, field_names)
    if value is None:
        raise ApiTennisSourceAError(f"missing_required_field:{field_names[0]}")
    return value


def _optional_text_any(fixture: Mapping[str, Any], field_names: tuple[str, ...]) -> str | None:
    for field_name in field_names:
        value = _optional_text(fixture, field_name)
        if value is not None:
            return value
    return None


def _optional_text(fixture: Mapping[str, Any], field_name: str) -> str | None:
    value = fixture.get(field_name)
    if value is None:
        return None
    text = collapse_whitespace(str(value))
    return text or None


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = collapse_whitespace(str(value))
    return text or None


def _normalize_token(value: str) -> str:
    return collapse_whitespace(value).casefold()
