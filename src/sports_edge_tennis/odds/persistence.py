from __future__ import annotations

import json
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from .contracts import (
    BookmakerMapping,
    LiveOddsIntegrityError,
    MatchVersionContext,
    NormalizedOddsQuote,
    ProviderObservation,
    RawObservationRecord,
)
from .normalizer import raw_payload_and_hash


NORMALIZER_VERSION = "live_odds_normalizer_v1"


def create_ingest_run(conn: psycopg.Connection, *, provider_code: str, started_at: datetime) -> int:
    return conn.execute(
        """
        INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
        VALUES (%s, 'LIVE_ODDS', %s, 'RUNNING')
        RETURNING ingest_run_id
        """,
        (provider_code, started_at),
    ).fetchone()[0]


def finish_ingest_run(
    conn: psycopg.Connection,
    *,
    ingest_run_id: int,
    status: str,
    rows_seen: int,
    rows_inserted: int,
    rows_rejected: int,
    rows_unmatched: int,
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
            rows_unmatched = %s,
            error_summary = %s::jsonb
        WHERE ingest_run_id = %s
        """,
        (
            datetime.now(timezone.utc),
            status,
            rows_seen,
            rows_inserted,
            rows_rejected,
            rows_unmatched,
            json.dumps(error_summary, sort_keys=True),
            ingest_run_id,
        ),
    )


def persist_raw_observation(
    conn: psycopg.Connection,
    *,
    ingest_run_id: int,
    observation: ProviderObservation,
) -> RawObservationRecord:
    redacted_payload, digest = raw_payload_and_hash(observation.payload)
    row = conn.execute(
        """
        WITH inserted AS (
            INSERT INTO raw_odds_observations (
                ingest_run_id,
                provider_code,
                provider_event_id,
                provider_observation_key,
                provider_request_id,
                provider_response_id,
                provider_observed_at,
                received_at,
                observation_time,
                observation_time_basis,
                payload_hash,
                payload_json,
                adapter_version,
                normalizer_version
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
            )
            ON CONFLICT (provider_code, provider_observation_key) DO NOTHING
            RETURNING
                raw_odds_observation_id,
                ingest_run_id,
                provider_code,
                provider_event_id,
                provider_observation_key,
                provider_request_id,
                provider_response_id,
                provider_observed_at,
                received_at,
                observation_time,
                observation_time_basis,
                payload_hash,
                payload_json,
                adapter_version,
                normalizer_version,
                TRUE AS inserted
        )
        SELECT * FROM inserted
        UNION ALL
        SELECT
            raw_odds_observation_id,
            ingest_run_id,
            provider_code,
            provider_event_id,
            provider_observation_key,
            provider_request_id,
            provider_response_id,
            provider_observed_at,
            received_at,
            observation_time,
            observation_time_basis,
            payload_hash,
            payload_json,
            adapter_version,
            normalizer_version,
            FALSE AS inserted
        FROM raw_odds_observations
        WHERE provider_code = %s
          AND provider_observation_key = %s
          AND NOT EXISTS (SELECT 1 FROM inserted)
        LIMIT 1
        """,
        (
            ingest_run_id,
            observation.provider_code,
            observation.provider_event_id,
            observation.provider_observation_key,
            observation.provider_request_id,
            observation.provider_response_id,
            observation.provider_observed_at,
            observation.received_at,
            observation.observation_time,
            observation.observation_time_basis,
            digest,
            Jsonb(redacted_payload),
            observation.adapter_version,
            NORMALIZER_VERSION,
            observation.provider_code,
            observation.provider_observation_key,
        ),
    ).fetchone()
    if row is None:
        raise LiveOddsIntegrityError("raw observation insert/read returned no row")

    record = RawObservationRecord(
        raw_odds_observation_id=row[0],
        ingest_run_id=row[1],
        provider_code=row[2],
        provider_event_id=row[3],
        provider_observation_key=row[4],
        provider_request_id=row[5],
        provider_response_id=row[6],
        provider_observed_at=row[7],
        received_at=row[8],
        observation_time=row[9],
        observation_time_basis=row[10],
        payload_hash=row[11],
        payload_json=row[12],
        adapter_version=row[13],
        normalizer_version=row[14],
        inserted=row[15],
    )
    expected = {
        "provider_code": observation.provider_code,
        "provider_event_id": observation.provider_event_id,
        "provider_observation_key": observation.provider_observation_key,
        "provider_request_id": observation.provider_request_id,
        "provider_response_id": observation.provider_response_id,
        "provider_observed_at": observation.provider_observed_at,
        "received_at": observation.received_at,
        "observation_time": observation.observation_time,
        "observation_time_basis": observation.observation_time_basis,
        "payload_hash": digest,
        "payload_json": redacted_payload,
        "adapter_version": observation.adapter_version,
        "normalizer_version": NORMALIZER_VERSION,
    }
    actual = {
        "provider_code": record.provider_code,
        "provider_event_id": record.provider_event_id,
        "provider_observation_key": record.provider_observation_key,
        "provider_request_id": record.provider_request_id,
        "provider_response_id": record.provider_response_id,
        "provider_observed_at": record.provider_observed_at,
        "received_at": record.received_at,
        "observation_time": record.observation_time,
        "observation_time_basis": record.observation_time_basis,
        "payload_hash": record.payload_hash,
        "payload_json": record.payload_json,
        "adapter_version": record.adapter_version,
        "normalizer_version": record.normalizer_version,
    }
    if actual != expected:
        raise LiveOddsIntegrityError("raw observation retry payload differs from persisted truth")
    return record


def insert_bookmaker_mapping(
    conn: psycopg.Connection,
    *,
    provider_code: str,
    provider_bookmaker_key: str,
    mapping_version: int,
    bookmaker_code: str,
    normalization_class: str,
    effective_from: datetime,
) -> int:
    return conn.execute(
        """
        INSERT INTO provider_bookmaker_mappings (
            provider_code,
            provider_bookmaker_key,
            mapping_version,
            bookmaker_code,
            normalization_class,
            effective_from
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING provider_bookmaker_mapping_id
        """,
        (
            provider_code,
            provider_bookmaker_key,
            mapping_version,
            bookmaker_code,
            normalization_class,
            effective_from,
        ),
    ).fetchone()[0]


def select_bookmaker_mapping(
    conn: psycopg.Connection,
    *,
    provider_code: str,
    provider_bookmaker_key: str,
    observation_time: datetime,
) -> BookmakerMapping | None:
    row = conn.execute(
        """
        SELECT
            provider_bookmaker_mapping_id,
            provider_code,
            provider_bookmaker_key,
            mapping_version,
            bookmaker_code,
            normalization_class::text,
            effective_from
        FROM provider_bookmaker_mappings
        WHERE provider_code = %s
          AND provider_bookmaker_key = %s
          AND effective_from <= %s
        ORDER BY effective_from DESC, provider_bookmaker_mapping_id DESC
        LIMIT 1
        """,
        (provider_code, provider_bookmaker_key, observation_time),
    ).fetchone()
    if row is None:
        return None
    return BookmakerMapping(*row)


def resolve_provider_event(
    conn: psycopg.Connection,
    *,
    provider_code: str,
    provider_event_id: str,
) -> tuple[str, int | None]:
    rows = conn.execute(
        """
        SELECT match_id
        FROM match_source_ids
        WHERE source_system = %s
          AND source_match_id = %s
        ORDER BY match_source_id
        LIMIT 2
        """,
        (provider_code, provider_event_id),
    ).fetchall()
    if not rows:
        return "UNMATCHED", None
    if len(rows) > 1:
        return "IDENTITY_CONFLICT", None
    return "RESOLVED", rows[0][0]


def resolve_provider_player(
    conn: psycopg.Connection,
    *,
    provider_code: str,
    provider_player_id: str,
) -> tuple[str, int | None]:
    rows = conn.execute(
        """
        SELECT player_id
        FROM player_source_ids
        WHERE source_system = %s
          AND source_player_id = %s
        ORDER BY player_source_id
        LIMIT 2
        """,
        (provider_code, provider_player_id),
    ).fetchall()
    if not rows:
        return "UNMATCHED", None
    if len(rows) > 1:
        return "IDENTITY_CONFLICT", None
    return "RESOLVED", rows[0][0]


def load_pit_match_version(
    conn: psycopg.Connection,
    *,
    match_id: int,
    observation_time: datetime,
) -> MatchVersionContext | None:
    row = conn.execute(
        """
        SELECT
            match_id,
            match_version_id,
            player_1_id,
            player_2_id,
            match_start_at,
            tour_scope::text,
            competition_tier::text,
            match_status::text
        FROM match_versions
        WHERE match_id = %s
          AND source_observed_at <= %s
          AND as_of <= %s
        ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
        LIMIT 1
        """,
        (match_id, observation_time, observation_time),
    ).fetchone()
    if row is None:
        return None
    return MatchVersionContext(*row)


def insert_live_odds_snapshot(
    conn: psycopg.Connection,
    *,
    match_id: int,
    quote: NormalizedOddsQuote,
) -> bool:
    row = conn.execute(
        """
        WITH inserted AS (
            INSERT INTO odds_snapshots (
                match_id,
                source_system,
                bookmaker_code,
                market_type,
                selection_type,
                line_value,
                decimal_price,
                market_status,
                source_snapshot_id,
                source_record_hash,
                source_observed_at,
                as_of,
                raw_odds_observation_id,
                provider_bookmaker_mapping_id,
                odds_origin
            )
            VALUES (
                %s, %s, %s, 'MATCH_WINNER', %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, 'LIVE_PROVIDER'
            )
            ON CONFLICT (
                source_system,
                source_snapshot_id,
                bookmaker_code,
                match_id,
                market_type,
                selection_type,
                line_value,
                source_observed_at
            )
            WHERE source_snapshot_id IS NOT NULL
            DO NOTHING
            RETURNING
                odds_snapshot_id,
                decimal_price,
                market_status::text,
                source_record_hash,
                as_of,
                raw_odds_observation_id,
                provider_bookmaker_mapping_id,
                odds_origin::text,
                TRUE AS inserted
        )
        SELECT * FROM inserted
        UNION ALL
        SELECT
            odds_snapshot_id,
            decimal_price,
            market_status::text,
            source_record_hash,
            as_of,
            raw_odds_observation_id,
            provider_bookmaker_mapping_id,
            odds_origin::text,
            FALSE AS inserted
        FROM odds_snapshots
        WHERE source_system = %s
          AND source_snapshot_id = %s
          AND bookmaker_code = %s
          AND match_id = %s
          AND market_type = 'MATCH_WINNER'
          AND selection_type = %s
          AND line_value IS NULL
          AND source_observed_at = %s
          AND NOT EXISTS (SELECT 1 FROM inserted)
        LIMIT 1
        """,
        (
            match_id,
            quote.source_system,
            quote.bookmaker_code,
            quote.selection_type,
            quote.decimal_price,
            quote.market_status,
            quote.source_snapshot_id,
            quote.source_record_hash,
            quote.source_observed_at,
            quote.as_of,
            quote.raw_odds_observation_id,
            quote.provider_bookmaker_mapping_id,
            quote.source_system,
            quote.source_snapshot_id,
            quote.bookmaker_code,
            match_id,
            quote.selection_type,
            quote.source_observed_at,
        ),
    ).fetchone()
    if row is None:
        raise LiveOddsIntegrityError("odds snapshot insert/read returned no row")
    if (
        row[1] != quote.decimal_price
        or row[2] != quote.market_status
        or row[3] != quote.source_record_hash
        or row[4] != quote.as_of
        or row[5] != quote.raw_odds_observation_id
        or row[6] != quote.provider_bookmaker_mapping_id
        or row[7] != "LIVE_PROVIDER"
    ):
        raise LiveOddsIntegrityError("persisted odds snapshot truth differs from normalized quote")
    return bool(row[8])


def reconciliation_error_summary(details: list[dict[str, object]]) -> list[dict[str, object]]:
    return [dict(item) for item in details]
