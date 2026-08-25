from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg

from .contracts import (
    IngestCounters,
    OddsPriceRecord,
    QualityAccumulator,
    SackmannMatchRecord,
    SourceDocument,
    UnmatchedRecord,
)
from .repository import HistoricalRepository
from .sources.sackmann import parse_sackmann_csv
from .sources.tennis_data import parse_tennis_data_file


def start_ingest_run(
    conn: psycopg.Connection,
    *,
    source_system: str,
    source_dataset: str,
    document: SourceDocument,
) -> int:
    return conn.execute(
        """
        INSERT INTO ingest_runs (
            source_system,
            source_dataset,
            started_at,
            status,
            source_version,
            source_file_name,
            source_file_hash
        )
        VALUES (%s, %s, %s, 'RUNNING', %s, %s, %s)
        RETURNING ingest_run_id
        """,
        (
            source_system,
            source_dataset,
            datetime.now(timezone.utc),
            document.source_revision,
            document.filename,
            document.sha256,
        ),
    ).fetchone()[0]


def finish_ingest_run(
    conn: psycopg.Connection,
    *,
    ingest_run_id: int,
    counters: IngestCounters,
    status: str,
    error_summary: list[dict[str, object]],
) -> None:
    conn.execute(
        """
        UPDATE ingest_runs
        SET finished_at = %s,
            status = %s,
            rows_seen = %s,
            rows_inserted = %s,
            rows_updated = %s,
            rows_rejected = %s,
            rows_unmatched = %s,
            error_summary = %s::jsonb
        WHERE ingest_run_id = %s
        """,
        (
            datetime.now(timezone.utc),
            status,
            counters.rows_seen,
            counters.rows_inserted,
            counters.rows_updated,
            counters.rows_rejected,
            counters.rows_unmatched,
            json.dumps(error_summary),
            ingest_run_id,
        ),
    )


def _score_sets(scoreline_text: str | None) -> tuple[int | None, int | None, int | None, int | None]:
    if not scoreline_text:
        return None, None, None, None
    sets_1 = sets_2 = games_1 = games_2 = 0
    for token in scoreline_text.split():
        if not token or "-" not in token:
            continue
        left, right = token.split("-", 1)
        left = "".join(ch for ch in left if ch.isdigit())
        right = "".join(ch for ch in right if ch.isdigit())
        if not left or not right:
            continue
        left_value = int(left)
        right_value = int(right)
        games_1 += left_value
        games_2 += right_value
        if left_value > right_value:
            sets_1 += 1
        elif right_value > left_value:
            sets_2 += 1
    return sets_1, sets_2, games_1, games_2


def _orient_players(winner_player_id: int, loser_player_id: int) -> tuple[int, int, int]:
    player_1_id, player_2_id = sorted((winner_player_id, loser_player_id))
    winner_side = 1 if winner_player_id == player_1_id else 2
    return player_1_id, player_2_id, winner_side


def _serialize_counters(
    counters: IngestCounters,
    *,
    identity_resolution_status: str,
) -> dict[str, object]:
    payload = asdict(counters)
    if identity_resolution_status == "NOT_EXECUTED":
        for key in ("rows_unmatched", "resolved_players", "unmatched_players", "ambiguous_players"):
            payload[key] = None
    return payload


def write_reports(
    *,
    quality_path: Path,
    unmatched_path: Path,
    counters: IngestCounters,
    quality: QualityAccumulator,
    unmatched: list[UnmatchedRecord],
    latest_observation_at: datetime | None,
    identity_resolution_status: str,
) -> None:
    serialized_counters = _serialize_counters(
        counters,
        identity_resolution_status=identity_resolution_status,
    )
    quality_payload = {
        **serialized_counters,
        "source_files": quality.source_files,
        "coverage_by_year": quality.years,
        "coverage_by_tour_tier": quality.tour_tiers,
        "coverage_by_surface": quality.surfaces,
        "coverage_by_bookmaker": quality.bookmakers,
        "identity_resolution_status": identity_resolution_status,
        "latest_observation_at": (
            latest_observation_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if latest_observation_at else None
        ),
        "known_gaps": quality.known_gaps,
    }
    quality_path.parent.mkdir(parents=True, exist_ok=True)
    quality_path.write_text(json.dumps(quality_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    unmatched_payload = [
        {
            "source": entry.source,
            "source_record": entry.source_record,
            "raw_player_names": entry.raw_player_names,
            "raw_tournament": entry.raw_tournament,
            "event_date": entry.event_date,
            "reason": entry.reason,
            "candidate_ids": entry.candidate_ids,
        }
        for entry in unmatched
    ]
    unmatched_path.parent.mkdir(parents=True, exist_ok=True)
    unmatched_path.write_text(json.dumps(unmatched_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ingest_sackmann_document(
    conn: psycopg.Connection,
    *,
    document: SourceDocument,
    dry_run: bool,
    quality_path: Path | None = None,
    unmatched_path: Path | None = None,
) -> dict[str, object]:
    parse_outcome = parse_sackmann_csv(document)
    counters = IngestCounters(rows_seen=len(parse_outcome.records) + len(parse_outcome.rejected_rows))
    counters.rows_rejected = len(parse_outcome.rejected_rows)
    quality = QualityAccumulator(source_files=[document.filename])
    unmatched: list[UnmatchedRecord] = []
    latest_observation_at: datetime | None = None
    error_summary = list(parse_outcome.rejected_rows)

    if dry_run:
        for record in parse_outcome.records:
            quality.increment(quality.years, str(record.season_year))
            quality.increment(quality.tour_tiers, record.competition_tier)
            quality.increment(quality.surfaces, record.surface)
            for limitation in record.limitations:
                if limitation not in quality.known_gaps:
                    quality.known_gaps.append(limitation)
        if quality_path and unmatched_path:
            write_reports(
                quality_path=quality_path,
                unmatched_path=unmatched_path,
                counters=counters,
                quality=quality,
                unmatched=unmatched,
                latest_observation_at=latest_observation_at,
                identity_resolution_status="NOT_EXECUTED",
            )
        return {
            "status": "DRY_RUN",
            "identity_resolution_status": "NOT_EXECUTED",
            "counters": _serialize_counters(
                counters,
                identity_resolution_status="NOT_EXECUTED",
            ),
            "errors": error_summary,
        }

    ingest_run_id = start_ingest_run(
        conn,
        source_system=document.source_system,
        source_dataset=document.dataset_type,
        document=document,
    )
    repository = HistoricalRepository(conn, ingest_run_id)
    try:
        with conn.transaction():
            for record in parse_outcome.records:
                quality.increment(quality.years, str(record.season_year))
                quality.increment(quality.tour_tiers, record.competition_tier)
                quality.increment(quality.surfaces, record.surface)
                for limitation in record.limitations:
                    if limitation not in quality.known_gaps:
                        quality.known_gaps.append(limitation)

                winner_resolution = repository.resolve_player(
                    source_system=document.source_system,
                    raw_name=record.winner.raw_name,
                    source_record_id=record.source_match_id or record.source_record_hash,
                    context_tournament_name=record.tournament_name,
                    context_match_start_at=record.match_start_at,
                    source_observed_at=record.match_source_observed_at,
                    as_of=record.match_as_of,
                    stable_source_player_id=record.winner.source_player_id,
                    create_if_missing_with_stable_id=True,
                )
                loser_resolution = repository.resolve_player(
                    source_system=document.source_system,
                    raw_name=record.loser.raw_name,
                    source_record_id=record.source_match_id or record.source_record_hash,
                    context_tournament_name=record.tournament_name,
                    context_match_start_at=record.match_start_at,
                    source_observed_at=record.match_source_observed_at,
                    as_of=record.match_as_of,
                    stable_source_player_id=record.loser.source_player_id,
                    create_if_missing_with_stable_id=True,
                )
                for resolution in (winner_resolution, loser_resolution):
                    if resolution.status == "RESOLVED":
                        counters.resolved_players += 1
                    elif resolution.status == "UNMATCHED":
                        counters.unmatched_players += 1
                    else:
                        counters.ambiguous_players += 1

                if winner_resolution.status != "RESOLVED" or loser_resolution.status != "RESOLVED":
                    counters.rows_unmatched += 1
                    unmatched.append(
                        UnmatchedRecord(
                            source=document.source_system,
                            source_record=record.source_record_locator,
                            raw_player_names=[record.winner.raw_name, record.loser.raw_name],
                            raw_tournament=record.tournament_name,
                            event_date=record.match_start_at.date().isoformat(),
                            reason="player_identity_resolution_failed",
                            candidate_ids=list(
                                winner_resolution.candidate_player_ids + loser_resolution.candidate_player_ids
                            ),
                        )
                    )
                    continue

                player_1_id, player_2_id, winner_side = _orient_players(
                    winner_resolution.player_id,
                    loser_resolution.player_id,
                )
                tournament_id = repository.get_or_create_tournament(
                    source_system=document.source_system,
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
                )
                locator = repository.upsert_match_version(
                    tournament_edition_id=edition_id,
                    source_system=document.source_system,
                    source_match_id=record.source_match_id,
                    source_record_hash=record.source_record_hash,
                    source_record_locator=record.source_record_locator,
                    source_file_name=document.filename,
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
                    match_status="COMPLETED" if record.outcome_status == "COMPLETED" else "SCHEDULED",
                    source_observed_at=record.match_source_observed_at,
                    as_of=record.match_as_of,
                )
                counters.matches_normalized += 1
                counters.rows_inserted += 1

                if latest_observation_at is None or record.result_source_observed_at > latest_observation_at:
                    latest_observation_at = record.result_source_observed_at

                sets_1, sets_2, games_1, games_2 = _score_sets(record.scoreline_text)
                if repository.insert_match_result(
                    match_id=locator.match_id,
                    outcome_status=record.outcome_status,
                    winner_side=winner_side if record.outcome_status != "CANCELLED" else None,
                    retired_side=winner_side if record.outcome_status == "RETIRED" else None,
                    sets_won_player_1=sets_1,
                    sets_won_player_2=sets_2,
                    games_won_player_1=games_1,
                    games_won_player_2=games_2,
                    scoreline_text=record.scoreline_text,
                    source_observed_at=record.result_source_observed_at,
                    as_of=record.result_as_of,
                ):
                    counters.result_rows += 1
                else:
                    counters.duplicate_rows += 1

                stats_by_player = {
                    winner_resolution.player_id: record.winner_stats,
                    loser_resolution.player_id: record.loser_stats,
                }
                for player_id, stat_line in stats_by_player.items():
                    if repository.insert_match_stat(
                        match_id=locator.match_id,
                        player_id=player_id,
                        source_system=document.source_system,
                        source_record_id=record.source_match_id,
                        source_record_hash=record.source_record_hash,
                        line=stat_line,
                        source_observed_at=record.result_source_observed_at,
                        as_of=record.result_as_of,
                    ):
                        counters.stat_rows += 1
                    else:
                        counters.duplicate_rows += 1

            finish_ingest_run(
                conn,
                ingest_run_id=ingest_run_id,
                counters=counters,
                status="COMPLETED" if not unmatched and not parse_outcome.rejected_rows else "PARTIAL",
                error_summary=error_summary,
            )
            repository.insert_health_check(
                source_system=document.source_system,
                dataset_name=document.dataset_type,
                latest_observation_at=latest_observation_at,
                coverage_ratio=None,
                unmatched_count=counters.rows_unmatched,
                error_count=counters.rows_rejected,
                status="WARNING" if unmatched or parse_outcome.rejected_rows else "HEALTHY",
                notes="Historical match ingest quality summary.",
            )
    except Exception:
        finish_ingest_run(
            conn,
            ingest_run_id=ingest_run_id,
            counters=counters,
            status="FAILED",
            error_summary=error_summary,
        )
        raise

    if quality_path and unmatched_path:
        write_reports(
            quality_path=quality_path,
            unmatched_path=unmatched_path,
            counters=counters,
            quality=quality,
            unmatched=unmatched,
            latest_observation_at=latest_observation_at,
            identity_resolution_status="EXECUTED",
        )
    return {
        "status": "COMPLETED",
        "identity_resolution_status": "EXECUTED",
        "counters": _serialize_counters(
            counters,
            identity_resolution_status="EXECUTED",
        ),
        "errors": error_summary,
    }


def ingest_tennis_data_document(
    conn: psycopg.Connection,
    *,
    document: SourceDocument,
    dry_run: bool,
    quality_path: Path | None = None,
    unmatched_path: Path | None = None,
) -> dict[str, object]:
    parse_outcome = parse_tennis_data_file(document)
    counters = IngestCounters(rows_seen=len(parse_outcome.records) + len(parse_outcome.rejected_rows))
    counters.rows_rejected = len(parse_outcome.rejected_rows)
    counters.invalid_prices = len(
        [row for row in parse_outcome.rejected_rows if row.get("reason") == "invalid_decimal_price"]
    )
    quality = QualityAccumulator(source_files=[document.filename])
    unmatched: list[UnmatchedRecord] = []
    latest_observation_at: datetime | None = None
    error_summary = list(parse_outcome.rejected_rows)

    if dry_run:
        for record in parse_outcome.records:
            counters.odds_candidate_rows += 1
            quality.increment(quality.years, str(record.event_date.year))
            quality.increment(quality.surfaces, record.surface)
            quality.increment(quality.bookmakers, record.bookmaker_code)
            for limitation in record.limitations:
                if limitation not in quality.known_gaps:
                    quality.known_gaps.append(limitation)
        if quality_path and unmatched_path:
            write_reports(
                quality_path=quality_path,
                unmatched_path=unmatched_path,
                counters=counters,
                quality=quality,
                unmatched=unmatched,
                latest_observation_at=latest_observation_at,
                identity_resolution_status="NOT_EXECUTED",
            )
        return {
            "status": "DRY_RUN",
            "identity_resolution_status": "NOT_EXECUTED",
            "counters": _serialize_counters(
                counters,
                identity_resolution_status="NOT_EXECUTED",
            ),
            "errors": error_summary,
        }

    ingest_run_id = start_ingest_run(
        conn,
        source_system=document.source_system,
        source_dataset=document.dataset_type,
        document=document,
    )
    repository = HistoricalRepository(conn, ingest_run_id)
    try:
        with conn.transaction():
            for record in parse_outcome.records:
                counters.odds_candidate_rows += 1
                quality.increment(quality.years, str(record.event_date.year))
                quality.increment(quality.surfaces, record.surface)
                quality.increment(quality.bookmakers, record.bookmaker_code)
                for limitation in record.limitations:
                    if limitation not in quality.known_gaps:
                        quality.known_gaps.append(limitation)

                winner_resolution = repository.resolve_player(
                    source_system=document.source_system,
                    raw_name=record.winner_name,
                    source_record_id=record.source_snapshot_id,
                    context_tournament_name=record.tournament_name,
                    context_match_start_at=None,
                    source_observed_at=record.source_observed_at,
                    as_of=record.as_of,
                )
                loser_resolution = repository.resolve_player(
                    source_system=document.source_system,
                    raw_name=record.loser_name,
                    source_record_id=record.source_snapshot_id,
                    context_tournament_name=record.tournament_name,
                    context_match_start_at=None,
                    source_observed_at=record.source_observed_at,
                    as_of=record.as_of,
                )
                if winner_resolution.status != "RESOLVED" or loser_resolution.status != "RESOLVED":
                    counters.rows_unmatched += 1
                    if winner_resolution.status == "AMBIGUOUS" or loser_resolution.status == "AMBIGUOUS":
                        counters.odds_ambiguous_rows += 1
                    unmatched.append(
                        UnmatchedRecord(
                            source=document.source_system,
                            source_record=record.source_record_locator,
                            raw_player_names=[record.winner_name, record.loser_name],
                            raw_tournament=record.tournament_name,
                            event_date=record.event_date.isoformat(),
                            reason="odds_player_identity_resolution_failed",
                            candidate_ids=list(
                                winner_resolution.candidate_player_ids + loser_resolution.candidate_player_ids
                            ),
                        )
                    )
                    continue

                matches = repository.find_match_for_odds(
                    tournament_name=record.tournament_name,
                    event_date=record.event_date,
                    player_a_id=winner_resolution.player_id,
                    player_b_id=loser_resolution.player_id,
                )
                if len(matches) != 1:
                    counters.rows_unmatched += 1
                    if len(matches) > 1:
                        counters.odds_ambiguous_rows += 1
                    unmatched.append(
                        UnmatchedRecord(
                            source=document.source_system,
                            source_record=record.source_record_locator,
                            raw_player_names=[record.winner_name, record.loser_name],
                            raw_tournament=record.tournament_name,
                            event_date=record.event_date.isoformat(),
                            reason="odds_match_resolution_failed",
                            candidate_ids=[row[0] for row in matches],
                        )
                    )
                    continue

                match_id, player_1_id, player_2_id = matches[0]
                if record.selection_name == record.winner_name:
                    selection_player_id = winner_resolution.player_id
                else:
                    selection_player_id = loser_resolution.player_id
                selection_type = "PLAYER_1" if selection_player_id == player_1_id else "PLAYER_2"
                if repository.insert_odds_snapshot(
                    match_id=match_id,
                    bookmaker_code=record.bookmaker_code,
                    selection_type=selection_type,
                    decimal_price=record.decimal_price,
                    source_snapshot_id=record.source_snapshot_id,
                    source_record_hash=record.source_record_hash,
                    source_observed_at=record.source_observed_at,
                    as_of=record.as_of,
                ):
                    counters.rows_inserted += 1
                    counters.odds_rows += 1
                    counters.odds_matched_rows += 1
                else:
                    counters.duplicate_rows += 1

                if latest_observation_at is None or record.source_observed_at > latest_observation_at:
                    latest_observation_at = record.source_observed_at

            ratio = None
            if counters.odds_candidate_rows:
                ratio = Decimal(counters.odds_matched_rows) / Decimal(counters.odds_candidate_rows)
            finish_ingest_run(
                conn,
                ingest_run_id=ingest_run_id,
                counters=counters,
                status="COMPLETED" if not unmatched and not parse_outcome.rejected_rows else "PARTIAL",
                error_summary=error_summary,
            )
            repository.insert_health_check(
                source_system=document.source_system,
                dataset_name=document.dataset_type,
                latest_observation_at=latest_observation_at,
                coverage_ratio=ratio,
                unmatched_count=counters.rows_unmatched,
                error_count=counters.rows_rejected,
                status="WARNING" if unmatched or parse_outcome.rejected_rows else "HEALTHY",
                notes="Historical odds ingest quality summary.",
            )
    except Exception:
        finish_ingest_run(
            conn,
            ingest_run_id=ingest_run_id,
            counters=counters,
            status="FAILED",
            error_summary=error_summary,
        )
        raise

    if quality_path and unmatched_path:
        write_reports(
            quality_path=quality_path,
            unmatched_path=unmatched_path,
            counters=counters,
            quality=quality,
            unmatched=unmatched,
            latest_observation_at=latest_observation_at,
            identity_resolution_status="EXECUTED",
        )
    return {
        "status": "COMPLETED",
        "identity_resolution_status": "EXECUTED",
        "counters": _serialize_counters(
            counters,
            identity_resolution_status="EXECUTED",
        ),
        "errors": error_summary,
    }
