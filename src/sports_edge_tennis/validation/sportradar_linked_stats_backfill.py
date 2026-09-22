from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable, Mapping

import psycopg

from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.live.sportradar_fixtures import (
    SOURCE_SYSTEM,
    SportradarFixtureCandidate,
    SportradarFixtureError,
    parse_summary,
)
from sports_edge_tennis.live.sportradar_history_refresh import (
    ParsedResult,
    ParsedStat,
    _invalidate_model_checkpoint,
    _latest_result_semantic_key,
    _orient_result_to_canonical,
    _parse_final_stats,
    _parse_terminal_result,
    _sportradar_player_map,
    _terminal_result_conflict,
    _validate_sportradar_player_map,
)
from sports_edge_tennis.live.target_fixtures import fetch_sportradar_fixture_rows


SOURCE_DATASET = "sportradar_linked_stats_backfill"
SOURCE_VERSION = "SPORTRADAR_LINKED_STATS_BACKFILL_V1"
MAX_DAYS = 14
ENABLED_ENV = "SPORTRADAR_STATS_BACKFILL_APPLY_ENABLED"
AUTH_ENV = "SPORTRADAR_STATS_BACKFILL_APPLY_AUTHORIZATION"
HASH_ENV = "SPORTRADAR_STATS_BACKFILL_ARTIFACT_SHA256"
AUTH_TOKEN = "SPORTRADAR_LINKED_STATS_BACKFILL_V1"

FetchRows = Callable[[str, date], tuple[datetime, list[dict[str, Any]]]]


@dataclass(frozen=True)
class BackfillCandidate:
    target_date: date
    received_at: datetime
    fixture: SportradarFixtureCandidate
    match_id: int
    player_1_id: int
    player_2_id: int
    result: ParsedResult
    stats: tuple[ParsedStat, ParsedStat]
    player_map: Mapping[str, int]

    @property
    def stat_hashes(self) -> tuple[str, str]:
        values = tuple(sorted(item.record_hash for item in self.stats))
        return values[0], values[1]


@dataclass(frozen=True)
class CandidateCollection:
    candidates: tuple[BackfillCandidate, ...]
    artifact_sha256: str
    counts: Mapping[str, int]
    rejection_reasons: Mapping[str, int]


def _enabled(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _validate_window(start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    days = (end_date - start_date).days + 1
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"backfill window must be 1..{MAX_DAYS} days")


def _dates(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _linked_match_context(
    conn: psycopg.Connection,
    *,
    event_id: str,
) -> tuple[int, int, int] | None:
    rows = conn.execute(
        """
        SELECT DISTINCT match_id
        FROM match_source_ids
        WHERE source_system = %s
          AND source_match_id = %s
        ORDER BY match_id
        """,
        (SOURCE_SYSTEM, event_id),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("sportradar_event_link_conflict")

    match_id = int(rows[0][0])
    version = conn.execute(
        """
        SELECT player_1_id, player_2_id
        FROM match_versions
        WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    if version is None:
        raise ValueError("linked_match_version_missing")
    return match_id, int(version[0]), int(version[1])


def _existing_stat_hashes(
    conn: psycopg.Connection,
    *,
    match_id: int,
    hashes: tuple[str, str],
) -> set[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT source_record_hash
        FROM match_stat_observations
        WHERE match_id = %s
          AND source_system = %s
          AND observation_kind = 'RAW_SOURCE'
          AND source_record_hash = ANY(%s)
        """,
        (match_id, SOURCE_SYSTEM, list(hashes)),
    ).fetchall()
    return {str(row[0]) for row in rows if row[0] is not None}


def _result_detail_conflict(
    current_result: tuple[object, ...] | None,
    candidate: ParsedResult,
) -> str | None:
    terminal = _terminal_result_conflict(current_result, candidate)
    if terminal is not None:
        return terminal
    if current_result is None:
        return "canonical_terminal_result_missing"

    labels = (
        ("sets_player_1", current_result[3], candidate.sets_won_player_1),
        ("sets_player_2", current_result[4], candidate.sets_won_player_2),
        ("games_player_1", current_result[5], candidate.games_won_player_1),
        ("games_player_2", current_result[6], candidate.games_won_player_2),
    )
    for label, current, incoming in labels:
        if current is not None and incoming is not None and int(current) != int(incoming):
            return f"terminal_score_conflict:{label}:{current}:{incoming}"
    return None


def _artifact_row(candidate: BackfillCandidate) -> dict[str, object]:
    return {
        "target_date": candidate.target_date.isoformat(),
        "event_id": candidate.fixture.source_match_id,
        "match_id": candidate.match_id,
        "canonical_player_1_id": candidate.player_1_id,
        "canonical_player_2_id": candidate.player_2_id,
        "provider_player_map": {
            key: int(value)
            for key, value in sorted(candidate.player_map.items())
        },
        "outcome_status": candidate.result.outcome_status,
        "winner_side": candidate.result.winner_side,
        "retired_side": candidate.result.retired_side,
        "sets_won_player_1": candidate.result.sets_won_player_1,
        "sets_won_player_2": candidate.result.sets_won_player_2,
        "games_won_player_1": candidate.result.games_won_player_1,
        "games_won_player_2": candidate.result.games_won_player_2,
        "stat_hashes": list(candidate.stat_hashes),
    }


def _artifact_hash(candidates: tuple[BackfillCandidate, ...]) -> str:
    rows = [_artifact_row(candidate) for candidate in candidates]
    payload = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _same_candidate(
    left: BackfillCandidate,
    right: BackfillCandidate,
) -> bool:
    left_row = _artifact_row(left).copy()
    right_row = _artifact_row(right).copy()
    left_row.pop("target_date", None)
    right_row.pop("target_date", None)
    return left_row == right_row


def collect_candidates(
    conn: psycopg.Connection,
    *,
    api_key: str,
    start_date: date,
    end_date: date,
    fetch_rows: FetchRows = fetch_sportradar_fixture_rows,
) -> CandidateCollection:
    _validate_window(start_date, end_date)
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    by_match: dict[int, BackfillCandidate] = {}

    for target_date in _dates(start_date, end_date):
        received_at, summaries = fetch_rows(api_key, target_date)
        if received_at.tzinfo is None:
            raise ValueError("Sportradar receive time must be timezone-aware")
        counts["provider_requests"] += 1

        for summary in summaries:
            counts["summaries_seen"] += 1
            try:
                fixture = parse_summary(summary, received_at=received_at)
            except SportradarFixtureError as exc:
                counts["fixture_parse_rejected"] += 1
                reasons[str(exc)] += 1
                continue

            if fixture.match_status != "COMPLETED":
                counts[f"skipped_{fixture.match_status.lower()}"] += 1
                continue
            counts["completed_seen"] += 1

            try:
                context = _linked_match_context(
                    conn,
                    event_id=fixture.source_match_id,
                )
            except ValueError as exc:
                counts["link_conflict"] += 1
                reasons[str(exc)] += 1
                continue
            if context is None:
                counts["completed_unlinked"] += 1
                continue

            match_id, player_1_id, player_2_id = context
            counts["completed_linked"] += 1

            try:
                result = _parse_terminal_result(summary, fixture=fixture)
                player_map = _validate_sportradar_player_map(
                    _sportradar_player_map(
                        conn,
                        provider_ids=(
                            fixture.player_1_source_id,
                            fixture.player_2_source_id,
                        ),
                    ),
                    expected_player_ids=(player_1_id, player_2_id),
                )
                result = _orient_result_to_canonical(
                    result,
                    fixture=fixture,
                    player_map=player_map,
                    canonical_player_ids=(player_1_id, player_2_id),
                )
            except ValueError as exc:
                counts["result_or_identity_rejected"] += 1
                reasons[str(exc)] += 1
                continue

            conflict = _result_detail_conflict(
                _latest_result_semantic_key(conn, match_id=match_id),
                result,
            )
            if conflict is not None:
                counts["terminal_truth_conflict"] += 1
                reasons[conflict] += 1
                continue

            try:
                stats = _parse_final_stats(summary, fixture=fixture)
                _validate_sportradar_player_map(
                    player_map,
                    expected_player_ids=(player_1_id, player_2_id),
                )
            except ValueError as exc:
                counts["final_stats_rejected"] += 1
                reasons[str(exc)] += 1
                continue

            candidate = BackfillCandidate(
                target_date=target_date,
                received_at=received_at.astimezone(UTC),
                fixture=fixture,
                match_id=match_id,
                player_1_id=player_1_id,
                player_2_id=player_2_id,
                result=result,
                stats=stats,
                player_map=dict(player_map),
            )

            existing = _existing_stat_hashes(
                conn,
                match_id=match_id,
                hashes=candidate.stat_hashes,
            )
            if len(existing) == 2:
                counts["already_backfilled"] += 1
                continue
            if existing:
                counts["partial_existing_stats"] += 1
                reasons["partial_existing_sportradar_stats"] += 1
                continue

            previous = by_match.get(match_id)
            if previous is not None:
                if _same_candidate(previous, candidate):
                    counts["duplicate_provider_candidate"] += 1
                    continue
                counts["duplicate_candidate_conflict"] += 1
                reasons["same_match_multiple_provider_candidates"] += 1
                by_match.pop(match_id, None)
                continue
            by_match[match_id] = candidate

    candidates = tuple(
        sorted(
            by_match.values(),
            key=lambda item: (
                item.target_date,
                item.match_id,
                item.fixture.source_match_id,
            ),
        )
    )
    counts["candidate_matches"] = len(candidates)
    counts["candidate_stat_rows"] = len(candidates) * 2
    return CandidateCollection(
        candidates=candidates,
        artifact_sha256=_artifact_hash(candidates),
        counts=dict(sorted(counts.items())),
        rejection_reasons=dict(sorted(reasons.items())),
    )


def preview_linked_stats_backfill(
    *,
    dsn: str,
    api_key: str,
    start_date: date,
    end_date: date,
    fetch_rows: FetchRows = fetch_sportradar_fixture_rows,
) -> dict[str, object]:
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("transaction_read_only is not on")
        collection = collect_candidates(
            conn,
            api_key=api_key,
            start_date=start_date,
            end_date=end_date,
            fetch_rows=fetch_rows,
        )
        conn.rollback()

    return {
        "status": "OK",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "artifact_sha256": collection.artifact_sha256,
        "counts": dict(collection.counts),
        "rejection_reasons": dict(collection.rejection_reasons),
        "transaction_read_only": "on",
        "writes": 0,
    }


def apply_linked_stats_backfill(
    *,
    dsn: str,
    api_key: str,
    start_date: date,
    end_date: date,
    env: Mapping[str, str] | None = None,
    fetch_rows: FetchRows = fetch_sportradar_fixture_rows,
) -> dict[str, object]:
    values = os.environ if env is None else env
    if not _enabled(values.get(ENABLED_ENV)):
        raise RuntimeError(f"{ENABLED_ENV} is not enabled")
    if str(values.get(AUTH_ENV) or "").strip() != AUTH_TOKEN:
        raise RuntimeError(f"{AUTH_ENV} authorization mismatch")
    reviewed_hash = str(values.get(HASH_ENV) or "").strip()
    if not reviewed_hash:
        raise RuntimeError(f"{HASH_ENV} is required")

    _validate_window(start_date, end_date)

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET LOCAL lock_timeout = '10s'")
        conn.execute("SET LOCAL statement_timeout = '180s'")
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext('sportradar_linked_stats_backfill_v1'))"
        )

        collection = collect_candidates(
            conn,
            api_key=api_key,
            start_date=start_date,
            end_date=end_date,
            fetch_rows=fetch_rows,
        )
        if collection.artifact_sha256 != reviewed_hash:
            raise RuntimeError(
                "reviewed artifact hash mismatch "
                f"expected={reviewed_hash} actual={collection.artifact_sha256}"
            )

        # Revalidate the exact linked identity, terminal truth, and absence of
        # equivalent provider stats immediately before any insert.
        writable: list[BackfillCandidate] = []
        existing_matches = 0
        for candidate in collection.candidates:
            context = _linked_match_context(
                conn,
                event_id=candidate.fixture.source_match_id,
            )
            if context != (
                candidate.match_id,
                candidate.player_1_id,
                candidate.player_2_id,
            ):
                raise RuntimeError(
                    f"linked match changed event={candidate.fixture.source_match_id}"
                )

            current = _latest_result_semantic_key(
                conn,
                match_id=candidate.match_id,
            )
            conflict = _result_detail_conflict(current, candidate.result)
            if conflict is not None:
                raise RuntimeError(
                    f"terminal truth changed match={candidate.match_id}: {conflict}"
                )

            current_map = _validate_sportradar_player_map(
                _sportradar_player_map(
                    conn,
                    provider_ids=(
                        candidate.fixture.player_1_source_id,
                        candidate.fixture.player_2_source_id,
                    ),
                ),
                expected_player_ids=(
                    candidate.player_1_id,
                    candidate.player_2_id,
                ),
            )
            if dict(current_map) != dict(candidate.player_map):
                raise RuntimeError(
                    f"player bridge changed match={candidate.match_id}"
                )

            existing = _existing_stat_hashes(
                conn,
                match_id=candidate.match_id,
                hashes=candidate.stat_hashes,
            )
            if len(existing) == 2:
                existing_matches += 1
                continue
            if existing:
                raise RuntimeError(
                    f"partial Sportradar stat state match={candidate.match_id}"
                )
            writable.append(candidate)

        if not writable:
            conn.rollback()
            return {
                "status": "NOOP",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "artifact_sha256": collection.artifact_sha256,
                "candidate_matches": len(collection.candidates),
                "match_existing": existing_matches,
                "result_inserted": 0,
                "stat_rows_inserted": 0,
                "player_inserted": 0,
                "match_inserted": 0,
                "source_identity_inserted": 0,
            }

        ingest_run_id = int(
            conn.execute(
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
                (
                    SOURCE_SYSTEM,
                    SOURCE_DATASET,
                    datetime.now(UTC),
                    SOURCE_VERSION,
                ),
            ).fetchone()[0]
        )
        repository = HistoricalRepository(conn, ingest_run_id)
        result_inserted = 0
        stat_inserted = 0

        try:
            for candidate in writable:
                inserted_result = repository.insert_match_result(
                    match_id=candidate.match_id,
                    outcome_status=candidate.result.outcome_status,
                    winner_side=candidate.result.winner_side,
                    retired_side=candidate.result.retired_side,
                    sets_won_player_1=candidate.result.sets_won_player_1,
                    sets_won_player_2=candidate.result.sets_won_player_2,
                    games_won_player_1=candidate.result.games_won_player_1,
                    games_won_player_2=candidate.result.games_won_player_2,
                    scoreline_text=candidate.result.scoreline_text,
                    source_observed_at=candidate.received_at,
                    as_of=candidate.received_at,
                )
                result_inserted += int(inserted_result)

                inserted_for_match = 0
                for stat in candidate.stats:
                    player_id = candidate.player_map.get(stat.provider_player_id)
                    if player_id is None:
                        raise RuntimeError(
                            f"player bridge missing at insert match={candidate.match_id}"
                        )
                    inserted = repository.insert_match_stat(
                        match_id=candidate.match_id,
                        player_id=int(player_id),
                        source_system=SOURCE_SYSTEM,
                        source_record_id=candidate.fixture.source_match_id,
                        source_record_hash=stat.record_hash,
                        line=stat.line,
                        source_observed_at=candidate.received_at,
                        as_of=candidate.received_at,
                    )
                    inserted_for_match += int(inserted)
                    stat_inserted += int(inserted)

                if inserted_for_match != 2:
                    raise RuntimeError(
                        "expected exactly two stat inserts "
                        f"match={candidate.match_id} actual={inserted_for_match}"
                    )

            _invalidate_model_checkpoint(
                conn,
                "AGENT_2_HIERARCHICAL_MARKOV",
            )
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
                (
                    datetime.now(UTC),
                    len(writable),
                    result_inserted + stat_inserted,
                    ingest_run_id,
                ),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(
                f"linked stats backfill rolled back: {type(exc).__name__}: {exc}"
            ) from exc

    return {
        "status": "COMPLETED",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "artifact_sha256": collection.artifact_sha256,
        "candidate_matches": len(collection.candidates),
        "match_existing": existing_matches,
        "result_inserted": result_inserted,
        "stat_rows_inserted": stat_inserted,
        "player_inserted": 0,
        "match_inserted": 0,
        "source_identity_inserted": 0,
    }


__all__ = [
    "AUTH_ENV",
    "AUTH_TOKEN",
    "ENABLED_ENV",
    "HASH_ENV",
    "MAX_DAYS",
    "apply_linked_stats_backfill",
    "collect_candidates",
    "preview_linked_stats_backfill",
]
