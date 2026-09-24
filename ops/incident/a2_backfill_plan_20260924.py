from __future__ import annotations

"""Read-only A2 active-player deficit + backfill request plan (runs 505-507)."""

import argparse
import json
import os
from collections import Counter

import psycopg

RUN_IDS = (505, 506, 507)
A2_MIN_MATCHES = 5
A2_MIN_SERVE = 250
A2_MIN_RETURN = 250


def main() -> int:
    argparse.ArgumentParser().parse_args()
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("A2_PLAN_BLOCKED missing_dsn")
        return 2

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()

        active = conn.execute(
            """
            WITH targets AS (
                SELECT DISTINCT match_id FROM prediction_run_targets
                WHERE prediction_run_id = ANY(%s)
            ),
            latest AS (
                SELECT DISTINCT ON (mv.match_id) mv.match_id, mv.player_1_id, mv.player_2_id
                FROM match_versions mv
                WHERE mv.match_id IN (SELECT match_id FROM targets)
                ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
            )
            SELECT player_1_id FROM latest UNION SELECT player_2_id FROM latest
            """,
            (list(RUN_IDS),),
        ).fetchall()
        active_ids = [int(r[0]) for r in active]

        # Historical completed matches involving active players, with Sportradar link flag.
        rows = conn.execute(
            """
            WITH active AS (SELECT unnest(%s::bigint[]) AS pid),
            hist AS (
                SELECT DISTINCT ON (mro.match_id)
                    mro.match_id, mv.match_start_at, mv.player_1_id, mv.player_2_id
                FROM match_result_observations mro
                JOIN LATERAL (
                    SELECT match_start_at, player_1_id, player_2_id FROM match_versions
                    WHERE match_id = mro.match_id
                    ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
                    LIMIT 1
                ) mv ON TRUE
                WHERE mro.outcome_status = 'COMPLETED'
                  AND (mv.player_1_id IN (SELECT pid FROM active) OR mv.player_2_id IN (SELECT pid FROM active))
            )
            SELECT h.match_start_at::date AS d,
                   count(DISTINCT h.match_id) AS matches,
                   count(DISTINCT h.match_id) FILTER (
                       WHERE EXISTS (
                           SELECT 1 FROM match_source_ids msi
                           WHERE msi.match_id = h.match_id AND msi.source_system = 'SPORTRADAR'
                       )
                   ) AS linked
            FROM hist h
            WHERE h.match_start_at IS NOT NULL
            GROUP BY d
            ORDER BY count(DISTINCT h.match_id) DESC
            """,
            (active_ids,),
        ).fetchall()
        date_plan = [
            {"date": r[0].isoformat(), "active_match_rows": int(r[1]), "already_sportradar_linked": int(r[2])}
            for r in rows
        ]

        # Per-player current usable sportradar stat depth (same definition as prior audit).
        depth = conn.execute(
            """
            WITH usable AS (
                SELECT mso.match_id
                FROM match_stat_observations mso
                WHERE mso.source_system = 'SPORTRADAR' AND mso.observation_kind = 'RAW_SOURCE'
                  AND mso.service_points_played > 0
                  AND mso.first_serve_points_won IS NOT NULL
                  AND mso.second_serve_points_won IS NOT NULL
                GROUP BY mso.match_id
                HAVING count(DISTINCT mso.player_id) = 2 AND count(*) = 2
            ),
            parts AS (
                SELECT u.match_id, mv.player_1_id AS pid, mv.player_2_id AS opp
                FROM usable u JOIN LATERAL (SELECT player_1_id, player_2_id FROM match_versions WHERE match_id=u.match_id ORDER BY as_of DESC LIMIT 1) mv ON TRUE
                UNION ALL
                SELECT u.match_id, mv.player_2_id, mv.player_1_id
                FROM usable u JOIN LATERAL (SELECT player_1_id, player_2_id FROM match_versions WHERE match_id=u.match_id ORDER BY as_of DESC LIMIT 1) mv ON TRUE
            )
            SELECT pid, count(DISTINCT match_id) FROM parts WHERE pid = ANY(%s) GROUP BY pid
            """,
            (active_ids,),
        ).fetchall()
        conn.rollback()

    depth_map = {int(r[0]): int(r[1]) for r in depth}
    buckets = Counter()
    for pid in active_ids:
        m = depth_map.get(pid, 0)
        if m >= A2_MIN_MATCHES:
            buckets["usable_matches_ge5"] += 1
        elif m > 0:
            buckets["partial_1_4"] += 1
        else:
            buckets["none_0"] += 1

    top_dates = date_plan[:15]
    print(
        "A2_BACKFILL_PLAN "
        + json.dumps(
            {
                "transaction_read_only": read_only,
                "writes": 0,
                "active_players": len(active_ids),
                "sportradar_usable_match_buckets": dict(buckets),
                "dates_with_active_player_history": len(date_plan),
                "top_dates_by_active_coverage": top_dates,
                "request_ceiling_policy": "1 daily-summary request per historical date",
                "provider_calls": 0,
            },
            sort_keys=True,
        )[:12000]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
