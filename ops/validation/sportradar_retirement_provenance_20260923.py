from __future__ import annotations

import json
import os

import psycopg


MATCH_IDS = (130673, 130705, 130724, 130736, 130746)


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")

    result_rows = []
    version_rows = []

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("transaction_read_only is not on")

        results = conn.execute(
            """
            SELECT
                mro.match_id,
                mro.match_result_observation_id,
                mro.outcome_status::text,
                mro.winner_side,
                mro.retired_side,
                mro.sets_won_player_1,
                mro.sets_won_player_2,
                mro.games_won_player_1,
                mro.games_won_player_2,
                mro.scoreline_text,
                mro.source_observed_at,
                mro.as_of,
                mro.ingest_run_id,
                ir.source_system,
                ir.source_dataset,
                ir.source_version,
                ir.source_file_name,
                ir.status::text
            FROM match_result_observations mro
            LEFT JOIN ingest_runs ir ON ir.ingest_run_id = mro.ingest_run_id
            WHERE mro.match_id = ANY(%s)
            ORDER BY mro.match_id, mro.as_of, mro.source_observed_at,
                     mro.match_result_observation_id
            """,
            (list(MATCH_IDS),),
        ).fetchall()

        for row in results:
            result_rows.append({
                "match_id": int(row[0]),
                "observation_id": int(row[1]),
                "outcome_status": str(row[2]),
                "winner_side": row[3],
                "retired_side": row[4],
                "sets": [row[5], row[6]],
                "games": [row[7], row[8]],
                "scoreline": row[9],
                "source_observed_at": row[10].isoformat(),
                "as_of": row[11].isoformat(),
                "ingest_run_id": row[12],
                "source_system": row[13],
                "source_dataset": row[14],
                "source_version": row[15],
                "source_file_name": row[16],
                "ingest_status": row[17],
            })

        versions = conn.execute(
            """
            SELECT
                mv.match_id,
                mv.match_version_id,
                mv.match_status::text,
                mv.match_start_at,
                mv.source_observed_at,
                mv.as_of,
                mvs.source_system,
                mvs.source_match_id,
                mvs.source_record_locator,
                mvs.source_file_name
            FROM match_versions mv
            LEFT JOIN match_version_sources mvs
              ON mvs.match_version_id = mv.match_version_id
            WHERE mv.match_id = ANY(%s)
            ORDER BY mv.match_id, mv.as_of, mv.source_observed_at,
                     mv.match_version_id, mvs.match_version_source_id
            """,
            (list(MATCH_IDS),),
        ).fetchall()

        for row in versions:
            version_rows.append({
                "match_id": int(row[0]),
                "match_version_id": int(row[1]),
                "match_status": str(row[2]),
                "match_start_at": row[3].isoformat(),
                "source_observed_at": row[4].isoformat(),
                "as_of": row[5].isoformat(),
                "version_source_system": row[6],
                "source_match_id": row[7],
                "source_record_locator": row[8],
                "source_file_name": row[9],
            })

        conn.rollback()

    print(
        "SPORTRADAR_RETIREMENT_PROVENANCE "
        + json.dumps({
            "status": "OK",
            "transaction_read_only": "on",
            "writes": 0,
            "match_ids": list(MATCH_IDS),
            "result_observations": result_rows,
            "match_versions": version_rows,
        }, sort_keys=True),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
