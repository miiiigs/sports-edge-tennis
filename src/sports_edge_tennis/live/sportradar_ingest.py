from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

import psycopg

from sports_edge_tennis.data.hashing import source_hash
from sports_edge_tennis.data.repository import HistoricalRepository, MatchLocator
from sports_edge_tennis.live.sportradar_fixtures import (
    ADAPTER_VERSION,
    SOURCE_SYSTEM,
    SportradarFixtureCandidate,
    parse_daily_summaries,
)
from sports_edge_tennis.live.sportradar_reconciliation import (
    ExistingPlayerResolution,
    resolve_sportradar_event_overlap,
)


SOURCE_DATASET = "sportradar_tennis_daily_summaries"


@dataclass(frozen=True)
class SportradarIngestedMatch:
    source_match_id: str
    match_id: int
    match_version_id: int
    player_1_id: int
    player_2_id: int
    match_status: str
    overlap_status: str


@dataclass(frozen=True)
class SportradarIngestOutcome:
    rows_seen: int
    rows_ingested: int
    rows_rejected: int
    rows_deferred_new: int
    status: str
    matches: tuple[SportradarIngestedMatch, ...] = ()
    rejected_rows: tuple[dict[str, object], ...] = ()


class SportradarIngestError(RuntimeError):
    pass


def ingest_sportradar_daily_summaries(
    conn: psycopg.Connection,
    payload: Mapping[str, Any],
    *,
    received_at: datetime,
    dry_run: bool = False,
    allow_new_events: bool = False,
) -> SportradarIngestOutcome:
    parsed = parse_daily_summaries(payload, received_at=received_at)
    rejected = [
        {"reason": reason, "count": count}
        for reason, count in parsed.rejection_reasons
    ]
    if dry_run:
        return SportradarIngestOutcome(
            rows_seen=parsed.rows_seen,
            rows_ingested=0,
            rows_rejected=parsed.rows_rejected,
            rows_deferred_new=0,
            status=_status(0, parsed.rows_rejected, 0),
            rejected_rows=tuple(rejected),
        )

    ingest_run_id = _start_ingest_run(conn, received_at=received_at)
    repository = HistoricalRepository(conn, ingest_run_id)
    ingested: list[SportradarIngestedMatch] = []
    deferred_new = 0

    try:
        for fixture in parsed.fixtures:
            try:
                with conn.transaction():
                    overlap = resolve_sportradar_event_overlap(
                        conn,
                        fixture=fixture,
                    )
                    if overlap.status in {
                        "PLAYER_IDENTITY_UNRESOLVED",
                        "AMBIGUOUS_EXISTING_MATCH",
                        "SOURCE_ID_CONFLICT",
                        "TERMINAL_TRUTH_CONFLICT",
                        "TOURNAMENT_EVIDENCE_CONFLICT",
                    }:
                        raise SportradarIngestError(
                            f"overlap_reconciliation:{overlap.status}"
                        )

                    if overlap.match_id is not None:
                        terminal_conflict = _terminal_status_conflict(
                            _latest_terminal_outcome(
                                conn,
                                match_id=int(overlap.match_id),
                            ),
                            fixture.match_status,
                        )
                        if terminal_conflict is not None:
                            raise SportradarIngestError(terminal_conflict)

                    if (
                        overlap.status == "NEW_CANONICAL_EVENT"
                        and not allow_new_events
                    ):
                        deferred_new += 1
                        rejected.append(
                            {
                                "event_id": fixture.source_match_id,
                                "reason": "new_event_deferred_until_cutover",
                            }
                        )
                        continue

                    locator = _persist_fixture(
                        conn,
                        repository=repository,
                        fixture=fixture,
                        overlap=overlap,
                        ingest_run_id=ingest_run_id,
                    )
                    ingested.append(
                        SportradarIngestedMatch(
                            source_match_id=fixture.source_match_id,
                            match_id=locator.match_id,
                            match_version_id=locator.match_version_id,
                            player_1_id=locator.player_1_id,
                            player_2_id=locator.player_2_id,
                            match_status=fixture.match_status,
                            overlap_status=overlap.status,
                        )
                    )
            except (SportradarIngestError, ValueError) as exc:
                rejected.append(
                    {
                        "event_id": fixture.source_match_id,
                        "reason": str(exc),
                    }
                )

        rows_rejected = parsed.rows_rejected + (
            len(rejected) - len(parsed.rejection_reasons)
        )
        status = _status(len(ingested), rows_rejected, deferred_new)
        _finish_ingest_run(
            conn,
            ingest_run_id=ingest_run_id,
            status=status,
            rows_seen=parsed.rows_seen,
            rows_inserted=len(ingested),
            rows_rejected=rows_rejected,
            error_summary=rejected,
        )
    except Exception:
        _finish_ingest_run(
            conn,
            ingest_run_id=ingest_run_id,
            status="FAILED",
            rows_seen=parsed.rows_seen,
            rows_inserted=len(ingested),
            rows_rejected=parsed.rows_rejected + len(rejected),
            error_summary=rejected,
        )
        raise

    return SportradarIngestOutcome(
        rows_seen=parsed.rows_seen,
        rows_ingested=len(ingested),
        rows_rejected=rows_rejected,
        rows_deferred_new=deferred_new,
        status=status,
        matches=tuple(ingested),
        rejected_rows=tuple(rejected),
    )


def _latest_terminal_outcome(
    conn: psycopg.Connection,
    *,
    match_id: int,
) -> str | None:
    row = conn.execute(
        """
        SELECT outcome_status::text
        FROM match_result_observations
        WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC,
                 match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        return None
    status = str(row[0])
    return status if status in {"COMPLETED", "RETIRED", "WALKOVER", "CANCELLED"} else None


def _terminal_status_conflict(
    existing_outcome: str | None,
    incoming_status: str,
) -> str | None:
    if existing_outcome is None or existing_outcome == incoming_status:
        return None
    return f"terminal_truth_conflict:{existing_outcome}:{incoming_status}"


def _persist_fixture(
    conn: psycopg.Connection,
    *,
    repository: HistoricalRepository,
    fixture: SportradarFixtureCandidate,
    overlap,
    ingest_run_id: int,
) -> MatchLocator:
    p1_id = _materialize_player(
        conn,
        repository=repository,
        resolution=overlap.player_1,
        provider_player_id=fixture.player_1_source_id,
        provider_name=fixture.player_1_name,
        observed_at=fixture.received_at,
        ingest_run_id=ingest_run_id,
    )
    p2_id = _materialize_player(
        conn,
        repository=repository,
        resolution=overlap.player_2,
        provider_player_id=fixture.player_2_source_id,
        provider_name=fixture.player_2_name,
        observed_at=fixture.received_at,
        ingest_run_id=ingest_run_id,
    )
    if p1_id == p2_id:
        raise SportradarIngestError("resolved_players_collide")

    if overlap.status in {"EXACT_EXISTING_MATCH", "SOURCE_ID_RESOLVED"}:
        match_id = int(overlap.match_id)
        existing = _existing_match_context(
            conn,
            match_id=match_id,
            as_of=fixture.received_at,
        )
        if existing is None:
            raise SportradarIngestError("existing_match_context_missing")
        (
            existing_version_id,
            canonical_p1,
            canonical_p2,
            edition_id,
            tournament_id,
        ) = existing

        direct = canonical_p1 == p1_id and canonical_p2 == p2_id
        reverse = canonical_p1 == p2_id and canonical_p2 == p1_id
        if direct == reverse:
            raise SportradarIngestError("existing_match_player_conflict")
        oriented_p1, oriented_p2 = (
            (p1_id, p2_id) if direct else (p2_id, p1_id)
        )
        _attach_tournament_source(
            conn,
            tournament_id=tournament_id,
            provider_competition_id=fixture.competition_source_id,
            ingest_run_id=ingest_run_id,
        )
        _attach_match_source(
            conn,
            match_id=match_id,
            match_version_id=existing_version_id,
            provider_event_id=fixture.source_match_id,
            ingest_run_id=ingest_run_id,
        )
        return repository.upsert_match_version(
            tournament_edition_id=edition_id,
            source_system=SOURCE_SYSTEM,
            source_match_id=fixture.source_match_id,
            source_record_hash=_record_hash(fixture),
            source_record_locator=f"sportradar:sport_event:{fixture.source_match_id}",
            source_file_name=SOURCE_DATASET,
            source_version_hash=_record_hash(fixture),
            player_1_id=oriented_p1,
            player_2_id=oriented_p2,
            match_start_at=fixture.match_start_at,
            tour_scope=fixture.tour_scope,
            competition_tier=fixture.competition_tier,
            surface=fixture.surface,
            round_code=fixture.round_code,
            best_of_sets=fixture.best_of_sets,
            qualifying_flag=fixture.qualifying_flag,
            match_status=fixture.match_status,
            source_observed_at=fixture.received_at,
            as_of=fixture.received_at,
        )

    tournament_id = repository.get_or_create_tournament(
        source_system=SOURCE_SYSTEM,
        source_tournament_id=fixture.competition_source_id,
        canonical_name=fixture.competition_name,
        tour_scope=fixture.tour_scope,
        competition_tier=fixture.competition_tier,
    )
    edition_id = repository.get_or_create_tournament_edition(
        tournament_id=tournament_id,
        season_year=fixture.match_start_at.year,
        edition_name=f"{fixture.competition_name} {fixture.match_start_at.year}",
        surface=fixture.surface,
        start_date=fixture.match_start_at.date(),
        end_date=None,
    )
    return repository.upsert_match_version(
        tournament_edition_id=edition_id,
        source_system=SOURCE_SYSTEM,
        source_match_id=fixture.source_match_id,
        source_record_hash=_record_hash(fixture),
        source_record_locator=f"sportradar:sport_event:{fixture.source_match_id}",
        source_file_name=SOURCE_DATASET,
        source_version_hash=_record_hash(fixture),
        player_1_id=p1_id,
        player_2_id=p2_id,
        match_start_at=fixture.match_start_at,
        tour_scope=fixture.tour_scope,
        competition_tier=fixture.competition_tier,
        surface=fixture.surface,
        round_code=fixture.round_code,
        best_of_sets=fixture.best_of_sets,
        qualifying_flag=fixture.qualifying_flag,
        match_status=fixture.match_status,
        source_observed_at=fixture.received_at,
        as_of=fixture.received_at,
    )


def _materialize_player(
    conn: psycopg.Connection,
    *,
    repository: HistoricalRepository,
    resolution: ExistingPlayerResolution,
    provider_player_id: str,
    provider_name: str,
    observed_at: datetime,
    ingest_run_id: int,
) -> int:
    if resolution.status in {"AMBIGUOUS_PLAYER", "SOURCE_ID_CONFLICT"}:
        raise SportradarIngestError(
            f"player_identity:{resolution.status}"
        )
    if resolution.player_id is None:
        if resolution.status != "NEW_PLAYER":
            raise SportradarIngestError(
                f"player_identity:{resolution.status}"
            )
        return repository.create_player_with_source_id(
            source_system=SOURCE_SYSTEM,
            source_player_id=provider_player_id,
            canonical_name=provider_name,
            observed_at=observed_at,
        )

    player_id = int(resolution.player_id)
    existing = conn.execute(
        """
        SELECT player_id
        FROM player_source_ids
        WHERE source_system = %s
          AND source_player_id = %s
        """,
        (SOURCE_SYSTEM, provider_player_id),
    ).fetchone()
    if existing is not None and int(existing[0]) != player_id:
        raise SportradarIngestError("player_source_id_conflict")
    if existing is None:
        conn.execute(
            """
            INSERT INTO player_source_ids (
                player_id,
                source_system,
                source_player_id,
                ingest_run_id
            )
            VALUES (%s, %s, %s, %s)
            """,
            (player_id, SOURCE_SYSTEM, provider_player_id, ingest_run_id),
        )
    repository.ensure_player_alias(
        player_id,
        SOURCE_SYSTEM,
        provider_name,
        observed_at,
    )
    return player_id


def _existing_match_context(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
):
    return conn.execute(
        """
        SELECT
            mv.match_version_id,
            mv.player_1_id,
            mv.player_2_id,
            m.tournament_edition_id,
            te.tournament_id
        FROM matches m
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN LATERAL (
            SELECT match_version_id, player_1_id, player_2_id
            FROM match_versions
            WHERE match_id = m.match_id
              AND source_observed_at <= %s
              AND as_of <= %s
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        WHERE m.match_id = %s
        """,
        (as_of, as_of, match_id),
    ).fetchone()


def _attach_tournament_source(
    conn: psycopg.Connection,
    *,
    tournament_id: int,
    provider_competition_id: str,
    ingest_run_id: int,
) -> None:
    row = conn.execute(
        """
        SELECT tournament_id
        FROM tournament_source_ids
        WHERE source_system = %s
          AND source_tournament_id = %s
        """,
        (SOURCE_SYSTEM, provider_competition_id),
    ).fetchone()
    if row is not None:
        if int(row[0]) != tournament_id:
            raise SportradarIngestError("tournament_source_id_conflict")
        return
    conn.execute(
        """
        INSERT INTO tournament_source_ids (
            tournament_id,
            source_system,
            source_tournament_id,
            ingest_run_id
        )
        VALUES (%s, %s, %s, %s)
        """,
        (
            tournament_id,
            SOURCE_SYSTEM,
            provider_competition_id,
            ingest_run_id,
        ),
    )


def _attach_match_source(
    conn: psycopg.Connection,
    *,
    match_id: int,
    match_version_id: int,
    provider_event_id: str,
    ingest_run_id: int,
) -> None:
    row = conn.execute(
        """
        SELECT match_id
        FROM match_source_ids
        WHERE source_system = %s
          AND source_match_id = %s
        """,
        (SOURCE_SYSTEM, provider_event_id),
    ).fetchone()
    if row is not None:
        if int(row[0]) != match_id:
            raise SportradarIngestError("match_source_id_conflict")
        return
    conn.execute(
        """
        INSERT INTO match_source_ids (
            match_id,
            match_version_id,
            source_system,
            source_match_id,
            ingest_run_id
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            match_id,
            match_version_id,
            SOURCE_SYSTEM,
            provider_event_id,
            ingest_run_id,
        ),
    )


def _record_hash(fixture: SportradarFixtureCandidate) -> str:
    return source_hash(
        SOURCE_SYSTEM,
        {
            "event_id": fixture.source_match_id,
            "competition_id": fixture.competition_source_id,
            "p1": fixture.player_1_source_id,
            "p2": fixture.player_2_source_id,
            "start": fixture.match_start_at,
            "status": fixture.match_status,
            "surface": fixture.surface,
            "round": fixture.round_code,
            "best_of": fixture.best_of_sets,
            "qualifying": fixture.qualifying_flag,
        },
    )


def _status(ingested: int, rejected: int, deferred: int) -> str:
    if rejected == 0 and deferred == 0:
        return "COMPLETED"
    if ingested == 0:
        return "FAILED"
    return "PARTIAL"


def _start_ingest_run(
    conn: psycopg.Connection,
    *,
    received_at: datetime,
) -> int:
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


__all__ = [
    "SOURCE_DATASET",
    "SportradarIngestError",
    "SportradarIngestOutcome",
    "SportradarIngestedMatch",
    "ingest_sportradar_daily_summaries",
]
