from __future__ import annotations

"""Read-only duplicate-consolidation planning probe.

Classifies the known Sportradar duplicate population into SAFE_EXACT / CONFLICT /
AMBIGUOUS and measures whether the stranded evidence is relevant to the current
concrete slate's Agent 2 readiness. Writes nothing. Prints sanitized JSON.
Never prints DSNs, passwords, or keys.
"""

import argparse
import json
import os
import re
from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.live.current_slate_bootstrap import snapshot_readiness

PLACEHOLDER = re.compile(r"^(R\d+P\d+|Qf\d+|WQF\d+)$", re.IGNORECASE)
CYCLE_WINDOW_MINUTES = 30
TIGHT_START_DELTA_SECONDS = 3600


def emit(label: str, payload: dict) -> None:
    print(f"{label} {json.dumps(payload, default=str, sort_keys=True)}", flush=True)


def _dsn() -> str:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DUPLICATE_PLAN_BLOCKED missing_dsn")
    return dsn


PAIR_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (mv.match_id)
        mv.match_id, mv.player_1_id, mv.player_2_id, mv.match_start_at
    FROM match_versions mv
    ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
),
sr AS (SELECT DISTINCT match_id FROM match_source_ids WHERE source_system = 'SPORTRADAR')
SELECT sr_match.match_id, other.match_id,
       abs(extract(epoch FROM (sr_match.match_start_at - other.match_start_at)))::int
FROM latest sr_match
JOIN sr ON sr.match_id = sr_match.match_id
JOIN latest other
  ON other.match_id <> sr_match.match_id
 AND other.match_start_at IS NOT NULL
 AND sr_match.match_start_at IS NOT NULL
 AND abs(extract(epoch FROM (sr_match.match_start_at - other.match_start_at))) <= 10800
 AND (
     (other.player_1_id = sr_match.player_1_id AND other.player_2_id = sr_match.player_2_id)
     OR (other.player_1_id = sr_match.player_2_id AND other.player_2_id = sr_match.player_1_id)
 )
LEFT JOIN sr other_sr ON other_sr.match_id = other.match_id
WHERE other_sr.match_id IS NULL
ORDER BY sr_match.match_id, other.match_id
"""


def _match_detail(conn, match_id: int) -> dict:
    ver = conn.execute(
        """
        SELECT mv.player_1_id, mv.player_2_id, mv.match_start_at, mv.surface::text,
               mv.match_status::text, te.tournament_id, t.canonical_name
        FROM match_versions mv
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE mv.match_id = %s
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    result = conn.execute(
        """
        SELECT outcome_status::text, winner_side, sets_won_player_1, sets_won_player_2
        FROM match_result_observations
        WHERE match_id = %s
        ORDER BY as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    stats = conn.execute(
        """
        SELECT count(*), count(DISTINCT player_id),
               count(*) FILTER (WHERE source_system = 'SPORTRADAR')
        FROM match_stat_observations WHERE match_id = %s
        """,
        (match_id,),
    ).fetchone()
    sources = conn.execute(
        "SELECT DISTINCT source_system FROM match_source_ids WHERE match_id = %s ORDER BY source_system",
        (match_id,),
    ).fetchall()
    return {
        "match_id": match_id,
        "start": ver[2] if ver else None,
        "surface": ver[3] if ver else None,
        "status": ver[4] if ver else None,
        "tournament_id": int(ver[5]) if ver and ver[5] is not None else None,
        "tournament": ver[6] if ver else None,
        "result_status": result[0] if result else None,
        "winner_side": result[1] if result else None,
        "sets": (result[2], result[3]) if result else None,
        "stat_rows": int(stats[0]) if stats else 0,
        "stat_players": int(stats[1]) if stats else 0,
        "sportradar_stat_rows": int(stats[2]) if stats else 0,
        "source_systems": [r[0] for r in sources],
    }


def _current_slate(conn) -> tuple[list[int], list[int]]:
    run = conn.execute(
        """
        SELECT prediction_run_id, started_at, as_of FROM prediction_runs
        WHERE run_status = 'COMPLETED' AND experiment_id IS NULL
          AND prediction_contract_version = 'predictive_agent_result_v1'
          AND prediction_finalized_at IS NOT NULL
        ORDER BY started_at DESC, prediction_run_id DESC LIMIT 1
        """,
    ).fetchone()
    if run is None:
        return [], []
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
    players: list[int] = []
    concrete: list[int] = []
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
        concrete.append(match_id)
        players.extend((int(row[0]), int(row[1])))
    return concrete, sorted(set(players))


def _classify(original: dict, duplicate: dict, originals_for_dup: int) -> tuple[str, str]:
    if originals_for_dup > 1:
        return "AMBIGUOUS", "multiple_originals"
    if original["sportradar_stat_rows"] > 0 or "SPORTRADAR" in original["source_systems"]:
        return "CONFLICT", "original_already_sportradar"
    if duplicate["surface"] is not None and original["surface"] is not None and duplicate["surface"] != original["surface"]:
        return "CONFLICT", "surface_mismatch"
    if (
        duplicate["tournament_id"] is not None
        and original["tournament_id"] is not None
        and duplicate["tournament_id"] != original["tournament_id"]
    ):
        return "CONFLICT", "tournament_mismatch"
    o_status = original["result_status"]
    d_status = duplicate["result_status"]
    if o_status is not None and d_status is not None:
        if o_status != d_status:
            return "CONFLICT", "result_status_mismatch"
        if original["winner_side"] is not None and duplicate["winner_side"] is not None:
            if original["winner_side"] != duplicate["winner_side"]:
                return "CONFLICT", "winner_mismatch"
    if original["start"] is None or duplicate["start"] is None:
        return "CONFLICT", "missing_start"
    return "SAFE_EXACT", "single_original_exact"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--example-limit", type=int, default=15)
    args = parser.parse_args()

    with psycopg.connect(_dsn(), autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        pairs = conn.execute(PAIR_SQL).fetchall()
        concrete, current_players = _current_slate(conn)
        current_player_set = set(current_players)

        originals_for_dup: dict[int, set[int]] = {}
        for sr_id, hist_id, _delta in pairs:
            originals_for_dup.setdefault(int(sr_id), set()).add(int(hist_id))

        counts = {"SAFE_EXACT": 0, "CONFLICT": 0, "AMBIGUOUS": 0}
        safe_examples = []
        conflict_examples = []
        ambiguous_examples = []
        recoverable_pairs = 0
        recoverable_for_current_players = 0

        for sr_id, hist_id, delta in pairs:
            original = _match_detail(conn, int(hist_id))
            duplicate = _match_detail(conn, int(sr_id))
            verdict, reason = _classify(original, duplicate, len(originals_for_dup[int(sr_id)]))
            counts[verdict] += 1
            recoverable = duplicate["stat_rows"] > original["stat_rows"] or (
                duplicate["result_status"] is not None and original["result_status"] is None
            )
            if recoverable:
                recoverable_pairs += 1
            pair_players = set()
            if duplicate["stat_players"]:
                pair_players = {int(r[0]) for r in conn.execute(
                    "SELECT DISTINCT player_id FROM match_stat_observations WHERE match_id = %s",
                    (int(sr_id),),
                ).fetchall()}
            if pair_players & current_player_set:
                recoverable_for_current_players += 1
            record = {
                "sportradar_match_id": int(sr_id),
                "original_match_id": int(hist_id),
                "verdict": verdict,
                "reason": reason,
                "start_delta_seconds": int(delta),
                "original": original,
                "duplicate": duplicate,
                "recoverable": recoverable,
                "affects_current_players": bool(pair_players & current_player_set),
            }
            if verdict == "SAFE_EXACT" and len(safe_examples) < args.example_limit:
                safe_examples.append(record)
            elif verdict == "CONFLICT" and len(conflict_examples) < args.example_limit:
                conflict_examples.append(record)
            elif verdict == "AMBIGUOUS" and len(ambiguous_examples) < args.example_limit:
                ambiguous_examples.append(record)

        emit(
            "DUPLICATE_PLAN_SUMMARY",
            {
                "pairs_examined": len(pairs),
                "SAFE_EXACT": counts["SAFE_EXACT"],
                "CONFLICT": counts["CONFLICT"],
                "AMBIGUOUS": counts["AMBIGUOUS"],
                "recoverable_pairs": recoverable_pairs,
                "recoverable_pairs_affecting_current_players": recoverable_for_current_players,
                "current_concrete_matches": len(concrete),
                "current_real_players": len(current_players),
            },
        )
        for record in safe_examples:
            emit("DUPLICATE_PLAN_SAFE_EXACT", record)
        for record in conflict_examples:
            emit("DUPLICATE_PLAN_CONFLICT", record)
        for record in ambiguous_examples:
            emit("DUPLICATE_PLAN_AMBIGUOUS", record)

        if current_players:
            readiness = snapshot_readiness(conn, player_ids=current_players, cutoff=datetime.now(UTC))
            emit(
                "DUPLICATE_PLAN_A2_READINESS",
                {
                    "ready": sum(1 for pid in current_players if readiness[pid].ready),
                    "not_ready": sum(1 for pid in current_players if not readiness[pid].ready),
                },
            )
    emit("DUPLICATE_PLAN_DONE", {"writes": 0})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
