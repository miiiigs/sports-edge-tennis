"""Scheduled multi-day Live MVP runner for Railway cron.

Runs a bounded UTC slate horizon with no retries or polling. Each slate date
uses the existing single-run orchestrator. API-Tennis fixtures are fetched once
per slate date, PINNAPI is allowed at most one board request across the entire
multi-day batch cycle, and API-Tennis pre-match odds are fetched at most once
for the whole bounded horizon when Manager consensus exists.

A PostgreSQL session advisory lock prevents two Railway cron containers from
processing the same live database concurrently. If a previous batch still owns
the lock, the later invocation exits successfully without provider calls or DB
mutations beyond opening the connection.
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
from sports_edge_tennis.live.offered import ingest_api_tennis_offered_odds
from sports_edge_tennis.live.orchestrator import LiveMvpError, run_live_mvp
from sports_edge_tennis.live.transport import TransportError, fetch_api_tennis_odds
from sports_edge_tennis.market import (
    SharpReferenceSpec,
    ensure_market_policy_version_v1,
    evaluate_and_persist_market_slate,
)


_SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")
_BATCH_ADVISORY_LOCK_KEY = 726_645_031_002_026


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _log(message: str) -> None:
    print(message, flush=True)


def _try_acquire_batch_lock(conn: psycopg.Connection) -> bool:
    row = conn.execute(
        "SELECT pg_try_advisory_lock(%s)",
        (_BATCH_ADVISORY_LOCK_KEY,),
    ).fetchone()
    return bool(row and row[0])


def _load_consensus_match_ids(
    conn: psycopg.Connection,
    prediction_run_ids: tuple[int, ...],
) -> tuple[int, ...]:
    if not prediction_run_ids:
        return ()
    rows = conn.execute(
        """
        SELECT DISTINCT match_id
        FROM manager_consensus_results
        WHERE prediction_run_id = ANY(%s)
          AND manager_status = 'CONSENSUS'
        ORDER BY match_id
        """,
        (list(prediction_run_ids),),
    ).fetchall()
    return tuple(row[0] for row in rows)


def _load_manager_reason_counts(
    conn: psycopg.Connection,
    prediction_run_id: int,
) -> tuple[tuple[str, str, int], ...]:
    """Return compact persisted Manager outcome counts for production diagnostics."""
    rows = conn.execute(
        """
        SELECT manager_status::text, reason_code::text, count(*)
        FROM manager_consensus_results
        WHERE prediction_run_id = %s
        GROUP BY manager_status, reason_code
        ORDER BY manager_status, reason_code
        """,
        (prediction_run_id,),
    ).fetchall()
    return tuple((row[0], row[1], int(row[2])) for row in rows)


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
        "offered_requests": 0,
        "offered_quotes": 0,
        "bets": 0,
    }
    prediction_run_ids: list[int] = []

    try:
        _log("LIVE_BATCH_DB_CONNECT")
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            _log("LIVE_BATCH_DB_CONNECTED")
            if not _try_acquire_batch_lock(conn):
                _log("LIVE_BATCH_SKIPPED_OVERLAP")
                return 0
            _log("LIVE_BATCH_LOCK_ACQUIRED")

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
                if result.prediction_run_id is not None:
                    prediction_run_ids.append(result.prediction_run_id)
                    reason_counts = _load_manager_reason_counts(
                        conn, result.prediction_run_id
                    )
                    if reason_counts:
                        _log(
                            "MANAGER_REASONS "
                            f"prediction_run_id={result.prediction_run_id} "
                            + " ".join(
                                f"{status}:{reason}={count}"
                                for status, reason, count in reason_counts
                            )
                        )
                _log(
                    "SLATE "
                    f"date={result.slate_date} status={result.status} "
                    f"fixtures={result.fixture_count} targets={result.target_count} "
                    f"consensus={result.manager_consensus_count} "
                    f"pinnapi_requests={result.pinnapi_requests}"
                )

            if totals["consensus"] > 0 and prediction_run_ids:
                consensus_match_ids = _load_consensus_match_ids(
                    conn, tuple(prediction_run_ids)
                )
                market_as_of = datetime.now(timezone.utc)
                if consensus_match_ids:
                    try:
                        odds_received_at, odds_result = fetch_api_tennis_odds(
                            api_tennis_key,
                            args.start_date,
                            args.start_date + timedelta(days=args.days - 1),
                        )
                        totals["offered_requests"] = 1
                        offered = ingest_api_tennis_offered_odds(
                            conn,
                            odds_result=odds_result,
                            received_at=odds_received_at,
                            candidate_match_ids=consensus_match_ids,
                        )
                        totals["offered_quotes"] = offered.normalized_quotes
                        market_as_of = max(market_as_of, odds_received_at)
                        _log(
                            "OFFERED_ODDS "
                            f"candidates={offered.candidate_matches} "
                            f"events={offered.events_with_odds} "
                            f"quotes={offered.normalized_quotes} "
                            f"missing={offered.missing_events} "
                            f"bookmakers={offered.bookmaker_count}"
                        )
                    except TransportError as exc:
                        _log(f"OFFERED_ODDS_UNAVAILABLE kind={exc.kind}")

                policy = ensure_market_policy_version_v1(conn)
                market_execution_now = datetime.now(timezone.utc)
                market_as_of = max(market_as_of, market_execution_now)
                for prediction_run_id in prediction_run_ids:
                    outcome = evaluate_and_persist_market_slate(
                        conn,
                        prediction_run_id=prediction_run_id,
                        market_policy_version_id=policy.market_policy_version_id,
                        decision_as_of=market_as_of,
                        market_execution_now=market_as_of,
                        sharp_reference=_SHARP,
                    )
                    totals["bets"] += outcome.qualified_bet_count
                    _log(
                        "MARKET "
                        f"prediction_run_id={prediction_run_id} "
                        f"status={outcome.status} "
                        f"decisions={outcome.new_decision_count} "
                        f"bets={outcome.qualified_bet_count}"
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
