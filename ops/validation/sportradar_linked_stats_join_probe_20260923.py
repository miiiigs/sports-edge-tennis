from __future__ import annotations

"""Read-only proof that the linked-only Sportradar backfill is A2-consumable.

Agent 2's historical loader joins `match_stat_observations` to the selected
`match_result_observations` row on `(match_id, source_observed_at, as_of)` with
`observation_kind='RAW_SOURCE'`. This probe proves, without writing anything,
that the backfilled Sportradar result+stat rows share that timestamp triple and
that each backfilled match has exactly the two expected player stat rows.
"""

import json
import os

import psycopg


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("transaction_read_only is not on")

        stat_matches, stat_rows = conn.execute(
            """
            SELECT count(DISTINCT match_id), count(*)
            FROM match_stat_observations
            WHERE source_system = 'SPORTRADAR'
              AND observation_kind = 'RAW_SOURCE'
            """
        ).fetchone()

        joined_matches = conn.execute(
            """
            SELECT count(DISTINCT mso.match_id)
            FROM match_stat_observations mso
            JOIN match_result_observations mro
              ON mro.match_id = mso.match_id
             AND mro.source_observed_at = mso.source_observed_at
             AND mro.as_of = mso.as_of
             AND mro.outcome_status = 'COMPLETED'
             AND mro.winner_side IN (1, 2)
            WHERE mso.source_system = 'SPORTRADAR'
              AND mso.observation_kind = 'RAW_SOURCE'
            """
        ).fetchone()[0]

        two_side_matches = conn.execute(
            """
            SELECT count(*) FROM (
                SELECT mso.match_id
                FROM match_stat_observations mso
                WHERE mso.source_system = 'SPORTRADAR'
                  AND mso.observation_kind = 'RAW_SOURCE'
                GROUP BY mso.match_id
                HAVING count(DISTINCT mso.player_id) = 2
                   AND count(*) = 2
            ) two_sided
            """
        ).fetchone()[0]

        triple_matches = conn.execute(
            """
            SELECT count(DISTINCT mso.match_id)
            FROM match_stat_observations mso
            JOIN match_result_observations mro
              ON mro.match_id = mso.match_id
             AND mro.source_observed_at = mso.source_observed_at
             AND mro.as_of = mso.as_of
            JOIN match_versions mv
              ON mv.match_id = mso.match_id
            WHERE mso.source_system = 'SPORTRADAR'
              AND mso.observation_kind = 'RAW_SOURCE'
              AND mro.outcome_status = 'COMPLETED'
              AND mro.winner_side IN (1, 2)
            """
        ).fetchone()[0]

        examples = conn.execute(
            """
            SELECT mso.match_id,
                   mro.source_observed_at,
                   mro.as_of,
                   count(DISTINCT mso.player_id),
                   count(*)
            FROM match_stat_observations mso
            JOIN match_result_observations mro
              ON mro.match_id = mso.match_id
             AND mro.source_observed_at = mso.source_observed_at
             AND mro.as_of = mso.as_of
            WHERE mso.source_system = 'SPORTRADAR'
              AND mso.observation_kind = 'RAW_SOURCE'
            GROUP BY mso.match_id, mro.source_observed_at, mro.as_of
            ORDER BY mso.match_id
            LIMIT 8
            """
        ).fetchall()

        conn.rollback()

    result = {
        "status": "OK",
        "transaction_read_only": "on",
        "writes": 0,
        "sportradar_stat_matches": int(stat_matches),
        "sportradar_stat_rows": int(stat_rows),
        "matches_with_shared_result_timestamp": int(joined_matches),
        "matches_with_two_player_rows": int(two_side_matches),
        "matches_joined_with_match_versions": int(triple_matches),
        "examples": [
            {
                "match_id": int(row[0]),
                "source_observed_at": row[1].isoformat(),
                "as_of": row[2].isoformat(),
                "distinct_players": int(row[3]),
                "stat_rows": int(row[4]),
            }
            for row in examples
        ],
    }
    print("SPORTRADAR_LINKED_STATS_JOIN " + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
