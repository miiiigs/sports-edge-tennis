from __future__ import annotations

import json
import os
from collections import defaultdict

import psycopg


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("transaction_read_only is not on")

        rows = conn.execute(
            """
            WITH latest_result AS (
                SELECT DISTINCT ON (match_id)
                    match_id,
                    outcome_status::text AS outcome_status,
                    winner_side,
                    retired_side
                FROM match_result_observations
                ORDER BY match_id, as_of DESC, source_observed_at DESC,
                         match_result_observation_id DESC
            ),
            latest_version AS (
                SELECT DISTINCT ON (match_id)
                    match_id,
                    match_start_at
                FROM match_versions
                ORDER BY match_id, as_of DESC, source_observed_at DESC,
                         match_version_id DESC
            ),
            sr_link AS (
                SELECT DISTINCT ON (match_id)
                    match_id,
                    source_match_id
                FROM match_source_ids
                WHERE source_system = 'SPORTRADAR'
                ORDER BY match_id, match_source_id DESC
            )
            SELECT
                lv.match_start_at::date AS match_date,
                lr.outcome_status,
                lr.winner_side,
                lr.retired_side,
                lr.match_id,
                sl.source_match_id
            FROM latest_result lr
            JOIN latest_version lv USING (match_id)
            JOIN sr_link sl USING (match_id)
            WHERE lv.match_start_at::date BETWEEN DATE '2026-09-10' AND DATE '2026-09-21'
              AND lr.outcome_status IN ('RETIRED', 'WALKOVER', 'CANCELLED')
            ORDER BY match_date DESC, lr.outcome_status, lr.match_id
            """
        ).fetchall()
        conn.rollback()

    by_date: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    examples = []
    for match_date, status, winner_side, retired_side, match_id, event_id in rows:
        by_date[str(match_date)][str(status)] += 1
        examples.append(
            {
                "date": str(match_date),
                "status": str(status),
                "winner_side": winner_side,
                "retired_side": retired_side,
                "match_id": int(match_id),
                "event_id": str(event_id),
            }
        )

    result = {
        "status": "OK",
        "transaction_read_only": "on",
        "writes": 0,
        "total_candidates": len(rows),
        "by_date": {d: dict(v) for d, v in sorted(by_date.items(), reverse=True)},
        "examples": examples,
    }
    print("SPORTRADAR_EXCEPTION_CANDIDATES " + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
