"""Bounded current-slate historical bootstrap for Agent 2.

This runs before a prediction cycle. For the latest concrete target matches it
finds the participating canonical players, measures each player's Agent 2
readiness deficit, and fetches targeted Sportradar historical daily summaries
only for dates on which those under-covered players have completed-but-statless
matches. Ingestion reuses the corrected Sportradar terminal result/stat pipeline,
which preserves point-in-time rules and invalidates the Agent 2 checkpoint.

It is bounded by an explicit request cap and stops early once every target player
is Agent 2-ready. It never lowers Agent 2 thresholds; it only supplies missing
real history.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import psycopg

from sports_edge_tennis.data.agent2_history import (
    build_history_snapshot,
    load_agent2_history,
)
from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar_history_refresh import (
    SportradarHistoryDayOutcome,
    refresh_sportradar_history_day,
)
from sports_edge_tennis.live.transport import TransportError

_PLACEHOLDER = re.compile(r"^(R\d+P\d+|Qf\d+|WQF\d+)$", re.IGNORECASE)

MIN_USABLE_MATCHES = 5
MIN_SERVE_POINTS = 250
MIN_RETURN_POINTS = 250

DEFAULT_MAX_REQUESTS = 12
DEFAULT_LOOKBACK_DAYS = 120


@dataclass(frozen=True)
class PlayerReadiness:
    player_id: int
    usable_matches: int
    serve_points: int
    return_points: int

    @property
    def ready(self) -> bool:
        return (
            self.usable_matches >= MIN_USABLE_MATCHES
            and self.serve_points >= MIN_SERVE_POINTS
            and self.return_points >= MIN_RETURN_POINTS
        )

    @property
    def missing_matches(self) -> int:
        return max(0, MIN_USABLE_MATCHES - self.usable_matches)


@dataclass(frozen=True)
class BootstrapOutcome:
    concrete_matches: int
    unique_players: int
    ready_before: int
    ready_after: int
    requests_consumed: int
    dates_attempted: tuple[str, ...]
    results_inserted: int
    stats_inserted: int
    stopped_reason: str
    readiness_before: dict[str, PlayerReadiness]
    readiness_after: dict[str, PlayerReadiness]


def is_placeholder_name(name: str | None) -> bool:
    return bool(_PLACEHOLDER.match((name or "").strip()))


DEFAULT_CYCLE_WINDOW_MINUTES = 30


def load_concrete_targets(
    conn: psycopg.Connection,
    *,
    run_id: int | None = None,
    cutoff: datetime,
    cycle_window_minutes: int = DEFAULT_CYCLE_WINDOW_MINUTES,
) -> list[tuple[int, int, int]]:
    """Return (match_id, player_1_id, player_2_id) for concrete targets only.

    A full slate is split across several prediction runs in one cycle, so by
    default targets are gathered from every run started within
    ``cycle_window_minutes`` of the latest run. Placeholders/unresolved
    participant slots are excluded. Player ids are taken from the latest
    point-in-time-visible match version.
    """
    if run_id is None:
        row = conn.execute(
            "SELECT prediction_run_id, started_at FROM prediction_runs "
            "ORDER BY prediction_run_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return []
        run_id = int(row[0])
        match_ids = [
            int(r[0])
            for r in conn.execute(
                """
                SELECT DISTINCT t.match_id
                FROM prediction_run_targets t
                JOIN prediction_runs r ON r.prediction_run_id = t.prediction_run_id
                WHERE r.started_at >= %s::timestamptz - (%s || ' minutes')::interval
                """,
                (row[1], cycle_window_minutes),
            ).fetchall()
        ]
    else:
        match_ids = [
            int(r[0])
            for r in conn.execute(
                "SELECT DISTINCT match_id FROM prediction_run_targets WHERE prediction_run_id = %s",
                (run_id,),
            ).fetchall()
        ]
    targets: list[tuple[int, int, int]] = []
    for match_id in match_ids:
        row = conn.execute(
            """
            SELECT DISTINCT ON (mv.match_id) mv.player_1_id, mv.player_2_id
            FROM match_versions mv
            WHERE mv.match_id = %s
              AND mv.source_observed_at <= %s
              AND mv.as_of <= %s
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
            """,
            (match_id, cutoff, cutoff),
        ).fetchone()
        if row is None:
            continue
        player_1_id, player_2_id = int(row[0]), int(row[1])
        names = {
            int(r[0]): r[1]
            for r in conn.execute(
                "SELECT player_id, canonical_name FROM players WHERE player_id IN (%s, %s)",
                (player_1_id, player_2_id),
            ).fetchall()
        }
        if is_placeholder_name(names.get(player_1_id)) or is_placeholder_name(
            names.get(player_2_id)
        ):
            continue
        targets.append((match_id, player_1_id, player_2_id))
    return targets


def snapshot_readiness(
    conn: psycopg.Connection,
    *,
    player_ids: list[int] | tuple[int, ...],
    cutoff: datetime,
) -> dict[int, PlayerReadiness]:
    snapshot = build_history_snapshot(load_agent2_history(conn, cutoff=cutoff), cutoff=cutoff)
    readiness: dict[int, PlayerReadiness] = {}
    for player_id in player_ids:
        player = snapshot.players.get(player_id)
        readiness[int(player_id)] = PlayerReadiness(
            player_id=int(player_id),
            usable_matches=player.prior_usable_matches if player else 0,
            serve_points=player.overall_serve_points_played if player else 0,
            return_points=player.overall_return_points_played if player else 0,
        )
    return readiness


def select_bootstrap_dates(
    conn: psycopg.Connection,
    *,
    player_ids: list[int] | tuple[int, ...],
    cutoff: datetime,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_dates: int = DEFAULT_MAX_REQUESTS,
) -> list[tuple[date, int]]:
    """Rank historical dates by how many under-covered target players they can help.

    A candidate date is one on which at least one under-covered player has a
    completed match that currently has no raw stat rows at all. Ranked by number
    of distinct under-covered players, then most recent first.
    """
    if not player_ids or max_dates < 1:
        return []
    start = cutoff.date() - timedelta(days=lookback_days)
    rows = conn.execute(
        """
        SELECT d, count(DISTINCT pid) AS player_count
        FROM (
            SELECT (mv.match_start_at AT TIME ZONE 'UTC')::date AS d, x.pid
            FROM match_versions mv
            CROSS JOIN LATERAL (VALUES (mv.player_1_id), (mv.player_2_id)) AS x(pid)
            WHERE x.pid = ANY(%(player_ids)s)
              AND mv.match_start_at IS NOT NULL
              AND (mv.match_start_at AT TIME ZONE 'UTC')::date >= %(start)s
              AND mv.match_start_at <= %(cutoff)s
              AND mv.source_observed_at <= %(cutoff)s
              AND mv.as_of <= %(cutoff)s
              AND EXISTS (
                  SELECT 1 FROM match_result_observations r
                  WHERE r.match_id = mv.match_id
                    AND r.outcome_status = 'COMPLETED'
                    AND r.source_observed_at <= %(cutoff)s
                    AND r.as_of <= %(cutoff)s
              )
              AND NOT EXISTS (
                  SELECT 1 FROM match_stat_observations s
                  WHERE s.match_id = mv.match_id
                    AND s.observation_kind = 'RAW_SOURCE'
              )
        ) AS candidate
        GROUP BY d
        ORDER BY player_count DESC, d DESC
        LIMIT %(max_dates)s
        """,
        {
            "player_ids": list(player_ids),
            "start": start,
            "cutoff": cutoff,
            "max_dates": max_dates,
        },
    ).fetchall()
    return [(row[0], int(row[1])) for row in rows]


def run_current_slate_bootstrap(
    conn: psycopg.Connection,
    *,
    api_key: str,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    run_id: int | None = None,
    cutoff: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    fetch_day=refresh_sportradar_history_day,
    readiness_fn=snapshot_readiness,
) -> BootstrapOutcome:
    cutoff = cutoff or datetime.now(UTC)
    targets = load_concrete_targets(conn, run_id=run_id, cutoff=cutoff)
    player_ids = sorted({pid for _m, p1, p2 in targets for pid in (p1, p2)})

    before = readiness_fn(conn, player_ids=player_ids, cutoff=cutoff)
    ready_before = sum(1 for pid in player_ids if before[pid].ready)
    needing = [pid for pid in player_ids if not before[pid].ready]
    after = before

    requests_consumed = 0
    results_inserted = 0
    stats_inserted = 0
    attempted: list[str] = []

    if not targets:
        reason = "no_concrete_targets"
    elif not needing:
        reason = "all_target_players_ready"
    elif max_requests < 1:
        reason = "request_cap"
    else:
        reason = "request_cap"
        dates = select_bootstrap_dates(
            conn,
            player_ids=needing,
            cutoff=cutoff,
            lookback_days=lookback_days,
            max_dates=max_requests,
        )
        if not dates:
            reason = "no_candidate_dates"
        for target_date, _score in dates:
            if requests_consumed >= max_requests:
                reason = "request_cap"
                break
            try:
                day: SportradarHistoryDayOutcome = fetch_day(
                    conn, api_key=api_key, target_date=target_date
                )
            except TransportError as exc:
                reason = f"provider_{exc.kind}"
                break
            requests_consumed += 1
            attempted.append(target_date.isoformat())
            results_inserted += day.observations_inserted
            stats_inserted += day.stat_rows_inserted
            after = readiness_fn(conn, player_ids=player_ids, cutoff=cutoff)
            if all(after[pid].ready for pid in player_ids):
                reason = "all_target_players_ready"
                break

    return BootstrapOutcome(
        concrete_matches=len(targets),
        unique_players=len(player_ids),
        ready_before=ready_before,
        ready_after=sum(1 for pid in player_ids if after[pid].ready),
        requests_consumed=requests_consumed,
        dates_attempted=tuple(attempted),
        results_inserted=results_inserted,
        stats_inserted=stats_inserted,
        stopped_reason=reason,
        readiness_before={str(pid): before[pid] for pid in player_ids},
        readiness_after={str(pid): after[pid] for pid in player_ids},
    )


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded current-slate Sportradar historical bootstrap for Agent 2"
    )
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--dsn", default=_db_dsn())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.dsn:
        print("CURRENT_SLATE_BOOTSTRAP_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    if args.max_requests < 0:
        print("CURRENT_SLATE_BOOTSTRAP_BLOCKED reason=NEGATIVE_REQUEST_CAP", flush=True)
        return 2

    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not args.dry_run and not api_key:
        print(
            "CURRENT_SLATE_BOOTSTRAP_BLOCKED reason=MISSING_SPORTRADAR_TENNIS_API_KEY",
            flush=True,
        )
        return 2

    with psycopg.connect(args.dsn, autocommit=True) as conn:
        outcome = run_current_slate_bootstrap(
            conn,
            api_key=api_key or "",
            max_requests=0 if args.dry_run else args.max_requests,
            run_id=args.run_id,
            lookback_days=args.lookback_days,
        )

    print(
        "CURRENT_SLATE_BOOTSTRAP "
        f"concrete_matches={outcome.concrete_matches} "
        f"unique_players={outcome.unique_players} "
        f"ready_before={outcome.ready_before} ready_after={outcome.ready_after} "
        f"requests_consumed={outcome.requests_consumed} "
        f"results_inserted={outcome.results_inserted} stats_inserted={outcome.stats_inserted} "
        f"stopped_reason={outcome.stopped_reason} "
        f"dates={','.join(outcome.dates_attempted) or '-'}",
        flush=True,
    )
    for pid, value in outcome.readiness_after.items():
        if not value.ready:
            print(
                "CURRENT_SLATE_PLAYER_NOT_READY "
                f"player_id={pid} matches={value.usable_matches} "
                f"serve={value.serve_points} return={value.return_points} "
                f"missing_matches={value.missing_matches}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
