"""Fail-closed launcher for the frozen Sep 10-24 current-system replay V2."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg

from sports_edge_tennis.replay.cli import main as replay_main

EXPECTED_RUNS = 514
EXPECTED_TARGET_ROWS = 11284
EXPECTED_UNIQUE_MATCHES = 799
EXPECTED_MATCH_SHA256 = "6363c2915b526aa66ccb11d0816e246a35261573fed5c28fafa8f89dd174a1a7"
MATCH_IDS_PATH = Path("ops/replay/frozen_population_20260910_20260924_match_ids.txt")
REPLAY_ID = "CURRENT_SYSTEM_REPLAY_20260910_V2_60PCT"


def _fail(message: str) -> None:
    raise SystemExit(f"REPLAY_V2_GATE_FAIL {message}")


def main() -> int:
    raw = MATCH_IDS_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    match_ids = tuple(
        int(line.strip())
        for line in raw.decode("ascii").splitlines()
        if line.strip()
    )
    if digest != EXPECTED_MATCH_SHA256:
        _fail(f"file_sha expected={EXPECTED_MATCH_SHA256} actual={digest}")
    if len(match_ids) != EXPECTED_UNIQUE_MATCHES or len(set(match_ids)) != EXPECTED_UNIQUE_MATCHES:
        _fail(f"file_population count={len(match_ids)} unique={len(set(match_ids))}")
    if tuple(sorted(match_ids)) != match_ids:
        _fail("file_match_ids_not_sorted")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        _fail("DATABASE_URL_missing")

    with psycopg.connect(database_url, autocommit=True) as conn:
        read_only = conn.execute("SHOW default_transaction_read_only").fetchone()[0]
        if str(read_only).lower() not in {"on", "true"}:
            _fail(f"database_not_read_only value={read_only}")

        rows = conn.execute(
            """
            SELECT pr.prediction_run_id, prt.match_id
            FROM prediction_runs pr
            JOIN prediction_run_targets prt
              ON prt.prediction_run_id = pr.prediction_run_id
            WHERE pr.prediction_contract_version = 'predictive_agent_result_v1'
              AND pr.run_status = 'COMPLETED'
              AND pr.prediction_finalized_at IS NOT NULL
              AND pr.experiment_id IS NULL
              AND pr.as_of >= TIMESTAMPTZ '2026-09-10 00:00:00+00'
              AND pr.as_of <  TIMESTAMPTZ '2026-09-25 00:00:00+00'
            ORDER BY pr.prediction_run_id, prt.match_id
            """
        ).fetchall()
        runs = {int(row[0]) for row in rows}
        db_match_ids = tuple(sorted({int(row[1]) for row in rows}))

        if len(runs) != EXPECTED_RUNS:
            _fail(f"runs expected={EXPECTED_RUNS} actual={len(runs)}")
        if len(rows) != EXPECTED_TARGET_ROWS:
            _fail(f"target_rows expected={EXPECTED_TARGET_ROWS} actual={len(rows)}")
        if len(db_match_ids) != EXPECTED_UNIQUE_MATCHES:
            _fail(
                f"unique_matches expected={EXPECTED_UNIQUE_MATCHES} "
                f"actual={len(db_match_ids)}"
            )
        if db_match_ids != match_ids:
            _fail("database_match_ids_do_not_match_frozen_file")

        market_v2 = conn.execute(
            """
            SELECT count(*)
            FROM market_policy_versions
            WHERE policy_key = 'tennis_market_value'
              AND policy_version = 'v2'
            """
        ).fetchone()[0]
        if int(market_v2) != 1:
            _fail(f"market_v2_rows expected=1 actual={market_v2}")

    print(
        "REPLAY_V2_GATE_OK "
        f"runs={EXPECTED_RUNS} rows={EXPECTED_TARGET_ROWS} "
        f"matches={EXPECTED_UNIQUE_MATCHES} sha256={EXPECTED_MATCH_SHA256}",
        flush=True,
    )

    return replay_main(
        [
            "--start-date", "2026-09-10",
            "--end-date", "2026-09-24",
            "--match-ids-file", str(MATCH_IDS_PATH),
            "--manager-policy-version", "v1",
            "--market-policy-version", "v2",
            "--run-planner", "pit_safe",
            "--replay-id", REPLAY_ID,
            "--artifacts-root", os.environ.get("REPLAY_ARTIFACTS_ROOT", "artifacts/replay-v2"),
            "--label", "official",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
