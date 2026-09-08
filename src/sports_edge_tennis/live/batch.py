"""Scheduled multi-day Live MVP runner for Railway cron.

Runs a bounded UTC slate horizon with no retries or polling. Each slate date
uses the existing single-run orchestrator. API-Tennis is fetched once per slate
date, while PINNAPI is allowed at most one board request across the entire
multi-day batch cycle.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg

from sports_edge_tennis.live.api_tennis_fixture_compat import (
    ingest_api_tennis_source_a_fixtures_with_upcoming_status,
)
from sports_edge_tennis.live.bootstrap import ensure_pinnapi_bookmaker_mapping
from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.orchestrator import LiveMvpError, run_live_mvp


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _log(message: str) -> None:
    print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sports Edge Tennis scheduled live horizon")
    parser.add_argument("--start-date", type=date.fromisoformat, default=_today_utc())
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--dsn", default=_db_dsn())
    parser.add_argument("--artifacts-dir", default="artifacts/live_mvp")
    args = parser.parse_args(argv)

    _log(
        "LIVE_BATCH_START "
        f"start_date={args.start_date.isoformat()} days={args.days} "
        f"pinnapi_configured={bool(load_secret('PINNAPI_KEY'))}"
    )

    if args.days < 1 or args.days > 7:
        _log("BLOCKED_INVALID_HORIZON: --days must be between 1 and 7")
        return 2
    if not args.dsn:
        _log("BLOCKED_NO_LIVE_MVP_DATABASE: no DSN configured")
        return 2

    api_tennis_key = load_secret("API_TENNIS_API_KEY")
    if not api_tennis_key:
        _log("BLOCKED_MISSING_API_TENNIS_KEY")
        return 2

    pinnapi_key = load_secret("PINNAPI_KEY")
    totals = {
        "slates": 0,
        "fixtures": 0,
        "targets": 0,
        "consensus": 0,
        "pinnapi_requests": 0,
    }

    try:
        _log("LIVE_BATCH_DB_CONNECT")
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            _log("LIVE_BATCH_DB_CONNECTED")
            if pinnapi_key:
                ensure_pinnapi_bookmaker_mapping(conn)
                _log("LIVE_BATCH_PINNAPI_MAPPING_READY")

            for offset in range(args.days):
                slate_date = args.start_date + timedelta(days=offset)
                # The PINNAPI endpoint is a full prematch board. Until one board
                # payload can be reused across all slate-date reconciliation in
                # this batch, permit it only while this cycle has consumed zero
                # board requests. This preserves the production contract of at
                # most one sharp board request per retrieval cycle.
                slate_pinnapi_key = (
                    pinnapi_key if totals["pinnapi_requests"] == 0 else None
                )
                _log(
                    "SLATE_START "
                    f"date={slate_date.isoformat()} "
                    f"pinnapi_allowed={bool(slate_pinnapi_key)}"
                )
                result = run_live_mvp(
                    conn,
                    slate_date=slate_date,
                    api_tennis_key=api_tennis_key,
                    pinnapi_key=slate_pinnapi_key,
                    ingest_fixtures=ingest_api_tennis_source_a_fixtures_with_upcoming_status,
                    artifacts_dir=Path(args.artifacts_dir),
                )
                totals["slates"] += 1
                totals["fixtures"] += result.fixture_count
                totals["targets"] += result.target_count
                totals["consensus"] += result.manager_consensus_count
                totals["pinnapi_requests"] += result.pinnapi_requests
                _log(
                    "SLATE "
                    f"date={result.slate_date} status={result.status} "
                    f"fixtures={result.fixture_count} targets={result.target_count} "
                    f"consensus={result.manager_consensus_count} "
                    f"pinnapi_requests={result.pinnapi_requests}"
                )
    except LiveMvpError as exc:
        _log(f"{exc.result_code}: {exc}")
        return 2
    except MissingCredentialError as exc:
        _log(f"BLOCKED_MISSING_CREDENTIAL: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - fail closed and surface one concise error
        _log(f"IMPLEMENTATION_FAILED: {type(exc).__name__}: {exc}")
        return 2

    _log(
        "LIVE_BATCH_SUCCESS "
        + " ".join(f"{key}={value}" for key, value in totals.items())
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
