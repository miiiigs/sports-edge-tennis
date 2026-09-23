from __future__ import annotations

"""Read-only A2/A3 evidence-coverage audit for production runs 505-507.

Reconstructs the exact A2 threshold components (usable matches, serve points,
return points) for the active production players and measures A3 direct support
between target players. No writes, no provider calls.
"""

import argparse
import json
import os
from collections import Counter

import psycopg

RUN_IDS = (505, 506, 507)
A2_MIN_MATCHES = 5
A2_MIN_SERVE = 250
A2_MIN_RETURN = 250


def _targets(conn) -> list[dict]:
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, mv.player_1_id, mv.player_2_id,
                mv.match_start_at, mv.surface::text AS surface
            FROM match_versions mv
            WHERE mv.match_id IN (
                SELECT match_id FROM prediction_run_targets
                WHERE prediction_run_id = ANY(%s)
            )
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT match_id, player_1_id, player_2_id, match_start_at, surface
        FROM latest
        ORDER BY match_start_at, match_id
        """,
        (list(RUN_IDS),),
    ).fetchall()
    return [
        {
            "match_id": int(r[0]),
            "p1": int(r[1]),
            "p2": int(r[2]),
            "start": r[3],
            "surface": r[4],
        }
        for r in rows
    ]


def _a2_player_stats(conn) -> dict[int, dict]:
    rows = conn.execute(
        """
        WITH usable AS (
            SELECT mso.match_id
            FROM match_stat_observations mso
            WHERE mso.source_system = 'SPORTRADAR'
              AND mso.observation_kind = 'RAW_SOURCE'
              AND mso.service_points_played > 0
              AND mso.first_serve_points_won IS NOT NULL
              AND mso.second_serve_points_won IS NOT NULL
            GROUP BY mso.match_id
            HAVING count(DISTINCT mso.player_id) = 2 AND count(*) = 2
        ),
        rows AS (
            SELECT mso.match_id, mso.player_id, mso.service_points_played
            FROM match_stat_observations mso
            JOIN usable u ON u.match_id = mso.match_id
            WHERE mso.source_system = 'SPORTRADAR'
              AND mso.observation_kind = 'RAW_SOURCE'
        )
        SELECT r.player_id,
               count(DISTINCT r.match_id) AS usable_matches,
               coalesce(sum(r.service_points_played), 0) AS serve_played,
               coalesce(sum(o.service_points_played), 0) AS return_played
        FROM rows r
        JOIN rows o ON o.match_id = r.match_id AND o.player_id <> r.player_id
        GROUP BY r.player_id
        """
    ).fetchall()
    return {
        int(r[0]): {
            "usable_matches": int(r[1]),
            "serve_played": int(r[2]),
            "return_played": int(r[3]),
        }
        for r in rows
    }


def _a3_direct_support(conn, p1: int, p2: int) -> int:
    row = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT DISTINCT mro.match_id
            FROM match_result_observations mro
            JOIN LATERAL (
                SELECT player_1_id, player_2_id
                FROM match_versions
                WHERE match_id = mro.match_id
                ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
            WHERE mro.outcome_status = 'COMPLETED'
              AND (
                (mv.player_1_id = %s AND mv.player_2_id = %s)
                OR (mv.player_1_id = %s AND mv.player_2_id = %s)
              )
        ) t
        """,
        (p1, p2, p2, p1),
    ).fetchone()
    return int(row[0])


def _a3_player_degree(conn, player_ids: list[int]) -> dict[int, int]:
    if not player_ids:
        return {}
    rows = conn.execute(
        """
        SELECT mv.player_1_id, mv.player_2_id
        FROM match_result_observations mro
        JOIN LATERAL (
            SELECT player_1_id, player_2_id
            FROM match_versions
            WHERE match_id = mro.match_id
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        WHERE mro.outcome_status = 'COMPLETED'
          AND (mv.player_1_id = ANY(%s) OR mv.player_2_id = ANY(%s))
        """,
        (list(player_ids), list(player_ids)),
    ).fetchall()
    degree: dict[int, set[int]] = {}
    for a, b in rows:
        a = int(a)
        b = int(b)
        degree.setdefault(a, set()).add(b)
        degree.setdefault(b, set()).add(a)
    return {pid: len(opp) for pid, opp in degree.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("A2A3_AUDIT_BLOCKED missing_dsn")
        return 2

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        targets = _targets(conn)
        a2 = _a2_player_stats(conn)
        active_players = sorted({t["p1"] for t in targets} | {t["p2"] for t in targets})

        a2_target = Counter()
        a2_reasons = Counter()
        a3_direct = Counter()
        surface_gap = 0
        for t in targets:
            if t["surface"] is None:
                surface_gap += 1
            s1 = a2.get(t["p1"], {"usable_matches": 0, "serve_played": 0, "return_played": 0})
            s2 = a2.get(t["p2"], {"usable_matches": 0, "serve_played": 0, "return_played": 0})

            def fails(s):
                reasons = []
                if s["usable_matches"] < A2_MIN_MATCHES:
                    reasons.append("INSUFFICIENT_HISTORY")
                if s["serve_played"] < A2_MIN_SERVE or s["return_played"] < A2_MIN_RETURN:
                    reasons.append("INSUFFICIENT_POINT_HISTORY")
                return reasons

            f1, f2 = fails(s1), fails(s2)
            if not f1 and not f2:
                a2_target["BOTH_PASS"] += 1
            elif f1 and f2:
                a2_target["BOTH_FAIL"] += 1
            else:
                a2_target["ONE_FAILS"] += 1
            for reason in set(f1 + f2):
                a2_reasons[reason] += 1

            direct = _a3_direct_support(conn, t["p1"], t["p2"])
            a3_direct[">=direct_raw_support(2)" if direct >= 2 else ("1" if direct == 1 else "0")] += 1

        degree = _a3_player_degree(conn, active_players[:40])
        active_with_a2 = sum(
            1
            for p in active_players
            if a2.get(p, {}).get("usable_matches", 0) >= A2_MIN_MATCHES
            and a2.get(p, {}).get("serve_played", 0) >= A2_MIN_SERVE
            and a2.get(p, {}).get("return_played", 0) >= A2_MIN_RETURN
        )
        active_with_any_a2 = sum(1 for p in active_players if a2.get(p, {}).get("usable_matches", 0) > 0)
        conn.rollback()

    print(
        "A2A3_COVERAGE_AUDIT "
        + json.dumps(
            {
                "transaction_read_only": read_only,
                "writes": 0,
                "runs": list(RUN_IDS),
                "targets": len(targets),
                "active_players": len(active_players),
                "a2_target_classification": dict(a2_target),
                "a2_failed_reason_counts": dict(a2_reasons),
                "active_players_with_any_sportradar_stats": active_with_any_a2,
                "active_players_meeting_full_a2_thresholds": active_with_a2,
                "a3_direct_support_distribution": dict(a3_direct),
                "a3_sampled_adjacency_zero": sum(1 for d in degree.values() if d == 0),
                "a3_sampled_players": len(degree),
                "surface_gap_targets": surface_gap,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
