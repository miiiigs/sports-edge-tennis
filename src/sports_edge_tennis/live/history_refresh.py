"""Bounded API-Tennis completed-result refresh for historical model state.

This module is intentionally separate from the hourly live slate runner. It fetches a
bounded date window with no retries or polling, reuses the canonical API-Tennis fixture
adapter, and appends terminal result truth only when the provider outcome has changed.
It also persists reproducible match-period serve/return statistics that API-Tennis
already returns inline so Agent 2 can accumulate live-history coverage.

Point-in-time contract:
* ``source_observed_at`` and ``as_of`` are the local receive time, never backdated.
* match occurrence time stays on ``match_versions.match_start_at``.
* Agent 3 separately uses match occurrence time for decay once the result is PIT-visible.
* Agent 2 statistics become visible only when this refresh actually receives them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable, Iterable, Mapping

import psycopg
import requests

from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.data.sources.api_tennis_live import (
    SOURCE_SYSTEM,
    ApiTennisIngestedMatch,
    ingest_api_tennis_source_a_fixtures,
)
from sports_edge_tennis.live.api_tennis_fixture_compat import (
    enrich_api_tennis_fixture_surfaces,
    normalize_api_tennis_fixture_rounds,
    normalize_api_tennis_upcoming_status,
)
from sports_edge_tennis.live.api_tennis_match_stats import persist_api_tennis_match_stats
from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.transport import (
    API_TENNIS_BASE_URL,
    DEFAULT_TIMEOUT,
    TransportError,
)


RESULT_DATASET = "api_tennis_completed_result_refresh"
RESULT_ADAPTER_VERSION = "API_TENNIS_RESULT_REFRESH_V1"
DEFAULT_CHUNK_DAYS = 31
MAX_CHUNK_DAYS = 31
MAX_WINDOW_DAYS = 370

_TERMINAL_STATUS = {
    "finished": "COMPLETED",
    "completed": "COMPLETED",
    "retired": "RETIRED",
    "walkover": "WALKOVER",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
}
_FIRST_WINNER = {"first player", "first", "player 1", "player1", "1"}
_SECOND_WINNER = {"second player", "second", "player 2", "player2", "2"}
_FINAL_RESULT_RE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$")


class HistoryRefreshError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedTerminalResult:
    outcome_status: str
    winner_side: int | None
    retired_side: int | None
    sets_won_player_1: int | None
    sets_won_player_2: int | None
    scoreline_text: str | None

    @property
    def semantic_key(self) -> tuple[object, ...]:
        return (
            self.outcome_status,
            self.winner_side,
            self.retired_side,
            self.sets_won_player_1,
            self.sets_won_player_2,
        )


@dataclass(frozen=True)
class HistoryRefreshChunkOutcome:
    start_date: date
    end_date: date
    fixtures_seen: int
    fixtures_ingested: int
    fixture_rejections: int
    terminal_results_seen: int
    results_inserted: int
    results_unchanged: int
    result_rejections: int


FetchWindow = Callable[[str, date, date], tuple[datetime, list[dict[str, Any]]]]


def _normalize_token(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text if text and text != "-" else None


def _winner_side(value: Any) -> int | None:
    normalized = _normalize_token(value)
    if normalized in _FIRST_WINNER:
        return 1
    if normalized in _SECOND_WINNER:
        return 2
    return None


def _set_score(value: Any) -> tuple[int | None, int | None, str | None]:
    text = _text(value)
    if text is None:
        return None, None, None
    match = _FINAL_RESULT_RE.match(text)
    if match is None:
        return None, None, text
    return int(match.group(1)), int(match.group(2)), text


def parse_terminal_result(fixture: Mapping[str, Any]) -> ParsedTerminalResult | None:
    outcome_status = _TERMINAL_STATUS.get(_normalize_token(fixture.get("event_status")))
    if outcome_status is None:
        return None

    winner_side = _winner_side(fixture.get("event_winner"))
    if outcome_status != "CANCELLED" and winner_side is None:
        raise HistoryRefreshError("missing_terminal_winner")
    if outcome_status == "CANCELLED":
        winner_side = None

    retired_side = None
    if outcome_status == "RETIRED" and winner_side in (1, 2):
        retired_side = 2 if winner_side == 1 else 1

    sets_1, sets_2, scoreline = _set_score(fixture.get("event_final_result"))
    return ParsedTerminalResult(
        outcome_status=outcome_status,
        winner_side=winner_side,
        retired_side=retired_side,
        sets_won_player_1=sets_1,
        sets_won_player_2=sets_2,
        scoreline_text=scoreline,
    )


def fetch_api_tennis_fixture_window(
    api_key: str,
    start_date: date,
    end_date: date,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    base_url: str = API_TENNIS_BASE_URL,
    clock: Callable[[], datetime] | None = None,
) -> tuple[datetime, list[dict[str, Any]]]:
    """Fetch one API-Tennis fixture window with one HTTP request and no retries."""
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    if (end_date - start_date).days + 1 > MAX_CHUNK_DAYS:
        raise ValueError(f"fixture window may not exceed {MAX_CHUNK_DAYS} days")

    params = {
        "method": "get_fixtures",
        "APIkey": api_key,
        "date_start": start_date.isoformat(),
        "date_stop": end_date.isoformat(),
        "timezone": "UTC",
    }
    try:
        response = requests.get(base_url, params=params, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise TransportError("TIMEOUT", "api-tennis history request timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise TransportError("NETWORK", "api-tennis history request failed") from exc

    received_at = (clock or (lambda: datetime.now(UTC)))()
    if response.status_code != 200:
        raise TransportError(
            f"HTTP_{response.status_code}",
            "api-tennis history request returned non-200",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise TransportError("PARSE", "api-tennis history response was not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("success") != 1:
        raise TransportError("PROVIDER_ERROR", "api-tennis history provider reported failure")
    result = payload.get("result")
    if not isinstance(result, list):
        raise TransportError("PARSE", "api-tennis history response missing result list")
    return received_at, result


def _start_result_ingest_run(conn: psycopg.Connection, received_at: datetime) -> int:
    return int(
        conn.execute(
            """
            INSERT INTO ingest_runs (
                source_system, source_dataset, started_at, status, source_version
            )
            VALUES (%s, %s, %s, 'RUNNING', %s)
            RETURNING ingest_run_id
            """,
            (SOURCE_SYSTEM, RESULT_DATASET, received_at, RESULT_ADAPTER_VERSION),
        ).fetchone()[0]
    )


def _finish_result_ingest_run(
    conn: psycopg.Connection,
    *,
    ingest_run_id: int,
    status: str,
    rows_seen: int,
    rows_inserted: int,
    rows_rejected: int,
    errors: list[dict[str, object]],
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
            json.dumps(errors),
            ingest_run_id,
        ),
    )


def _latest_result_semantic_key(
    conn: psycopg.Connection,
    match_id: int,
) -> tuple[object, ...] | None:
    row = conn.execute(
        """
        SELECT
            outcome_status::text,
            winner_side,
            retired_side,
            sets_won_player_1,
            sets_won_player_2
        FROM match_result_observations
        WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    return None if row is None else tuple(row)


def _fixture_by_source_match_id(
    fixtures: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for fixture in fixtures:
        event_key = _text(fixture.get("event_key"))
        if event_key is not None:
            result[event_key] = fixture
    return result


def _persist_terminal_results(
    conn: psycopg.Connection,
    *,
    fixtures: list[dict[str, Any]],
    ingested_matches: tuple[ApiTennisIngestedMatch, ...],
    received_at: datetime,
) -> tuple[int, int, int, int]:
    by_event = _fixture_by_source_match_id(fixtures)
    parsed: list[tuple[ApiTennisIngestedMatch, ParsedTerminalResult]] = []
    rejected: list[dict[str, object]] = []

    for ingested in ingested_matches:
        fixture = by_event.get(ingested.source_match_id)
        if fixture is None:
            continue
        try:
            terminal = parse_terminal_result(fixture)
        except HistoryRefreshError as exc:
            rejected.append({"event_key": ingested.source_match_id, "reason": str(exc)})
            continue
        if terminal is not None:
            parsed.append((ingested, terminal))

    if not parsed and not rejected:
        return 0, 0, 0, 0

    ingest_run_id = _start_result_ingest_run(conn, received_at)
    repository = HistoricalRepository(conn, ingest_run_id)
    inserted = 0
    unchanged = 0
    for ingested, terminal in parsed:
        if _latest_result_semantic_key(conn, ingested.match_id) == terminal.semantic_key:
            unchanged += 1
            continue
        repository.insert_match_result(
            match_id=ingested.match_id,
            outcome_status=terminal.outcome_status,
            winner_side=terminal.winner_side,
            retired_side=terminal.retired_side,
            sets_won_player_1=terminal.sets_won_player_1,
            sets_won_player_2=terminal.sets_won_player_2,
            games_won_player_1=None,
            games_won_player_2=None,
            scoreline_text=terminal.scoreline_text,
            source_observed_at=received_at,
            as_of=received_at,
        )
        inserted += 1

    status = "COMPLETED" if not rejected else ("PARTIAL" if inserted or unchanged else "FAILED")
    _finish_result_ingest_run(
        conn,
        ingest_run_id=ingest_run_id,
        status=status,
        rows_seen=len(parsed) + len(rejected),
        rows_inserted=inserted,
        rows_rejected=len(rejected),
        errors=rejected,
    )
    return len(parsed) + len(rejected), inserted, unchanged, len(rejected)


def _invalidate_model_checkpoint(conn: psycopg.Connection, checkpoint_key: str) -> None:
    table_exists = conn.execute(
        "SELECT to_regclass('public.live_model_state_checkpoints')"
    ).fetchone()[0]
    if table_exists is None:
        return
    conn.execute(
        "DELETE FROM live_model_state_checkpoints WHERE checkpoint_key = %s",
        (checkpoint_key,),
    )


def refresh_history_chunk(
    conn: psycopg.Connection,
    *,
    api_key: str,
    start_date: date,
    end_date: date,
    fetch_window: FetchWindow = fetch_api_tennis_fixture_window,
) -> HistoryRefreshChunkOutcome:
    received_at, fixtures = fetch_window(api_key, start_date, end_date)

    normalized = normalize_api_tennis_upcoming_status(fixtures, received_at=received_at)
    normalized = normalize_api_tennis_fixture_rounds(normalized)
    # Historical refresh intentionally uses DB-only surface enrichment. A nine-month
    # backfill must not fan out into one get_draw request per tournament.
    normalized = enrich_api_tennis_fixture_surfaces(
        conn,
        normalized,
        api_key=None,
    )
    fixture_outcome = ingest_api_tennis_source_a_fixtures(
        conn,
        normalized,
        received_at=received_at,
    )
    result_seen, result_inserted, result_unchanged, result_rejected = _persist_terminal_results(
        conn,
        fixtures=normalized,
        ingested_matches=fixture_outcome.matches,
        received_at=received_at,
    )
    stat_outcome = persist_api_tennis_match_stats(
        conn,
        fixtures=normalized,
        ingested_matches=fixture_outcome.matches,
        received_at=received_at,
    )
    print(
        "API_TENNIS_MATCH_STATS "
        f"eligible_matches={stat_outcome.eligible_matches} "
        f"inserted_rows={stat_outcome.inserted_rows} "
        f"existing_rows={stat_outcome.existing_rows} "
        f"skipped_matches={stat_outcome.skipped_matches}",
        flush=True,
    )

    if result_inserted:
        # Agent 3's graph depends on terminal result truth.
        _invalidate_model_checkpoint(conn, "AGENT_3_INTRANSITIVITY")
    if stat_outcome.inserted_rows:
        # Agent 2's serve/return snapshot depends on raw match statistics.
        _invalidate_model_checkpoint(conn, "AGENT_2_HIERARCHICAL_MARKOV")

    return HistoryRefreshChunkOutcome(
        start_date=start_date,
        end_date=end_date,
        fixtures_seen=len(fixtures),
        fixtures_ingested=fixture_outcome.rows_ingested,
        fixture_rejections=fixture_outcome.rows_rejected,
        terminal_results_seen=result_seen,
        results_inserted=result_inserted,
        results_unchanged=result_unchanged,
        result_rejections=result_rejected,
    )


def _chunk_ranges(start_date: date, end_date: date, chunk_days: int):
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(end_date, cursor + timedelta(days=chunk_days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _default_end_date() -> date:
    return datetime.now(UTC).date() - timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    default_end = _default_end_date()
    parser = argparse.ArgumentParser(description="Refresh bounded API-Tennis historical results")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat, default=default_end)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    parser.add_argument("--dsn", default=_db_dsn())
    args = parser.parse_args(argv)

    start_date = args.start_date or (args.end_date - timedelta(days=2))
    if args.end_date < start_date:
        print("HISTORY_REFRESH_BLOCKED reason=INVALID_DATE_RANGE", flush=True)
        return 2
    if not 1 <= args.chunk_days <= MAX_CHUNK_DAYS:
        print(f"HISTORY_REFRESH_BLOCKED reason=CHUNK_DAYS_MAX_{MAX_CHUNK_DAYS}", flush=True)
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
        print("HISTORY_REFRESH_BLOCKED reason=MISSING_API_TENNIS_KEY", flush=True)
        return 2

    chunks = list(_chunk_ranges(start_date, args.end_date, args.chunk_days))
    print(
        "HISTORY_REFRESH_START "
        f"start_date={start_date.isoformat()} end_date={args.end_date.isoformat()} "
        f"chunk_days={args.chunk_days} chunks={len(chunks)}",
        flush=True,
    )

    totals = {
        "fixtures": 0,
        "fixtures_ingested": 0,
        "fixture_rejections": 0,
        "terminal_results": 0,
        "results_inserted": 0,
        "results_unchanged": 0,
        "result_rejections": 0,
    }
    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            for chunk_start, chunk_end in chunks:
                outcome = refresh_history_chunk(
                    conn,
                    api_key=api_key,
                    start_date=chunk_start,
                    end_date=chunk_end,
                )
                totals["fixtures"] += outcome.fixtures_seen
                totals["fixtures_ingested"] += outcome.fixtures_ingested
                totals["fixture_rejections"] += outcome.fixture_rejections
                totals["terminal_results"] += outcome.terminal_results_seen
                totals["results_inserted"] += outcome.results_inserted
                totals["results_unchanged"] += outcome.results_unchanged
                totals["result_rejections"] += outcome.result_rejections
                print(
                    "HISTORY_REFRESH_CHUNK "
                    f"start={chunk_start.isoformat()} end={chunk_end.isoformat()} "
                    f"fixtures={outcome.fixtures_seen} ingested={outcome.fixtures_ingested} "
                    f"fixture_rejections={outcome.fixture_rejections} "
                    f"terminal={outcome.terminal_results_seen} inserted={outcome.results_inserted} "
                    f"unchanged={outcome.results_unchanged} result_rejections={outcome.result_rejections}",
                    flush=True,
                )
    except TransportError as exc:
        print(f"HISTORY_REFRESH_FAILED kind={exc.kind}", flush=True)
        return 2
    except Exception as exc:  # noqa: BLE001 - concise fail-closed CLI boundary
        print(f"HISTORY_REFRESH_FAILED kind={type(exc).__name__}", flush=True)
        return 2

    print(
        "HISTORY_REFRESH_SUCCESS "
        + " ".join(f"{key}={value}" for key, value in totals.items()),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
