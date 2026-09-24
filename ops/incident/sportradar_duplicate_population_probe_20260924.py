"""Read-only Sportradar terminal duplicate population probe.

Reports the canonical matches that the pre-fix ingestion duplicated: a match
that carries a SPORTRADAR match source id alongside one or more historical
canonical matches with the same exact player pair and a start time inside the
overlap window.

Writes nothing. Run it against a replica/maintenance DSN once ``TENNIS_DATABASE_URL``
or ``DATABASE_URL`` is available; integration can then decide whether
deterministic consolidation of this specific population is safe.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


DUPLICATE_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (mv.match_id)
        mv.match_id,
        mv.player_1_id,
        mv.player_2_id,
        mv.match_start_at,
        mv.match_status::text AS match_status
    FROM match_versions mv
    ORDER BY
        mv.match_id,
        mv.as_of DESC,
        mv.source_observed_at DESC,
        mv.match_version_id DESC
),
sr AS (
    SELECT DISTINCT match_id AS match_id
    FROM match_source_ids
    WHERE source_system = 'SPORTRADAR'
),
version_count AS (
    SELECT match_id, count(*) AS version_count
    FROM match_versions
    GROUP BY match_id
),
stat_count AS (
    SELECT match_id, count(*) AS stat_count
    FROM match_stat_observations
    WHERE source_system = 'SPORTRADAR'
    GROUP BY match_id
),
result_count AS (
    SELECT match_id, count(*) AS result_count
    FROM match_result_observations
    GROUP BY match_id
)
SELECT
    sr_match.match_id AS sportradar_match_id,
    other.match_id AS historical_match_id,
    sr_match.player_1_id,
    sr_match.player_2_id,
    sr_match.match_start_at,
    sr_match.match_status AS sportradar_status,
    other.match_status AS historical_status,
    abs(extract(epoch FROM (sr_match.match_start_at - other.match_start_at)))::int
        AS start_delta_seconds,
    COALESCE(sr_stat.stat_count, 0) AS sportradar_match_stats,
    COALESCE(other_stat.stat_count, 0) AS historical_match_stats,
    COALESCE(sr_result.result_count, 0) AS sportradar_match_results,
    COALESCE(other_result.result_count, 0) AS historical_match_results
FROM latest sr_match
JOIN sr ON sr.match_id = sr_match.match_id
JOIN latest other
  ON other.match_id <> sr_match.match_id
 AND other.match_start_at IS NOT NULL
 AND sr_match.match_start_at IS NOT NULL
 AND abs(extract(epoch FROM (sr_match.match_start_at - other.match_start_at))) <= 10800
 AND (
     (other.player_1_id = sr_match.player_1_id AND other.player_2_id = sr_match.player_2_id)
     OR
     (other.player_1_id = sr_match.player_2_id AND other.player_2_id = sr_match.player_1_id)
 )
LEFT JOIN sr other_sr ON other_sr.match_id = other.match_id
LEFT JOIN stat_count sr_stat ON sr_stat.match_id = sr_match.match_id
LEFT JOIN stat_count other_stat ON other_stat.match_id = other.match_id
LEFT JOIN result_count sr_result ON sr_result.match_id = sr_match.match_id
LEFT JOIN result_count other_result ON other_result.match_id = other.match_id
WHERE other_sr.match_id IS NULL
ORDER BY sr_match.match_id, other.match_id
"""


def run(conn: psycopg.Connection, *, example_limit: int) -> dict:
    rows = conn.execute(DUPLICATE_SQL).fetchall()
    historical_pairs = {(int(r[0]), int(r[1])) for r in rows}
    sportradar_matches = {int(r[0]) for r in rows}
    historical_matches = {int(r[1]) for r in rows}
    stats_on_duplicates = sum(int(r[8]) for r in rows)
    stats_on_originals = sum(int(r[9]) for r in rows)
    examples = [
        {
            "sportradar_match_id": int(r[0]),
            "historical_match_id": int(r[1]),
            "start_delta_seconds": int(r[7]),
            "sportradar_status": r[5],
            "historical_status": r[6],
            "sportradar_match_stats": int(r[8]),
            "historical_match_stats": int(r[9]),
            "sportradar_match_results": int(r[10]),
            "historical_match_results": int(r[11]),
        }
        for r in rows[:example_limit]
    ]
    return {
        "duplicate_link_pairs": len(historical_pairs),
        "sportradar_duplicate_matches": len(sportradar_matches),
        "historical_originals_implicated": len(historical_matches),
        "sportradar_stat_rows_on_duplicates": stats_on_duplicates,
        "sportradar_stat_rows_on_originals": stats_on_originals,
        "examples": examples,
        "writes": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dsn",
        default=os.environ.get("TENNIS_DATABASE_URL")
        or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument("--examples", type=int, default=10)
    args = parser.parse_args(argv)

    if not args.dsn:
        print(
            "SPORTRADAR_DUPLICATE_PROBE_BLOCKED reason=no_dsn",
            flush=True,
        )
        return 2

    with psycopg.connect(args.dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("transaction_read_only is not on")
        report = run(conn, example_limit=max(args.examples, 0))
        conn.rollback()

    print(
        "SPORTRADAR_DUPLICATE_POPULATION "
        + json.dumps(report, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
