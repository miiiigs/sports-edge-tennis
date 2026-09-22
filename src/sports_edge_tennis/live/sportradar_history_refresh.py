"""Bounded Sportradar Tennis result/stat refresh for target-provider mode.

This is the prospective replacement for the API-Tennis history refresh after the
provider cutover. It is intentionally bounded to recent daily summaries and writes
only terminal result truth plus final two-sided match statistics.

Point-in-time rules:
* source_observed_at/as_of are the actual receive time, never match time;
* terminal result and its final stat rows share the same visibility timestamp;
* semantically unchanged result+stats observations are not duplicated;
* live/partial statistics are never admitted to Agent 2 history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

import psycopg

from sports_edge_tennis.data.contracts import MatchStatLine
from sports_edge_tennis.data.hashing import source_hash
from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar_fixtures import (
    SOURCE_SYSTEM,
    SportradarFixtureCandidate,
    SportradarFixtureError,
    parse_summary,
)
from sports_edge_tennis.live.sportradar_ingest import (
    SportradarIngestedMatch,
    ingest_sportradar_daily_summaries,
)
from sports_edge_tennis.live.target_fixtures import fetch_sportradar_fixture_rows
from sports_edge_tennis.live.transport import TransportError


RESULT_DATASET = "sportradar_tennis_terminal_result_stats"
RESULT_ADAPTER_VERSION = "SPORTRADAR_TENNIS_RESULT_STATS_V1"
MAX_REFRESH_DAYS = 7
DEFAULT_REFRESH_DAYS = 3
_TERMINAL = {"COMPLETED", "RETIRED", "WALKOVER", "CANCELLED"}


@dataclass(frozen=True)
class SportradarHistoryDayOutcome:
    target_date: date
    fixtures_seen: int
    fixtures_ingested: int
    fixture_rejections: int
    terminal_seen: int
    observations_inserted: int
    observations_unchanged: int
    result_rejections: int
    stat_rows_inserted: int
    stat_rows_unchanged: int
    stat_rejections: int


@dataclass(frozen=True)
class ParsedResult:
    outcome_status: str
    winner_side: int | None
    retired_side: int | None
    sets_won_player_1: int | None
    sets_won_player_2: int | None
    games_won_player_1: int | None
    games_won_player_2: int | None
    scoreline_text: str | None

    @property
    def semantic_key(self) -> tuple[object, ...]:
        return (
            self.outcome_status,
            self.winner_side,
            self.retired_side,
            self.sets_won_player_1,
            self.sets_won_player_2,
            self.games_won_player_1,
            self.games_won_player_2,
        )


@dataclass(frozen=True)
class ParsedStat:
    provider_player_id: str
    line: MatchStatLine
    record_hash: str


FetchRows = Callable[[str, date], tuple[datetime, list[dict[str, Any]]]]


def refresh_sportradar_history_day(
    conn: psycopg.Connection,
    *,
    api_key: str,
    target_date: date,
    fetch_rows: FetchRows = fetch_sportradar_fixture_rows,
) -> SportradarHistoryDayOutcome:
    received_at, summaries = fetch_rows(api_key, target_date)
    if received_at.tzinfo is None:
        raise ValueError("Sportradar receive time must be timezone-aware")

    payload = {"summaries": summaries}
    fixture_outcome = ingest_sportradar_daily_summaries(
        conn,
        payload,
        received_at=received_at,
        allow_new_events=True,
    )
    ingested_by_event = {
        row.source_match_id: row for row in fixture_outcome.matches
    }

    terminal_seen = 0
    inserted = 0
    unchanged = 0
    result_rejections = 0
    stat_inserted = 0
    stat_unchanged = 0
    stat_rejections = 0
    errors: list[dict[str, object]] = []

    prepared: list[
        tuple[
            SportradarFixtureCandidate,
            SportradarIngestedMatch,
            ParsedResult,
            tuple[ParsedStat, ParsedStat] | None,
        ]
    ] = []

    for summary in summaries:
        try:
            fixture = parse_summary(summary, received_at=received_at)
        except SportradarFixtureError:
            # Already accounted for by fixture_outcome.
            continue
        if fixture.match_status not in _TERMINAL:
            continue
        terminal_seen += 1

        ingested = ingested_by_event.get(fixture.source_match_id)
        if ingested is None:
            result_rejections += 1
            errors.append(
                {
                    "event_id": fixture.source_match_id,
                    "reason": "terminal_match_not_canonically_ingested",
                }
            )
            continue

        try:
            result = _parse_terminal_result(summary, fixture=fixture)
            result_player_map = _validate_sportradar_player_map(
                _sportradar_player_map(
                    conn,
                    provider_ids=(
                        fixture.player_1_source_id,
                        fixture.player_2_source_id,
                    ),
                ),
                expected_player_ids=(
                    ingested.player_1_id,
                    ingested.player_2_id,
                ),
            )
            result = _orient_result_to_canonical(
                result,
                fixture=fixture,
                player_map=result_player_map,
                canonical_player_ids=(
                    ingested.player_1_id,
                    ingested.player_2_id,
                ),
            )
        except ValueError as exc:
            result_rejections += 1
            errors.append(
                {"event_id": fixture.source_match_id, "reason": str(exc)}
            )
            continue

        current_result = _latest_result_semantic_key(
            conn,
            match_id=ingested.match_id,
        )
        result_conflict = _terminal_result_conflict(current_result, result)
        if result_conflict is not None:
            result_rejections += 1
            errors.append(
                {
                    "event_id": fixture.source_match_id,
                    "reason": result_conflict,
                }
            )
            continue

        stats: tuple[ParsedStat, ParsedStat] | None = None
        if result.outcome_status == "COMPLETED":
            try:
                stats = _parse_final_stats(summary, fixture=fixture)
            except ValueError as exc:
                stat_rejections += 1
                errors.append(
                    {
                        "event_id": fixture.source_match_id,
                        "reason": str(exc),
                    }
                )
        prepared.append((fixture, ingested, result, stats))

    if prepared:
        ingest_run_id = _start_ingest_run(conn, received_at=received_at)
        repository = HistoricalRepository(conn, ingest_run_id)
        try:
            for fixture, ingested, result, stats in prepared:
                try:
                    with conn.transaction():
                        current_result = _latest_result_semantic_key(
                            conn, match_id=ingested.match_id
                        )
                        current_hashes = _latest_aligned_sportradar_stat_hashes(
                            conn,
                            match_id=ingested.match_id,
                        )
                        next_hashes = (
                            tuple(sorted(item.record_hash for item in stats))
                            if stats is not None
                            else ()
                        )

                        same_result = current_result == result.semantic_key
                        same_stats = current_hashes == next_hashes
                        if same_result and same_stats:
                            unchanged += 1
                            if stats is not None:
                                stat_unchanged += 2
                            continue

                        canonical_players: dict[str, int] | None = None
                        if stats is not None:
                            canonical_players = _validate_sportradar_player_map(
                                result_player_map,
                                expected_player_ids=(
                                    ingested.player_1_id,
                                    ingested.player_2_id,
                                ),
                            )

                        # A new result observation is intentionally appended
                        # whenever new final-stat evidence becomes visible.
                        # Agent 2 joins final stats to result visibility at
                        # exactly the same timestamp.
                        repository.insert_match_result(
                            match_id=ingested.match_id,
                            outcome_status=result.outcome_status,
                            winner_side=result.winner_side,
                            retired_side=result.retired_side,
                            sets_won_player_1=result.sets_won_player_1,
                            sets_won_player_2=result.sets_won_player_2,
                            games_won_player_1=result.games_won_player_1,
                            games_won_player_2=result.games_won_player_2,
                            scoreline_text=result.scoreline_text,
                            source_observed_at=received_at,
                            as_of=received_at,
                        )

                        inserted_stats_this_event = 0
                        unchanged_stats_this_event = 0
                        if stats is not None:
                            assert canonical_players is not None
                            for stat in stats:
                                player_id = canonical_players.get(
                                    stat.provider_player_id
                                )
                                if player_id is None:
                                    raise ValueError(
                                        "sportradar_final_stats_player_bridge_incomplete"
                                    )
                                did_insert = repository.insert_match_stat(
                                    match_id=ingested.match_id,
                                    player_id=player_id,
                                    source_system=SOURCE_SYSTEM,
                                    source_record_id=(
                                        f"{fixture.source_match_id}:"
                                        f"{stat.provider_player_id}:FINAL"
                                    ),
                                    source_record_hash=stat.record_hash,
                                    line=stat.line,
                                    source_observed_at=received_at,
                                    as_of=received_at,
                                )
                                inserted_stats_this_event += 1 if did_insert else 0
                                unchanged_stats_this_event += 0 if did_insert else 1

                    inserted += 1
                    stat_inserted += inserted_stats_this_event
                    stat_unchanged += unchanged_stats_this_event
                except (ValueError, psycopg.Error) as exc:
                    result_rejections += 1
                    if stats is not None:
                        stat_rejections += 1
                    errors.append(
                        {
                            "event_id": fixture.source_match_id,
                            "reason": str(exc) or type(exc).__name__,
                        }
                    )
                    continue

            status = "COMPLETED" if not errors else (
                "PARTIAL" if inserted or unchanged else "FAILED"
            )
            _finish_ingest_run(
                conn,
                ingest_run_id=ingest_run_id,
                status=status,
                rows_seen=len(prepared),
                rows_inserted=inserted + stat_inserted,
                rows_rejected=result_rejections + stat_rejections,
                errors=errors,
            )
        except Exception:
            _finish_ingest_run(
                conn,
                ingest_run_id=ingest_run_id,
                status="FAILED",
                rows_seen=len(prepared),
                rows_inserted=inserted + stat_inserted,
                rows_rejected=result_rejections + stat_rejections + 1,
                errors=errors,
            )
            raise

    if inserted:
        _invalidate_model_checkpoint(conn, "AGENT_3_INTRANSITIVITY")
    if stat_inserted:
        _invalidate_model_checkpoint(conn, "AGENT_2_HIERARCHICAL_MARKOV")

    return SportradarHistoryDayOutcome(
        target_date=target_date,
        fixtures_seen=fixture_outcome.rows_seen,
        fixtures_ingested=fixture_outcome.rows_ingested,
        fixture_rejections=fixture_outcome.rows_rejected,
        terminal_seen=terminal_seen,
        observations_inserted=inserted,
        observations_unchanged=unchanged,
        result_rejections=result_rejections,
        stat_rows_inserted=stat_inserted,
        stat_rows_unchanged=stat_unchanged,
        stat_rejections=stat_rejections,
    )


def _parse_terminal_result(
    summary: Mapping[str, Any],
    *,
    fixture: SportradarFixtureCandidate,
) -> ParsedResult:
    status = summary.get("sport_event_status")
    if not isinstance(status, Mapping):
        raise ValueError("missing_sport_event_status")

    if fixture.match_status == "CANCELLED":
        winner_side = None
    else:
        winner_id = str(status.get("winner_id") or "").strip()
        if winner_id == fixture.player_1_source_id:
            winner_side = 1
        elif winner_id == fixture.player_2_source_id:
            winner_side = 2
        else:
            raise ValueError("terminal_winner_id_unresolved")

    retired_side = None
    if fixture.match_status == "RETIRED" and winner_side in (1, 2):
        retired_side = 2 if winner_side == 1 else 1
    period_sets_1, period_sets_2, games_1, games_2 = _period_totals(status)
    sets_1 = _nonnegative_int(status.get("home_score"))
    sets_2 = _nonnegative_int(status.get("away_score"))
    if sets_1 is None or sets_2 is None:
        sets_1, sets_2 = period_sets_1, period_sets_2
    scoreline = (
        f"{sets_1}-{sets_2}"
        if sets_1 is not None and sets_2 is not None
        else None
    )
    return ParsedResult(
        outcome_status=fixture.match_status,
        winner_side=winner_side,
        retired_side=retired_side,
        sets_won_player_1=sets_1,
        sets_won_player_2=sets_2,
        games_won_player_1=games_1,
        games_won_player_2=games_2,
        scoreline_text=scoreline,
    )


def _period_totals(
    status: Mapping[str, Any],
) -> tuple[int | None, int | None, int | None, int | None]:
    periods = status.get("period_scores")
    if not isinstance(periods, Sequence) or isinstance(
        periods, (str, bytes, bytearray)
    ):
        return None, None, None, None

    sets_1 = 0
    sets_2 = 0
    games_1 = 0
    games_2 = 0
    set_count = 0
    for period in periods:
        if not isinstance(period, Mapping):
            continue
        period_type = str(period.get("type") or "").strip().casefold()
        if period_type not in {"set", "regular_period"}:
            continue
        home = _nonnegative_int(period.get("home_score"))
        away = _nonnegative_int(period.get("away_score"))
        if home is None or away is None:
            continue
        set_count += 1
        games_1 += home
        games_2 += away
        if home > away:
            sets_1 += 1
        elif away > home:
            sets_2 += 1
    if set_count == 0:
        return None, None, None, None
    return sets_1, sets_2, games_1, games_2


def _parse_final_stats(
    summary: Mapping[str, Any],
    *,
    fixture: SportradarFixtureCandidate,
) -> tuple[ParsedStat, ParsedStat]:
    statistics = summary.get("statistics")
    if not isinstance(statistics, Mapping):
        raise ValueError("final_stats_missing")
    totals = statistics.get("totals")
    if not isinstance(totals, Mapping):
        raise ValueError("final_stats_missing_totals")
    rows = totals.get("competitors")
    if not isinstance(rows, Sequence) or isinstance(
        rows, (str, bytes, bytearray)
    ):
        raise ValueError("final_stats_missing_competitors")
    competitors = [row for row in rows if isinstance(row, Mapping)]
    if len(competitors) != 2:
        raise ValueError("final_stats_not_two_competitors")

    by_id: dict[str, Mapping[str, Any]] = {}
    for row in competitors:
        provider_id = str(row.get("id") or "").strip()
        if not provider_id or provider_id in by_id:
            raise ValueError("final_stats_competitor_identity_invalid")
        by_id[provider_id] = row
    expected = {
        fixture.player_1_source_id,
        fixture.player_2_source_id,
    }
    if set(by_id) != expected:
        raise ValueError("final_stats_competitor_identity_mismatch")

    first = _parsed_stat(
        by_id[fixture.player_1_source_id],
        opponent=by_id[fixture.player_2_source_id],
        event_id=fixture.source_match_id,
    )
    second = _parsed_stat(
        by_id[fixture.player_2_source_id],
        opponent=by_id[fixture.player_1_source_id],
        event_id=fixture.source_match_id,
    )
    return first, second


def _parsed_stat(
    row: Mapping[str, Any],
    *,
    opponent: Mapping[str, Any],
    event_id: str,
) -> ParsedStat:
    provider_id = str(row.get("id") or "").strip()
    stats = row.get("statistics")
    opponent_stats = opponent.get("statistics")
    if not provider_id or not isinstance(stats, Mapping):
        raise ValueError("final_stats_row_invalid")
    if not isinstance(opponent_stats, Mapping):
        raise ValueError("final_stats_opponent_row_invalid")

    first_won = _nonnegative_int(stats.get("first_serve_points_won"))
    second_won = _nonnegative_int(stats.get("second_serve_points_won"))
    service_won = _nonnegative_int(stats.get("service_points_won"))
    service_lost = _nonnegative_int(stats.get("service_points_lost"))
    if None in (first_won, second_won, service_won, service_lost):
        raise ValueError("final_stats_a2_core_missing")
    assert first_won is not None
    assert second_won is not None
    assert service_won is not None
    assert service_lost is not None
    if first_won + second_won != service_won:
        raise ValueError("final_stats_service_points_incoherent")
    service_played = service_won + service_lost
    if service_played <= 0:
        raise ValueError("final_stats_service_points_empty")

    opponent_service_lost = _nonnegative_int(
        opponent_stats.get("service_points_lost")
    )
    return_points_won = opponent_service_lost

    faced = _nonnegative_int(opponent_stats.get("total_breakpoints"))
    opponent_breaks = _nonnegative_int(opponent_stats.get("breakpoints_won"))
    saved = None
    if faced is not None and opponent_breaks is not None:
        if opponent_breaks > faced:
            raise ValueError("final_stats_breakpoints_incoherent")
        saved = faced - opponent_breaks

    line = MatchStatLine(
        first_serves_in=_nonnegative_int(stats.get("first_serve_successful")),
        first_serve_points_won=first_won,
        second_serve_points_won=second_won,
        service_points_played=service_played,
        return_points_won=return_points_won,
        aces=_nonnegative_int(stats.get("aces")),
        double_faults=_nonnegative_int(stats.get("double_faults")),
        break_points_saved=saved,
        break_points_faced=faced,
    )
    digest = source_hash(
        SOURCE_SYSTEM,
        {
            "event_id": event_id,
            "provider_player_id": provider_id,
            "first_serves_in": line.first_serves_in,
            "first_serve_points_won": line.first_serve_points_won,
            "second_serve_points_won": line.second_serve_points_won,
            "service_points_played": line.service_points_played,
            "return_points_won": line.return_points_won,
            "aces": line.aces,
            "double_faults": line.double_faults,
            "break_points_saved": line.break_points_saved,
            "break_points_faced": line.break_points_faced,
        },
    )
    return ParsedStat(
        provider_player_id=provider_id,
        line=line,
        record_hash=digest,
    )


def _orient_result_to_canonical(
    result: ParsedResult,
    *,
    fixture: SportradarFixtureCandidate,
    player_map: Mapping[str, int],
    canonical_player_ids: tuple[int, int],
) -> ParsedResult:
    try:
        provider_first = int(player_map[fixture.player_1_source_id])
        provider_second = int(player_map[fixture.player_2_source_id])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("sportradar_result_orientation_unresolved") from exc

    direct = (provider_first, provider_second) == canonical_player_ids
    reverse = (provider_second, provider_first) == canonical_player_ids
    if direct == reverse:
        raise ValueError("sportradar_result_orientation_conflict")
    if direct:
        return result

    def _flip_side(side: int | None) -> int | None:
        if side == 1:
            return 2
        if side == 2:
            return 1
        return side

    sets_1 = result.sets_won_player_2
    sets_2 = result.sets_won_player_1
    scoreline = (
        f"{sets_1}-{sets_2}"
        if sets_1 is not None and sets_2 is not None
        else None
    )
    return ParsedResult(
        outcome_status=result.outcome_status,
        winner_side=_flip_side(result.winner_side),
        retired_side=_flip_side(result.retired_side),
        sets_won_player_1=sets_1,
        sets_won_player_2=sets_2,
        games_won_player_1=result.games_won_player_2,
        games_won_player_2=result.games_won_player_1,
        scoreline_text=scoreline,
    )


def _terminal_result_conflict(
    current_result: tuple[object, ...] | None,
    candidate: ParsedResult,
) -> str | None:
    if current_result is None:
        return None

    current_status = str(current_result[0])
    if current_status not in _TERMINAL:
        return None
    if current_status != candidate.outcome_status:
        return (
            "terminal_outcome_conflict:"
            f"{current_status}:{candidate.outcome_status}"
        )

    current_winner = current_result[1]
    if current_winner != candidate.winner_side:
        return (
            "terminal_winner_conflict:"
            f"{current_winner}:{candidate.winner_side}"
        )

    current_retired = current_result[2]
    if current_status == "RETIRED" and current_retired != candidate.retired_side:
        return (
            "terminal_retired_side_conflict:"
            f"{current_retired}:{candidate.retired_side}"
        )
    return None


def _latest_result_semantic_key(
    conn: psycopg.Connection,
    *,
    match_id: int,
) -> tuple[object, ...] | None:
    row = conn.execute(
        """
        SELECT
            outcome_status::text,
            winner_side,
            retired_side,
            sets_won_player_1,
            sets_won_player_2,
            games_won_player_1,
            games_won_player_2
        FROM match_result_observations
        WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC,
                 match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    return None if row is None else tuple(row)


def _latest_aligned_sportradar_stat_hashes(
    conn: psycopg.Connection,
    *,
    match_id: int,
) -> tuple[str, ...]:
    row = conn.execute(
        """
        SELECT source_observed_at, as_of
        FROM match_result_observations
        WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC,
                 match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        return ()
    rows = conn.execute(
        """
        SELECT source_record_hash
        FROM match_stat_observations
        WHERE match_id = %s
          AND source_system = %s
          AND observation_kind = 'RAW_SOURCE'
          AND source_observed_at = %s
          AND as_of = %s
          AND source_record_hash IS NOT NULL
        ORDER BY source_record_hash
        """,
        (match_id, SOURCE_SYSTEM, row[0], row[1]),
    ).fetchall()
    return tuple(str(value[0]) for value in rows)


def _validate_sportradar_player_map(
    player_map: Mapping[str, int],
    *,
    expected_player_ids: tuple[int, int],
) -> dict[str, int]:
    resolved = {str(provider_id): int(player_id) for provider_id, player_id in player_map.items()}
    if len(resolved) != 2:
        raise ValueError("sportradar_final_stats_player_bridge_incomplete")
    if set(resolved.values()) != set(expected_player_ids):
        raise ValueError("sportradar_final_stats_player_bridge_mismatch")
    return resolved


def _sportradar_player_map(
    conn: psycopg.Connection,
    *,
    provider_ids: tuple[str, ...],
) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT source_player_id, player_id
        FROM player_source_ids
        WHERE source_system = %s
          AND source_player_id = ANY(%s)
        """,
        (SOURCE_SYSTEM, list(provider_ids)),
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _start_ingest_run(
    conn: psycopg.Connection,
    *,
    received_at: datetime,
) -> int:
    return int(
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
                RESULT_DATASET,
                received_at,
                RESULT_ADAPTER_VERSION,
            ),
        ).fetchone()[0]
    )


def _finish_ingest_run(
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
            json.dumps(errors, sort_keys=True),
            ingest_run_id,
        ),
    )


def _invalidate_model_checkpoint(
    conn: psycopg.Connection,
    checkpoint_key: str,
) -> None:
    table = conn.execute(
        "SELECT to_regclass('public.live_model_state_checkpoints')"
    ).fetchone()[0]
    if table is None:
        return
    conn.execute(
        "DELETE FROM live_model_state_checkpoints WHERE checkpoint_key = %s",
        (checkpoint_key,),
    )


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _default_end_date() -> date:
    return datetime.now(UTC).date() - timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh recent Sportradar Tennis terminal results/stats"
    )
    parser.add_argument("--end-date", type=date.fromisoformat, default=_default_end_date())
    parser.add_argument("--days", type=int, default=DEFAULT_REFRESH_DAYS)
    parser.add_argument("--dsn", default=_db_dsn())
    args = parser.parse_args(argv)

    if not 1 <= args.days <= MAX_REFRESH_DAYS:
        print(
            f"SPORTRADAR_HISTORY_REFRESH_BLOCKED reason=DAYS_MAX_{MAX_REFRESH_DAYS}",
            flush=True,
        )
        return 2
    if not args.dsn:
        print("SPORTRADAR_HISTORY_REFRESH_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not api_key:
        print(
            "SPORTRADAR_HISTORY_REFRESH_BLOCKED reason=MISSING_SPORTRADAR_TENNIS_API_KEY",
            flush=True,
        )
        return 2

    dates = [
        args.end_date - timedelta(days=offset)
        for offset in reversed(range(args.days))
    ]
    totals = {
        "fixtures": 0,
        "fixtures_ingested": 0,
        "fixture_rejections": 0,
        "terminal": 0,
        "results_inserted": 0,
        "results_unchanged": 0,
        "result_rejections": 0,
        "stats_inserted": 0,
        "stats_unchanged": 0,
        "stat_rejections": 0,
    }
    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            for target_date in dates:
                outcome = refresh_sportradar_history_day(
                    conn,
                    api_key=api_key,
                    target_date=target_date,
                )
                totals["fixtures"] += outcome.fixtures_seen
                totals["fixtures_ingested"] += outcome.fixtures_ingested
                totals["fixture_rejections"] += outcome.fixture_rejections
                totals["terminal"] += outcome.terminal_seen
                totals["results_inserted"] += outcome.observations_inserted
                totals["results_unchanged"] += outcome.observations_unchanged
                totals["result_rejections"] += outcome.result_rejections
                totals["stats_inserted"] += outcome.stat_rows_inserted
                totals["stats_unchanged"] += outcome.stat_rows_unchanged
                totals["stat_rejections"] += outcome.stat_rejections
                print(
                    "SPORTRADAR_HISTORY_REFRESH_DAY "
                    f"date={target_date.isoformat()} "
                    f"fixtures={outcome.fixtures_seen} "
                    f"terminal={outcome.terminal_seen} "
                    f"results_inserted={outcome.observations_inserted} "
                    f"stats_inserted={outcome.stat_rows_inserted}",
                    flush=True,
                )
    except TransportError as exc:
        print(
            f"SPORTRADAR_HISTORY_REFRESH_FAILED kind={exc.kind}",
            flush=True,
        )
        return 2
    except Exception as exc:
        print(
            f"SPORTRADAR_HISTORY_REFRESH_FAILED kind={type(exc).__name__}",
            flush=True,
        )
        return 2

    print(
        "SPORTRADAR_HISTORY_REFRESH_SUCCESS "
        + " ".join(f"{key}={value}" for key, value in totals.items()),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
