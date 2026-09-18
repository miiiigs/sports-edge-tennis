"""Resilient, resumable API-Tennis historical result refresh orchestration.

This wrapper keeps the canonical chunk ingest semantics from ``history_refresh`` and
adds bounded transient retries, exponential backoff, adaptive chunk splitting,
durable PostgreSQL progress, and a single-worker advisory lock. The checkpoint table
is operational state only; canonical historical truth remains in the append-only
fixture/result tables written by ``refresh_history_chunk``.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Callable

import psycopg

from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.history_refresh import (
    MAX_CHUNK_DAYS,
    MAX_WINDOW_DAYS,
    HistoryRefreshChunkOutcome,
    refresh_history_chunk,
)
from sports_edge_tennis.live.transport import TransportError

CHECKPOINT_SCHEMA_VERSION = "history_refresh_checkpoint_v1"
DEFAULT_CHECKPOINT_KEY = "API_TENNIS_RECENT_HISTORY_V1"
DEFAULT_CHUNK_DAYS = 7
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_MIN_CHUNK_DAYS = 1
A2_STATS_DRY_RUN_ENABLED_ENV = "A2_STATS_BACKFILL_DRY_RUN_ENABLED"
A2_STATS_DRY_RUN_DAYS_ENV = "A2_STATS_BACKFILL_DRY_RUN_DAYS"
A2_STATS_DRY_RUN_DEFAULT_DAYS = 7
A2_STATS_DRY_RUN_MAX_DAYS = 31
_ADVISORY_LOCK_KEY = 702_260_913
_RETRYABLE_KINDS = {
    "TIMEOUT", "NETWORK", "HTTP_429", "HTTP_500", "HTTP_502", "HTTP_503", "HTTP_504",
}

_CHECKPOINT_DDL = """
CREATE TABLE IF NOT EXISTS history_refresh_checkpoints (
    checkpoint_key TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    requested_chunk_days INTEGER NOT NULL CHECK (requested_chunk_days BETWEEN 1 AND 31),
    next_start_date DATE NOT NULL,
    completed_chunks BIGINT NOT NULL DEFAULT 0 CHECK (completed_chunks >= 0),
    last_completed_end DATE,
    last_error_kind TEXT,
    last_error_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@dataclass(frozen=True)
class HistoryRefreshCheckpoint:
    checkpoint_key: str
    window_start: date
    window_end: date
    requested_chunk_days: int
    next_start_date: date
    completed_chunks: int
    last_completed_end: date | None
    last_error_kind: str | None


@dataclass
class HistoryRefreshTotals:
    fixtures_seen: int = 0
    fixtures_ingested: int = 0
    fixture_rejections: int = 0
    terminal_results_seen: int = 0
    results_inserted: int = 0
    results_unchanged: int = 0
    result_rejections: int = 0
    completed_chunks: int = 0

    def add(self, outcome: HistoryRefreshChunkOutcome) -> None:
        self.fixtures_seen += outcome.fixtures_seen
        self.fixtures_ingested += outcome.fixtures_ingested
        self.fixture_rejections += outcome.fixture_rejections
        self.terminal_results_seen += outcome.terminal_results_seen
        self.results_inserted += outcome.results_inserted
        self.results_unchanged += outcome.results_unchanged
        self.result_rejections += outcome.result_rejections
        self.completed_chunks += 1


RefreshChunk = Callable[..., HistoryRefreshChunkOutcome]
Sleep = Callable[[float], None]


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _default_end_date() -> date:
    return datetime.now(UTC).date() - timedelta(days=1)


def _flag_enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _run_optional_a2_stats_dry_run(
    conn: psycopg.Connection,
    *,
    api_key: str,
    end_date: date,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., object] | None = None,
) -> None:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(A2_STATS_DRY_RUN_ENABLED_ENV)):
        return

    try:
        days = int(
            str(values.get(A2_STATS_DRY_RUN_DAYS_ENV) or A2_STATS_DRY_RUN_DEFAULT_DAYS).strip()
        )
    except ValueError as exc:
        raise ValueError("A2 stats dry-run days must be an integer") from exc
    if not 1 <= days <= A2_STATS_DRY_RUN_MAX_DAYS:
        raise ValueError(
            f"A2 stats dry-run days must be between 1 and {A2_STATS_DRY_RUN_MAX_DAYS}"
        )

    start_date = end_date - timedelta(days=days - 1)
    if runner is None:
        from sports_edge_tennis.live.api_tennis_stats_backfill import (
            backfill_stats_range as dry_run_runner,
        )

        runner = dry_run_runner

    print(
        "A2_STATS_BACKFILL_DRY_RUN_DISPATCH "
        f"start={start_date.isoformat()} end={end_date.isoformat()} "
        f"days={days} writes=0",
        flush=True,
    )
    runner(
        conn,
        api_key=api_key,
        start_date=start_date,
        end_date=end_date,
        chunk_days=min(DEFAULT_CHUNK_DAYS, days),
        dry_run=True,
    )


def ensure_checkpoint_table(conn: psycopg.Connection) -> None:
    conn.execute(_CHECKPOINT_DDL)


def _load_checkpoint(
    conn: psycopg.Connection, *, checkpoint_key: str
) -> HistoryRefreshCheckpoint | None:
    row = conn.execute(
        """
        SELECT checkpoint_key, window_start, window_end, requested_chunk_days,
               next_start_date, completed_chunks, last_completed_end, last_error_kind
        FROM history_refresh_checkpoints
        WHERE checkpoint_key = %s AND schema_version = %s
        """,
        (checkpoint_key, CHECKPOINT_SCHEMA_VERSION),
    ).fetchone()
    if row is None:
        return None
    return HistoryRefreshCheckpoint(
        checkpoint_key=row[0], window_start=row[1], window_end=row[2],
        requested_chunk_days=int(row[3]), next_start_date=row[4],
        completed_chunks=int(row[5]), last_completed_end=row[6], last_error_kind=row[7],
    )


def _create_checkpoint(
    conn: psycopg.Connection, *, checkpoint_key: str,
    start_date: date, end_date: date, chunk_days: int,
) -> HistoryRefreshCheckpoint:
    conn.execute(
        """
        INSERT INTO history_refresh_checkpoints (
            checkpoint_key, schema_version, window_start, window_end,
            requested_chunk_days, next_start_date, completed_chunks,
            last_completed_end, last_error_kind, last_error_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, 0, NULL, NULL, NULL, CURRENT_TIMESTAMP)
        ON CONFLICT (checkpoint_key) DO UPDATE
        SET schema_version = EXCLUDED.schema_version,
            window_start = EXCLUDED.window_start,
            window_end = EXCLUDED.window_end,
            requested_chunk_days = EXCLUDED.requested_chunk_days,
            next_start_date = EXCLUDED.next_start_date,
            completed_chunks = 0,
            last_completed_end = NULL,
            last_error_kind = NULL,
            last_error_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        """,
        (checkpoint_key, CHECKPOINT_SCHEMA_VERSION, start_date, end_date, chunk_days, start_date),
    )
    checkpoint = _load_checkpoint(conn, checkpoint_key=checkpoint_key)
    assert checkpoint is not None
    return checkpoint


def prepare_checkpoint(
    conn: psycopg.Connection, *, checkpoint_key: str,
    start_date: date, end_date: date, chunk_days: int,
    restart_window: bool = False,
) -> HistoryRefreshCheckpoint:
    """Load progress and extend the same window without replaying completed chunks."""
    ensure_checkpoint_table(conn)
    existing = _load_checkpoint(conn, checkpoint_key=checkpoint_key)
    if restart_window or existing is None or existing.window_start != start_date:
        return _create_checkpoint(
            conn, checkpoint_key=checkpoint_key, start_date=start_date,
            end_date=end_date, chunk_days=chunk_days,
        )
    if end_date < existing.window_end and existing.next_start_date > end_date + timedelta(days=1):
        raise ValueError("requested end_date is behind completed checkpoint progress")
    conn.execute(
        """
        UPDATE history_refresh_checkpoints
        SET window_end = %s, requested_chunk_days = %s, updated_at = CURRENT_TIMESTAMP
        WHERE checkpoint_key = %s
        """,
        (end_date, chunk_days, checkpoint_key),
    )
    checkpoint = _load_checkpoint(conn, checkpoint_key=checkpoint_key)
    assert checkpoint is not None
    return checkpoint


def _record_chunk_success(
    conn: psycopg.Connection, *, checkpoint_key: str, chunk_end: date
) -> None:
    conn.execute(
        """
        UPDATE history_refresh_checkpoints
        SET next_start_date = %s,
            completed_chunks = completed_chunks + 1,
            last_completed_end = %s,
            last_error_kind = NULL,
            last_error_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE checkpoint_key = %s
        """,
        (chunk_end + timedelta(days=1), chunk_end, checkpoint_key),
    )


def _record_failure(conn: psycopg.Connection, *, checkpoint_key: str, kind: str) -> None:
    conn.execute(
        """
        UPDATE history_refresh_checkpoints
        SET last_error_kind = %s, last_error_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE checkpoint_key = %s
        """,
        (kind, checkpoint_key),
    )


def _attempt_chunk(
    conn: psycopg.Connection, *, api_key: str, start_date: date, end_date: date,
    max_attempts: int, backoff_seconds: float,
    refresh_chunk: RefreshChunk, sleep: Sleep,
) -> HistoryRefreshChunkOutcome:
    for attempt in range(1, max_attempts + 1):
        try:
            return refresh_chunk(
                conn, api_key=api_key, start_date=start_date, end_date=end_date
            )
        except TransportError as exc:
            if exc.kind not in _RETRYABLE_KINDS or attempt >= max_attempts:
                raise
            delay = backoff_seconds * (2 ** (attempt - 1))
            print(
                "HISTORY_REFRESH_RETRY "
                f"start={start_date.isoformat()} end={end_date.isoformat()} "
                f"attempt={attempt + 1}/{max_attempts} kind={exc.kind} "
                f"sleep_seconds={delay:g}",
                flush=True,
            )
            sleep(delay)
    raise AssertionError("unreachable")


def _process_resilient_chunk(
    conn: psycopg.Connection, *, api_key: str, start_date: date, end_date: date,
    checkpoint_key: str, max_attempts: int, backoff_seconds: float,
    min_chunk_days: int, refresh_chunk: RefreshChunk, sleep: Sleep,
    totals: HistoryRefreshTotals,
) -> None:
    try:
        outcome = _attempt_chunk(
            conn, api_key=api_key, start_date=start_date, end_date=end_date,
            max_attempts=max_attempts, backoff_seconds=backoff_seconds,
            refresh_chunk=refresh_chunk, sleep=sleep,
        )
    except TransportError as exc:
        span_days = (end_date - start_date).days + 1
        if exc.kind in _RETRYABLE_KINDS and span_days > min_chunk_days:
            left_days = span_days // 2
            split_end = start_date + timedelta(days=left_days - 1)
            right_start = split_end + timedelta(days=1)
            print(
                "HISTORY_REFRESH_SPLIT "
                f"start={start_date.isoformat()} end={end_date.isoformat()} kind={exc.kind} "
                f"left={start_date.isoformat()}..{split_end.isoformat()} "
                f"right={right_start.isoformat()}..{end_date.isoformat()}",
                flush=True,
            )
            _process_resilient_chunk(
                conn, api_key=api_key, start_date=start_date, end_date=split_end,
                checkpoint_key=checkpoint_key, max_attempts=max_attempts,
                backoff_seconds=backoff_seconds, min_chunk_days=min_chunk_days,
                refresh_chunk=refresh_chunk, sleep=sleep, totals=totals,
            )
            _process_resilient_chunk(
                conn, api_key=api_key, start_date=right_start, end_date=end_date,
                checkpoint_key=checkpoint_key, max_attempts=max_attempts,
                backoff_seconds=backoff_seconds, min_chunk_days=min_chunk_days,
                refresh_chunk=refresh_chunk, sleep=sleep, totals=totals,
            )
            return
        _record_failure(conn, checkpoint_key=checkpoint_key, kind=exc.kind)
        print(
            "HISTORY_REFRESH_FAILED "
            f"kind={exc.kind} failed_start={start_date.isoformat()} "
            f"failed_end={end_date.isoformat()} checkpoint_key={checkpoint_key}",
            flush=True,
        )
        raise

    totals.add(outcome)
    _record_chunk_success(conn, checkpoint_key=checkpoint_key, chunk_end=end_date)
    print(
        "HISTORY_REFRESH_CHUNK "
        f"start={outcome.start_date.isoformat()} end={outcome.end_date.isoformat()} "
        f"fixtures_seen={outcome.fixtures_seen} fixtures_ingested={outcome.fixtures_ingested} "
        f"fixture_rejections={outcome.fixture_rejections} "
        f"terminal_seen={outcome.terminal_results_seen} "
        f"results_inserted={outcome.results_inserted} "
        f"results_unchanged={outcome.results_unchanged} "
        f"result_rejections={outcome.result_rejections}",
        flush=True,
    )


def run_resilient_history_refresh(
    conn: psycopg.Connection, *, api_key: str, start_date: date, end_date: date,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    checkpoint_key: str = DEFAULT_CHECKPOINT_KEY,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    min_chunk_days: int = DEFAULT_MIN_CHUNK_DAYS,
    restart_window: bool = False,
    refresh_chunk: RefreshChunk = refresh_history_chunk,
    sleep: Sleep = time.sleep,
) -> HistoryRefreshTotals:
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    if not 1 <= chunk_days <= MAX_CHUNK_DAYS:
        raise ValueError(f"chunk_days must be between 1 and {MAX_CHUNK_DAYS}")
    if not 1 <= min_chunk_days <= chunk_days:
        raise ValueError("min_chunk_days must be between 1 and chunk_days")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds must be >= 0")
    if (end_date - start_date).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError(f"window may not exceed {MAX_WINDOW_DAYS} days")

    lock_row = conn.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)).fetchone()
    if not lock_row or not bool(lock_row[0]):
        raise RuntimeError("history_refresh_already_running")
    try:
        checkpoint = prepare_checkpoint(
            conn, checkpoint_key=checkpoint_key, start_date=start_date,
            end_date=end_date, chunk_days=chunk_days, restart_window=restart_window,
        )
        resume_from = max(start_date, checkpoint.next_start_date)
        totals = HistoryRefreshTotals()
        print(
            "HISTORY_REFRESH_RESUME "
            f"checkpoint_key={checkpoint_key} resume_from={resume_from.isoformat()} "
            f"requested_start={start_date.isoformat()} requested_end={end_date.isoformat()} "
            f"completed_chunks={checkpoint.completed_chunks}",
            flush=True,
        )
        cursor = resume_from
        while cursor <= end_date:
            chunk_end = min(end_date, cursor + timedelta(days=chunk_days - 1))
            _process_resilient_chunk(
                conn, api_key=api_key, start_date=cursor, end_date=chunk_end,
                checkpoint_key=checkpoint_key, max_attempts=max_attempts,
                backoff_seconds=backoff_seconds, min_chunk_days=min_chunk_days,
                refresh_chunk=refresh_chunk, sleep=sleep, totals=totals,
            )
            cursor = chunk_end + timedelta(days=1)
        return totals
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))


def main(argv: list[str] | None = None) -> int:
    default_end = _default_end_date()
    parser = argparse.ArgumentParser(
        description="Resilient resumable API-Tennis historical result refresh"
    )
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat, default=default_end)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    parser.add_argument("--min-chunk-days", type=int, default=DEFAULT_MIN_CHUNK_DAYS)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--backoff-seconds", type=float, default=DEFAULT_BACKOFF_SECONDS)
    parser.add_argument("--checkpoint-key", default=DEFAULT_CHECKPOINT_KEY)
    parser.add_argument("--restart-window", action="store_true")
    parser.add_argument("--dsn", default=_db_dsn())
    args = parser.parse_args(argv)

    start_date = args.start_date or (args.end_date - timedelta(days=2))
    if args.end_date < start_date:
        print("HISTORY_REFRESH_BLOCKED reason=INVALID_DATE_RANGE", flush=True)
        return 2
    if not 1 <= args.chunk_days <= MAX_CHUNK_DAYS:
        print(f"HISTORY_REFRESH_BLOCKED reason=CHUNK_DAYS_MAX_{MAX_CHUNK_DAYS}", flush=True)
        return 2
    if not 1 <= args.min_chunk_days <= args.chunk_days:
        print("HISTORY_REFRESH_BLOCKED reason=INVALID_MIN_CHUNK_DAYS", flush=True)
        return 2
    if args.max_attempts < 1 or args.backoff_seconds < 0:
        print("HISTORY_REFRESH_BLOCKED reason=INVALID_RETRY_CONFIG", flush=True)
        return 2
    window_days = (args.end_date - start_date).days + 1
    if window_days > MAX_WINDOW_DAYS:
        print(f"HISTORY_REFRESH_BLOCKED reason=WINDOW_DAYS_MAX_{MAX_WINDOW_DAYS}", flush=True)
        return 2
    if not args.dsn:
        print("HISTORY_REFRESH_BLOCKED reason=NO_DATABASE", flush=True)
        return 2

    try:
        api_key = load_secret("API_TENNIS_API_KEY")
    except MissingCredentialError:
        api_key = None
    if not api_key:
        print("HISTORY_REFRESH_BLOCKED reason=NO_API_TENNIS_KEY", flush=True)
        return 2

    print(
        "HISTORY_REFRESH_START "
        f"start_date={start_date.isoformat()} end_date={args.end_date.isoformat()} "
        f"chunk_days={args.chunk_days} min_chunk_days={args.min_chunk_days} "
        f"max_attempts={args.max_attempts} checkpoint_key={args.checkpoint_key}",
        flush=True,
    )
    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            totals = run_resilient_history_refresh(
                conn, api_key=api_key, start_date=start_date, end_date=args.end_date,
                chunk_days=args.chunk_days, checkpoint_key=args.checkpoint_key,
                max_attempts=args.max_attempts, backoff_seconds=args.backoff_seconds,
                min_chunk_days=args.min_chunk_days, restart_window=args.restart_window,
            )
            _run_optional_a2_stats_dry_run(
                conn,
                api_key=api_key,
                end_date=args.end_date,
            )
    except TransportError:
        return 2
    except (ValueError, RuntimeError) as exc:
        print(f"HISTORY_REFRESH_BLOCKED reason={str(exc).upper()}", flush=True)
        return 2

    print(
        "HISTORY_REFRESH_SUCCESS "
        f"completed_chunks={totals.completed_chunks} fixtures_seen={totals.fixtures_seen} "
        f"fixtures_ingested={totals.fixtures_ingested} fixture_rejections={totals.fixture_rejections} "
        f"terminal_seen={totals.terminal_results_seen} results_inserted={totals.results_inserted} "
        f"results_unchanged={totals.results_unchanged} result_rejections={totals.result_rejections}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
