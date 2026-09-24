from __future__ import annotations

"""Targeted Agent 2 threshold-crossing bootstrap.

For each not-ready current-slate player, ranks the exact remaining deficit,
requests Sportradar historical days one at a time (closest player first), and
re-measures readiness after every request. Stops early when no high-value target
remains or a request moves no gate. Bounded writes go only through the existing
canonical Sportradar history refresh path. Sanitized output only.
"""

import argparse
import json
import os
import time
from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.current_slate_bootstrap import (
    MIN_RETURN_POINTS,
    MIN_SERVE_POINTS,
    MIN_USABLE_MATCHES,
    is_placeholder_name,
    load_concrete_targets,
    select_bootstrap_dates,
    snapshot_readiness,
)
from sports_edge_tennis.live.sportradar_history_refresh import refresh_sportradar_history_day
from sports_edge_tennis.live.transport import TransportError

MIN_SERVE = MIN_SERVE_POINTS
MIN_RETURN = MIN_RETURN_POINTS
MIN_MATCHES = MIN_USABLE_MATCHES


def emit(label: str, payload: dict) -> None:
    print(f"{label} {json.dumps(payload, default=str, sort_keys=True)}", flush=True)


def _name(conn, player_id: int) -> str:
    row = conn.execute(
        "SELECT canonical_name FROM players WHERE player_id = %s", (player_id,)
    ).fetchone()
    return str(row[0]) if row else "?"


def _score(readiness) -> tuple[int, int]:
    serve_gap = max(0, MIN_SERVE - readiness.serve_points)
    return_gap = max(0, MIN_RETURN - readiness.return_points)
    return readiness.missing_matches, serve_gap + return_gap


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-requests", type=int, default=12)
    args = parser.parse_args()

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not dsn or not api_key:
        raise SystemExit("TARGETED_A2_BLOCKED missing_dsn_or_key")

    with psycopg.connect(dsn, autocommit=True) as conn:
        cutoff = datetime.now(UTC)
        targets = load_concrete_targets(conn, cutoff=cutoff)
        player_ids = sorted({pid for _m, p1, p2 in targets for pid in (p1, p2)})
        before = snapshot_readiness(conn, player_ids=player_ids, cutoff=cutoff)
        emit(
            "TARGETED_A2_BEFORE",
            {
                "concrete_matches": len(targets),
                "players": len(player_ids),
                "ready": sum(1 for pid in player_ids if before[pid].ready),
                "not_ready": sum(1 for pid in player_ids if not before[pid].ready),
            },
        )

        not_ready = [pid for pid in player_ids if not before[pid].ready]
        if not not_ready:
            emit("TARGETED_A2_DONE", {"crossings": 0, "reason": "all_ready"})
            return 0

        for pid in sorted(not_ready, key=lambda p: _score(before[p])):
            r = before[pid]
            emit(
                "TARGETED_A2_DEFICIT",
                {
                    "player_id": pid,
                    "player_name": _name(conn, pid),
                    "usable_matches": r.usable_matches,
                    "min_matches": MIN_MATCHES,
                    "serve_points": r.serve_points,
                    "min_serve_points": MIN_SERVE,
                    "return_points": r.return_points,
                    "min_return_points": MIN_RETURN,
                    "missing_matches": r.missing_matches,
                    "serve_gap": max(0, MIN_SERVE - r.serve_points),
                    "return_gap": max(0, MIN_RETURN - r.return_points),
                },
            )

        readiness = dict(before)
        requested: set[str] = set()
        requests_used = 0
        crossings = 0
        stalled = 0

        for pid in sorted(not_ready, key=lambda p: _score(before[p])):
            if requests_used >= args.max_requests or stalled >= 3:
                break
            dates = select_bootstrap_dates(
                conn, player_ids=[pid], cutoff=cutoff, max_dates=3, lookback_days=180
            )
            for target_date, _score_value in dates:
                if requests_used >= args.max_requests or stalled >= 3:
                    break
                key = target_date.isoformat()
                if key in requested:
                    continue
                requested.add(key)
                before_state = readiness[pid]
                day = None
                error_kind = None
                for attempt in range(3):
                    try:
                        # Paced to respect the trial endpoint cooldown; the live
                        # runner shares the same provider allowance.
                        time.sleep(20)
                        day = refresh_sportradar_history_day(
                            conn, api_key=api_key, target_date=target_date
                        )
                        break
                    except TransportError as exc:
                        error_kind = exc.kind
                        if exc.kind != "HTTP_429":
                            break
                if day is None:
                    emit(
                        "TARGETED_A2_REQUEST",
                        {"request": requests_used + 1, "date": key, "error": error_kind},
                    )
                    if error_kind != "HTTP_429":
                        stalled += 1
                    continue
                requests_used += 1
                readiness = snapshot_readiness(conn, player_ids=player_ids, cutoff=datetime.now(UTC))
                after_state = readiness[pid]
                crossing = (not before_state.ready) and after_state.ready
                crossings += int(crossing)
                moved = (
                    before_state.usable_matches,
                    before_state.serve_points,
                    before_state.return_points,
                ) != (
                    after_state.usable_matches,
                    after_state.serve_points,
                    after_state.return_points,
                )
                stalled = 0 if moved else stalled + 1
                emit(
                    "TARGETED_A2_REQUEST",
                    {
                        "request": requests_used,
                        "date": key,
                        "target_player": pid,
                        "target_player_name": _name(conn, pid),
                        "results_inserted": day.observations_inserted,
                        "stats_inserted": day.stat_rows_inserted,
                        "usable_before": before_state.usable_matches,
                        "usable_after": after_state.usable_matches,
                        "serve_before": before_state.serve_points,
                        "serve_after": after_state.serve_points,
                        "return_before": before_state.return_points,
                        "return_after": after_state.return_points,
                        "crossed": crossing,
                        "moved": moved,
                    },
                )
                if all(readiness[p].ready for p in player_ids):
                    break
            if all(readiness[p].ready for p in player_ids):
                break

        after = readiness
        modelable_before = sum(
            1 for _m, p1, p2 in targets if before[p1].ready and before[p2].ready
        )
        modelable_after = sum(
            1 for _m, p1, p2 in targets if after[p1].ready and after[p2].ready
        )
        emit(
            "TARGETED_A2_AFTER",
            {
                "requests_used": requests_used,
                "crossings": crossings,
                "ready": sum(1 for pid in player_ids if after[pid].ready),
                "not_ready": sum(1 for pid in player_ids if not after[pid].ready),
                "modelable_before": modelable_before,
                "modelable_after": modelable_after,
                "concrete_matches": len(targets),
            },
        )
    emit("TARGETED_A2_DONE", {"writes": requests_used})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
