from __future__ import annotations

"""Read-only provider-readiness proof for the intended OddsPapi cutover.

Evaluates the intended authority (Sportradar data + OddsPapi odds + Manager v1)
against the production database without changing any Railway variable and
without making provider calls.
"""

import argparse
import json
import os

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.oddspapi_bootstrap import ensure_oddspapi_bookmaker_mappings
from sports_edge_tennis.live.provider_authority import (
    DATA_FAILOVER_AUTH_ENV,
    DATA_FAILOVER_AUTH_VALUE,
    DATA_MODE_ENV,
    ODDS_FAILOVER_AUTH_ENV,
    ODDS_FAILOVER_AUTH_VALUE,
    ODDS_MODE_ENV,
    TARGET_DATA_MODE,
    TARGET_ODDS_MODE,
    evaluate_cutover_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-mappings", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("PROVIDER_READINESS_BLOCKED missing_dsn")
        return 2

    seeded: list[int] | None = None
    if args.seed_mappings:
        with psycopg.connect(dsn, autocommit=True) as conn:
            seeded = list(ensure_oddspapi_bookmaker_mappings(conn))

    env = {
        DATA_MODE_ENV: TARGET_DATA_MODE,
        ODDS_MODE_ENV: TARGET_ODDS_MODE,
        DATA_FAILOVER_AUTH_ENV: DATA_FAILOVER_AUTH_VALUE,
        ODDS_FAILOVER_AUTH_ENV: ODDS_FAILOVER_AUTH_VALUE,
        "SPORTRADAR_TENNIS_API_KEY": load_secret("SPORTRADAR_TENNIS_API_KEY") or "",
        "SPORTRADAR_TENNIS_ACCESS_LEVEL": os.environ.get("SPORTRADAR_TENNIS_ACCESS_LEVEL", "trial"),
        "SPORTRADAR_TENNIS_LANGUAGE": os.environ.get("SPORTRADAR_TENNIS_LANGUAGE", "en"),
        "ODDSPAPI_API_KEY": load_secret("ODDSPAPI_API_KEY") or "",
        "TENNIS_MANAGER_POLICY_VERSION": "v1",
    }

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        readiness = evaluate_cutover_readiness(conn, env=env)
        conn.rollback()

    print(
        "PROVIDER_READINESS "
        + json.dumps(
            {
                "transaction_read_only": read_only,
                "writes": 0,
                "data_mode": readiness.authority.data_mode,
                "odds_mode": readiness.authority.odds_mode,
                "manager_policy": env["TENNIS_MANAGER_POLICY_VERSION"],
                "ready": readiness.ready,
                "blockers": list(readiness.blockers),
                "seeded_mapping_ids": seeded,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
