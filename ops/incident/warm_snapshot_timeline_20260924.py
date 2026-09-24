from __future__ import annotations

"""Read-only snapshot timeline for the warm-cache OddsPapi validation match."""

import json
import os

import psycopg


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    match_id = int(os.environ.get("WARM_MATCH_ID") or 130874)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        rows = conn.execute(
            """
            SELECT action_time, source_system, bookmaker_code, snapshots
            FROM (
                SELECT source_observed_at AS action_time, source_system, bookmaker_code, 1 AS snapshots
                FROM odds_snapshots
                WHERE match_id = %s AND source_system IN ('SHARP','OFFERED')
            ) t
            ORDER BY action_time DESC
            LIMIT 20
            """,
            (match_id,),
        ).fetchall()
        aggregates = conn.execute(
            """
            SELECT min(source_observed_at), max(source_observed_at), count(*)
            FROM odds_snapshots
            WHERE match_id = %s AND source_system IN ('SHARP','OFFERED')
            """,
            (match_id,),
        ).fetchone()
    print(
        "WARM_SNAP_TIMELINE "
        + json.dumps(
            {
                "match_id": match_id,
                "min_source_observed_at": aggregates[0],
                "max_source_observed_at": aggregates[1],
                "rows": int(aggregates[2]),
                "latest": [
                    {"source_observed_at": r[0], "source_system": r[1], "bookmaker_code": r[2]}
                    for r in rows[:10]
                ],
            },
            default=str,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
