from __future__ import annotations

"""Read-only measurement of A2 duplicate canonicalization against production.

Computes current-slate A2 readiness with the canonicalized loader (AFTER) and
with the counterpart lookup disabled (BEFORE), then reports per-player deltas and
concrete-match eligibility. Writes nothing. Sanitized output only.
"""

import json
import os
import re
from datetime import UTC, datetime

import psycopg

import sports_edge_tennis.data.agent2_history as a2
from sports_edge_tennis.live.current_slate_bootstrap import snapshot_readiness

PLACEHOLDER = re.compile(r"^(R\d+P\d+|Qf\d+|WQF\d+)$", re.IGNORECASE)
CYCLE_WINDOW_MINUTES = 30


def emit(label: str, payload: dict) -> None:
    print(f"{label} {json.dumps(payload, default=str, sort_keys=True)}", flush=True)


def _concrete(conn):
    run = conn.execute(
        """
        SELECT prediction_run_id, started_at, as_of FROM prediction_runs
        WHERE run_status = 'COMPLETED' AND experiment_id IS NULL
          AND prediction_contract_version = 'predictive_agent_result_v1'
          AND prediction_finalized_at IS NOT NULL
        ORDER BY started_at DESC, prediction_run_id DESC LIMIT 1
        """,
    ).fetchone()
    run_ids = [
        int(r[0])
        for r in conn.execute(
            """
            SELECT prediction_run_id FROM prediction_runs
            WHERE run_status = 'COMPLETED' AND experiment_id IS NULL
              AND prediction_contract_version = 'predictive_agent_result_v1'
              AND started_at >= %s::timestamptz - (%s || ' minutes')::interval
            """,
            (run[1], CYCLE_WINDOW_MINUTES),
        ).fetchall()
    ]
    match_ids = [
        int(r[0])
        for r in conn.execute(
            "SELECT DISTINCT match_id FROM prediction_run_targets WHERE prediction_run_id = ANY(%s)",
            (run_ids,),
        ).fetchall()
    ]
    concrete = []
    for match_id in match_ids:
        row = conn.execute(
            """
            SELECT mv.player_1_id, mv.player_2_id, p1.canonical_name, p2.canonical_name
            FROM match_versions mv
            JOIN players p1 ON p1.player_id = mv.player_1_id
            JOIN players p2 ON p2.player_id = mv.player_2_id
            WHERE mv.match_id = %s AND mv.source_observed_at <= %s AND mv.as_of <= %s
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC LIMIT 1
            """,
            (match_id, run[2], run[2]),
        ).fetchone()
        if row is None:
            continue
        if PLACEHOLDER.match((row[2] or "").strip()) or PLACEHOLDER.match((row[3] or "").strip()):
            continue
        concrete.append((match_id, int(row[0]), int(row[1])))
    return concrete


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("A2_CANON_BLOCKED missing_dsn")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        cutoff = datetime.now(UTC)
        concrete = _concrete(conn)
        player_ids = sorted({pid for _m, p1, p2 in concrete for pid in (p1, p2)})
        emit(
            "A2_CANON_SLATE",
            {"concrete_matches": len(concrete), "real_players": len(player_ids)},
        )

        original = a2._load_canonical_counterparts

        a2._load_canonical_counterparts = lambda conn, *, observations, cutoff: []
        try:
            before = snapshot_readiness(conn, player_ids=player_ids, cutoff=cutoff)
        except Exception as exc:  # noqa: BLE001
            emit("A2_CANON_ERROR", {"phase": "before", "error": type(exc).__name__})
            return 0
        finally:
            a2._load_canonical_counterparts = original

        try:
            after = snapshot_readiness(conn, player_ids=player_ids, cutoff=cutoff)
        except Exception as exc:  # noqa: BLE001
            emit("A2_CANON_ERROR", {"phase": "after", "error": type(exc).__name__})
            return 0

        emit(
            "A2_CANON_READINESS",
            {
                "before_ready": sum(1 for pid in player_ids if before[pid].ready),
                "before_not_ready": sum(1 for pid in player_ids if not before[pid].ready),
                "after_ready": sum(1 for pid in player_ids if after[pid].ready),
                "after_not_ready": sum(1 for pid in player_ids if not after[pid].ready),
            },
        )

        improved = 0
        for pid in player_ids:
            b, f = before[pid], after[pid]
            if (b.usable_matches, b.serve_points, b.return_points) != (
                f.usable_matches,
                f.serve_points,
                f.return_points,
            ):
                improved += 1
                emit(
                    "A2_CANON_PLAYER_DELTA",
                    {
                        "player_id": pid,
                        "before": {
                            "usable_matches": b.usable_matches,
                            "serve_points": b.serve_points,
                            "return_points": b.return_points,
                            "ready": b.ready,
                        },
                        "after": {
                            "usable_matches": f.usable_matches,
                            "serve_points": f.serve_points,
                            "return_points": f.return_points,
                            "ready": f.ready,
                        },
                    },
                )
        emit("A2_CANON_PLAYER_DELTA_COUNT", {"changed_players": improved})

        before_modelable = sum(
            1
            for _m, p1, p2 in concrete
            if before[p1].ready and before[p2].ready
        )
        after_modelable = sum(
            1 for _m, p1, p2 in concrete if after[p1].ready and after[p2].ready
        )
        emit(
            "A2_CANON_MATCH_ELIGIBILITY",
            {
                "before_both_ready": before_modelable,
                "after_both_ready": after_modelable,
                "concrete_matches": len(concrete),
            },
        )

        # Surface/counterpart diagnostics: how many Sportradar observations resolve.
        try:
            raw_observations = a2.load_agent2_history(conn, cutoff=cutoff)
            counterparts = a2._load_canonical_counterparts(
                conn, observations=raw_observations, cutoff=cutoff
            )
            canonicalized = a2.canonicalize_agent2_observations(
                raw_observations, counterparts, cutoff=cutoff
            )
            emit(
                "A2_CANON_DIAGNOSTIC",
                {
                    "raw_observations": len(raw_observations),
                    "counterparts_found": len(counterparts),
                    "canonicalized_observations": len(canonicalized),
                    "observations_collapsed": len(raw_observations) - len(canonicalized),
                },
            )
        except Exception as exc:  # noqa: BLE001
            emit("A2_CANON_ERROR", {"phase": "diagnostic", "error": type(exc).__name__})
    emit("A2_CANON_DONE", {"writes": 0})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
