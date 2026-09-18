"""Stats-only historical API-Tennis backfill for Agent 2 coverage.

This lane intentionally does *not* reuse the normal history fixture/result ingest. It
maps provider event/player ids onto already-existing canonical matches and appends only
serve/return match statistics accepted by the same strict parser used by the daily
refresh.

Safety contract:
* only terminal API-Tennis fixtures are considered;
* the event must already map to exactly one canonical match;
* both provider player ids must already map to the same two players on that match;
* no match, match-version, result, player, alias, or source-id row is created/changed;
* stats visibility is the actual backfill receive time, never the historical match date;
* semantic reruns are deduped by ``persist_api_tennis_match_stats``;
* dry-run mode measures strict eligibility without any database mutation;
* Agent 2's cached snapshot is invalidated only when new stat rows are inserted.
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from typing import Any, Callable, Iterable, Mapping

import psycopg

from sports_edge_tennis.data.sources.api_tennis_live import ApiTennisIngestedMatch
from sports_edge_tennis.live.api_tennis_match_stats import (
    StatsPersistOutcome,
    _parse_api_tennis_match_point_stats_with_reason,
    persist_api_tennis_match_stats,
)
from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.history_refresh import (
    MAX_WINDOW_DAYS,
    fetch_api_tennis_fixture_window,
)
from sports_edge_tennis.runner.live_model_cache import AGENT2_CACHE_KEY


DEFAULT_CHUNK_DAYS = 7
MAX_CHUNK_DAYS = 31
_TERMINAL_STATUSES = {"finished", "completed", "retired", "walkover"}

FetchWindow = Callable[[str, date, date], tuple[Any, list[dict[str, Any]]]]


@dataclass(frozen=True)
class StatsBackfillChunkOutcome:
    start_date: date
    end_date: date
    dry_run: bool
    fixtures_seen: int
    terminal_fixtures: int
    matched_existing: int
    unmatched_event: int
    missing_player_identity: int
    identity_mismatch: int
    eligible_stat_matches: int
    inserted_stat_rows: int
    existing_stat_rows: int
    skipped_stat_matches: int
    skip_reason_summary: str


@dataclass
class StatsBackfillTotals:
    chunks: int = 0
    fixtures_seen: int = 0
    terminal_fixtures: int = 0
    matched_existing: int = 0
    unmatched_event: int = 0
    missing_player_identity: int = 0
    identity_mismatch: int = 0
    eligible_stat_matches: int = 0
    inserted_stat_rows: int = 0
    existing_stat_rows: int = 0
    skipped_stat_matches: int = 0

    def add(self, outcome: StatsBackfillChunkOutcome) -> None:
        self.chunks += 1
        self.fixtures_seen += outcome.fixtures_seen
        self.terminal_fixtures += outcome.terminal_fixtures
        self.matched_existing += outcome.matched_existing
        self.unmatched_event += outcome.unmatched_event
        self.missing_player_identity += outcome.missing_player_identity
        self.identity_mismatch += outcome.identity_mismatch
        self.eligible_stat_matches += outcome.eligible_stat_matches
        self.inserted_stat_rows += outcome.inserted_stat_rows
        self.existing_stat_rows += outcome.existing_stat_rows
        self.skipped_stat_matches += outcome.skipped_stat_matches


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _terminal_fixture(fixture: Mapping[str, Any]) -> bool:
    return _normalized(fixture.get("event_status")) in _TERMINAL_STATUSES


def _source_player_map(
    conn: psycopg.Connection,
    source_player_ids: Iterable[str],
) -> dict[str, int]:
    ids = tuple(sorted({value for value in source_player_ids if value}))
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT source_player_id, player_id
        FROM player_source_ids
        WHERE source_system = 'API_TENNIS'
          AND source_player_id = ANY(%s)
        ORDER BY source_player_id, player_id
        """,
        (list(ids),),
    ).fetchall()
    result: dict[str, int] = {}
    ambiguous: set[str] = set()
    for source_player_id, player_id in rows:
        key = str(source_player_id)
        value = int(player_id)
        if key in result and result[key] != value:
            ambiguous.add(key)
        else:
            result[key] = value
    for key in ambiguous:
        result.pop(key, None)
    return result


def _existing_match_map(
    conn: psycopg.Connection,
    source_match_ids: Iterable[str],
) -> dict[str, tuple[int, int, int, int, str]]:
    ids = tuple(sorted({value for value in source_match_ids if value}))
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT DISTINCT ON (msi.source_match_id)
            msi.source_match_id,
            msi.match_id,
            latest.match_version_id,
            latest.player_1_id,
            latest.player_2_id,
            latest.match_status::text
        FROM match_source_ids msi
        JOIN LATERAL (
            SELECT
                mv.match_version_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_status
            FROM match_versions mv
            WHERE mv.match_id = msi.match_id
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
            LIMIT 1
        ) latest ON TRUE
        WHERE msi.source_system = 'API_TENNIS'
          AND msi.source_match_id = ANY(%s)
        ORDER BY msi.source_match_id, msi.match_source_id DESC
        """,
        (list(ids),),
    ).fetchall()
    return {
        str(source_match_id): (
            int(match_id),
            int(match_version_id),
            int(player_1_id),
            int(player_2_id),
            str(match_status),
        )
        for (
            source_match_id,
            match_id,
            match_version_id,
            player_1_id,
            player_2_id,
            match_status,
        ) in rows
    }


def _load_existing_ingested_matches(
    conn: psycopg.Connection,
    fixtures: list[dict[str, Any]],
) -> tuple[tuple[ApiTennisIngestedMatch, ...], int, int, int, int]:
    terminal = [fixture for fixture in fixtures if _terminal_fixture(fixture)]
    event_ids = [_text(fixture.get("event_key")) for fixture in terminal]
    player_ids = [
        _text(fixture.get(field))
        for fixture in terminal
        for field in ("first_player_key", "second_player_key")
    ]
    match_map = _existing_match_map(conn, event_ids)
    player_map = _source_player_map(conn, player_ids)

    matched: list[ApiTennisIngestedMatch] = []
    unmatched_event = 0
    missing_player_identity = 0
    identity_mismatch = 0

    for fixture in terminal:
        event_key = _text(fixture.get("event_key"))
        first_key = _text(fixture.get("first_player_key"))
        second_key = _text(fixture.get("second_player_key"))
        if not event_key or event_key not in match_map:
            unmatched_event += 1
            continue
        if (
            not first_key
            or not second_key
            or first_key == second_key
            or first_key not in player_map
            or second_key not in player_map
        ):
            missing_player_identity += 1
            continue

        match_id, match_version_id, current_p1, current_p2, match_status = match_map[event_key]
        source_p1 = player_map[first_key]
        source_p2 = player_map[second_key]
        if source_p1 == source_p2 or {source_p1, source_p2} != {current_p1, current_p2}:
            identity_mismatch += 1
            continue

        matched.append(
            ApiTennisIngestedMatch(
                source_match_id=event_key,
                match_id=match_id,
                match_version_id=match_version_id,
                player_1_id=source_p1,
                player_2_id=source_p2,
                match_status=match_status,
            )
        )

    return (
        tuple(matched),
        len(terminal),
        unmatched_event,
        missing_player_identity,
        identity_mismatch,
    )


def _preview_stat_outcome(
    fixtures: list[dict[str, Any]],
    existing_matches: tuple[ApiTennisIngestedMatch, ...],
) -> StatsPersistOutcome:
    by_event = {
        _text(fixture.get("event_key")): fixture
        for fixture in fixtures
        if _text(fixture.get("event_key"))
    }
    eligible = 0
    skipped = 0
    reasons: dict[str, int] = {}
    for existing in existing_matches:
        fixture = by_event.get(existing.source_match_id)
        if fixture is None:
            skipped += 1
            reasons["MISSING_INGESTED_FIXTURE"] = reasons.get("MISSING_INGESTED_FIXTURE", 0) + 1
            continue
        parsed, reason = _parse_api_tennis_match_point_stats_with_reason(fixture)
        if parsed is None:
            skipped += 1
            key = reason or "UNKNOWN_PARSE_GAP"
            reasons[key] = reasons.get(key, 0) + 1
            continue
        eligible += 1
    frozen = tuple(sorted((reason, count) for reason, count in reasons.items() if count > 0))
    return StatsPersistOutcome(
        eligible_matches=eligible,
        inserted_rows=0,
        existing_rows=0,
        skipped_matches=skipped,
        skip_reason_counts=frozen,
    )


def _invalidate_agent2_cache(conn: psycopg.Connection) -> None:
    table_exists = conn.execute(
        "SELECT to_regclass('public.live_model_state_checkpoints')"
    ).fetchone()[0]
    if table_exists is None:
        return
    conn.execute(
        "DELETE FROM live_model_state_checkpoints WHERE checkpoint_key = %s",
        (AGENT2_CACHE_KEY,),
    )


def backfill_stats_chunk(
    conn: psycopg.Connection,
    *,
    api_key: str,
    start_date: date,
    end_date: date,
    dry_run: bool = False,
    fetch_window: FetchWindow = fetch_api_tennis_fixture_window,
) -> StatsBackfillChunkOutcome:
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    if (end_date - start_date).days + 1 > MAX_CHUNK_DAYS:
        raise ValueError(f"chunk may not exceed {MAX_CHUNK_DAYS} days")

    stage_started = time.monotonic()
    print(
        "A2_STATS_BACKFILL_STAGE "
        f"start={start_date.isoformat()} end={end_date.isoformat()} stage=FETCH_START",
        flush=True,
    )
    received_at, fixtures = fetch_window(api_key, start_date, end_date)
    print(
        "A2_STATS_BACKFILL_STAGE "
        f"start={start_date.isoformat()} end={end_date.isoformat()} "
        f"stage=FETCH_DONE fixtures={len(fixtures)} "
        f"elapsed_ms={int((time.monotonic() - stage_started) * 1000)}",
        flush=True,
    )

    stage_started = time.monotonic()
    print(
        "A2_STATS_BACKFILL_STAGE "
        f"start={start_date.isoformat()} end={end_date.isoformat()} stage=LINK_START",
        flush=True,
    )
    (
        existing_matches,
        terminal_fixtures,
        unmatched_event,
        missing_player_identity,
        identity_mismatch,
    ) = _load_existing_ingested_matches(conn, fixtures)
    print(
        "A2_STATS_BACKFILL_STAGE "
        f"start={start_date.isoformat()} end={end_date.isoformat()} "
        f"stage=LINK_DONE terminal={terminal_fixtures} matched={len(existing_matches)} "
        f"elapsed_ms={int((time.monotonic() - stage_started) * 1000)}",
        flush=True,
    )

    stage_started = time.monotonic()
    print(
        "A2_STATS_BACKFILL_STAGE "
        f"start={start_date.isoformat()} end={end_date.isoformat()} "
        f"stage={'PREVIEW_START' if dry_run else 'PERSIST_START'}",
        flush=True,
    )
    if dry_run:
        stat_outcome = _preview_stat_outcome(fixtures, existing_matches)
    else:
        stat_outcome = persist_api_tennis_match_stats(
            conn,
            fixtures=fixtures,
            ingested_matches=existing_matches,
            received_at=received_at,
        )
        if stat_outcome.inserted_rows:
            _invalidate_agent2_cache(conn)

    print(
        "A2_STATS_BACKFILL_STAGE "
        f"start={start_date.isoformat()} end={end_date.isoformat()} "
        f"stage={'PREVIEW_DONE' if dry_run else 'PERSIST_DONE'} "
        f"eligible={stat_outcome.eligible_matches} skipped={stat_outcome.skipped_matches} "
        f"elapsed_ms={int((time.monotonic() - stage_started) * 1000)}",
        flush=True,
    )

    outcome = StatsBackfillChunkOutcome(
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
        fixtures_seen=len(fixtures),
        terminal_fixtures=terminal_fixtures,
        matched_existing=len(existing_matches),
        unmatched_event=unmatched_event,
        missing_player_identity=missing_player_identity,
        identity_mismatch=identity_mismatch,
        eligible_stat_matches=stat_outcome.eligible_matches,
        inserted_stat_rows=stat_outcome.inserted_rows,
        existing_stat_rows=stat_outcome.existing_rows,
        skipped_stat_matches=stat_outcome.skipped_matches,
        skip_reason_summary=stat_outcome.skip_reason_summary,
    )
    print(
        "A2_STATS_BACKFILL_CHUNK "
        f"start={start_date.isoformat()} end={end_date.isoformat()} dry_run={int(dry_run)} "
        f"fixtures_seen={outcome.fixtures_seen} terminal={outcome.terminal_fixtures} "
        f"matched_existing={outcome.matched_existing} unmatched_event={outcome.unmatched_event} "
        f"missing_player_identity={outcome.missing_player_identity} "
        f"identity_mismatch={outcome.identity_mismatch} "
        f"eligible_stats={outcome.eligible_stat_matches} "
        f"inserted_rows={outcome.inserted_stat_rows} existing_rows={outcome.existing_stat_rows} "
        f"skipped_stats={outcome.skipped_stat_matches} "
        f"skip_reasons={outcome.skip_reason_summary}",
        flush=True,
    )
    return outcome


def backfill_stats_range(
    conn: psycopg.Connection,
    *,
    api_key: str,
    start_date: date,
    end_date: date,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    dry_run: bool = False,
    fetch_window: FetchWindow = fetch_api_tennis_fixture_window,
) -> StatsBackfillTotals:
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    if not 1 <= chunk_days <= MAX_CHUNK_DAYS:
        raise ValueError(f"chunk_days must be between 1 and {MAX_CHUNK_DAYS}")
    if (end_date - start_date).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError(f"window may not exceed {MAX_WINDOW_DAYS} days")

    totals = StatsBackfillTotals()
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(end_date, cursor + timedelta(days=chunk_days - 1))
        totals.add(
            backfill_stats_chunk(
                conn,
                api_key=api_key,
                start_date=cursor,
                end_date=chunk_end,
                dry_run=dry_run,
                fetch_window=fetch_window,
            )
        )
        cursor = chunk_end + timedelta(days=1)

    print(
        "A2_STATS_BACKFILL_SUMMARY "
        f"dry_run={int(dry_run)} chunks={totals.chunks} fixtures_seen={totals.fixtures_seen} "
        f"terminal={totals.terminal_fixtures} matched_existing={totals.matched_existing} "
        f"unmatched_event={totals.unmatched_event} "
        f"missing_player_identity={totals.missing_player_identity} "
        f"identity_mismatch={totals.identity_mismatch} "
        f"eligible_stats={totals.eligible_stat_matches} "
        f"inserted_rows={totals.inserted_stat_rows} existing_rows={totals.existing_stat_rows} "
        f"skipped_stats={totals.skipped_stat_matches}",
        flush=True,
    )
    return totals


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stats-only API-Tennis Agent 2 historical backfill")
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dsn", default=_db_dsn())
    args = parser.parse_args(argv)

    if not args.dsn:
        print("A2_STATS_BACKFILL_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    try:
        api_key = load_secret("API_TENNIS_API_KEY")
    except MissingCredentialError:
        api_key = None
    if not api_key:
        print("A2_STATS_BACKFILL_BLOCKED reason=NO_API_TENNIS_KEY", flush=True)
        return 2

    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            backfill_stats_range(
                conn,
                api_key=api_key,
                start_date=args.start_date,
                end_date=args.end_date,
                chunk_days=args.chunk_days,
                dry_run=args.dry_run,
            )
    except (ValueError, psycopg.Error) as exc:
        print(f"A2_STATS_BACKFILL_BLOCKED reason={type(exc).__name__}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
