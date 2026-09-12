"""Scheduled multi-day Live MVP runner for Railway cron.

Runs a bounded UTC slate horizon with no retries or polling. API-Tennis fixtures
are fetched once per slate date. PINNAPI is fetched at most once for the entire
batch. Type A remains Manager/model dependent; Type B price dispersion scans the
whole canonical in-scope scheduled fixture slate independently of model targets.
API-Tennis pre-match odds are fetched at most once per batch. Prospective CLV
finalization reuses already-persisted odds snapshots and makes no provider calls.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.clv import activation_from_env, finalize_closing_evidence
from sports_edge_tennis.live.api_tennis_fixture_compat import (
    ingest_api_tennis_source_a_fixtures_with_upcoming_status,
)
from sports_edge_tennis.live.bootstrap import ensure_pinnapi_bookmaker_mapping
from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.offered import ingest_api_tennis_offered_odds
from sports_edge_tennis.live.orchestrator import (
    LiveMvpError,
    LivePilotApiTennisFailed,
    run_live_mvp,
)
from sports_edge_tennis.live.price_slate import load_price_scanner_candidate_match_ids
from sports_edge_tennis.live.sharp_slate import normalize_pinnapi_board_for_matches
from sports_edge_tennis.live.transport import (
    TransportError,
    fetch_api_tennis_odds,
    fetch_pinnapi_board,
)
from sports_edge_tennis.market import (
    SharpReferenceSpec,
    ensure_market_policy_version_v1,
    evaluate_and_persist_market_slate,
)
from sports_edge_tennis.market.type_b import scan_type_b_value_signals
from sports_edge_tennis.market.type_b_persistence import (
    consecutive_zero_signal_days,
    persist_type_b_scan,
)
from sports_edge_tennis.odds.providers.pinnapi import new_pinnapi_board_observation_id


_SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _log(message: str) -> None:
    print(message, flush=True)


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


def _load_market_reason_counts(
    conn: psycopg.Connection,
    prediction_run_id: int,
) -> tuple[tuple[str, str, str, int], ...]:
    """Return compact persisted Type A Market outcome counts for diagnostics."""
    rows = conn.execute(
        """
        SELECT signal_type::text, decision_status::text, reason_code::text, count(*)
        FROM decisions
        WHERE prediction_run_id = %s
          AND market_policy_version_id IS NOT NULL
        GROUP BY signal_type, decision_status, reason_code
        ORDER BY signal_type, decision_status, reason_code
        """,
        (prediction_run_id,),
    ).fetchall()
    return tuple((row[0], row[1], row[2], int(row[3])) for row in rows)


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
        "failed_slates": 0,
        "fixtures": 0,
        "targets": 0,
        "consensus": 0,
        "price_candidates": 0,
        "pinnapi_requests": 0,
        "offered_requests": 0,
        "offered_quotes": 0,
        "bets": 0,
        "type_b_value_signals": 0,
        "close_finalized": 0,
        "clv_calculated": 0,
    }
    prediction_run_ids: list[int] = []

    board_observation_id = new_pinnapi_board_observation_id()
    board_cache: dict[str, Any] = {}

    def fetch_board_once(key: str) -> tuple[datetime, dict[str, Any]]:
        if not board_cache:
            received_at, payload = fetch_pinnapi_board(key)
            board_cache["received_at"] = received_at
            board_cache["payload"] = payload
        return board_cache["received_at"], board_cache["payload"]

    try:
        _log("LIVE_BATCH_DB_CONNECT")
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            _log("LIVE_BATCH_DB_CONNECTED")
            if pinnapi_key:
                ensure_pinnapi_bookmaker_mapping(conn)
                _log("LIVE_BATCH_PINNAPI_MAPPING_READY")

            for offset in range(args.days):
                slate_date = args.start_date + timedelta(days=offset)
                slate_pinnapi_key = (
                    pinnapi_key if totals["pinnapi_requests"] == 0 else None
                )
                _log(
                    "SLATE_START "
                    f"date={slate_date.isoformat()} "
                    f"pinnapi_allowed={bool(slate_pinnapi_key)}"
                )
                try:
                    result = run_live_mvp(
                        conn,
                        slate_date=slate_date,
                        api_tennis_key=api_tennis_key,
                        pinnapi_key=slate_pinnapi_key,
                        fetch_board=fetch_board_once,
                        board_observation_id_factory=lambda: board_observation_id,
                        ingest_fixtures=ingest_api_tennis_source_a_fixtures_with_upcoming_status,
                        artifacts_dir=Path(args.artifacts_dir),
                    )
                except LivePilotApiTennisFailed as exc:
                    totals["failed_slates"] += 1
                    _log(
                        "SLATE_UNAVAILABLE "
                        f"date={slate_date.isoformat()} "
                        f"result_code={exc.result_code} error={exc}"
                    )
                    continue

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

            if totals["slates"] == 0 and totals["failed_slates"]:
                _log("LIVE_BATCH_FAILED_NO_SUCCESSFUL_SLATES")
                return 2

            market_as_of = datetime.now(timezone.utc)
            candidate_match_ids = load_price_scanner_candidate_match_ids(
                conn,
                horizon_start=args.start_date,
                horizon_days=args.days,
                as_of=market_as_of,
            )
            totals["price_candidates"] = len(candidate_match_ids)
            _log(
                "PRICE_SLATE "
                f"candidates={len(candidate_match_ids)} "
                f"model_targets={totals['targets']}"
            )

            if candidate_match_ids and pinnapi_key:
                if not board_cache:
                    try:
                        fetch_board_once(pinnapi_key)
                        totals["pinnapi_requests"] = 1
                    except TransportError as exc:
                        _log(f"SHARP_SLATE_UNAVAILABLE kind={exc.kind}")
                if board_cache:
                    board_received_at = board_cache["received_at"]
                    sharp = normalize_pinnapi_board_for_matches(
                        conn,
                        board_payload=board_cache["payload"],
                        received_at=board_received_at,
                        board_observation_id=board_observation_id,
                        candidate_match_ids=candidate_match_ids,
                        as_of=board_received_at,
                        log=_log,
                    )
                    market_as_of = max(market_as_of, board_received_at)
                    _log(
                        "SHARP_SLATE "
                        f"candidates={sharp.candidate_matches} "
                        f"board_events={sharp.board_events} "
                        f"resolved={sharp.resolved_events} "
                        f"normalized={sharp.normalized_events} "
                        f"rejected={sharp.rejected_events}"
                    )

            if candidate_match_ids:
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
                        candidate_match_ids=candidate_match_ids,
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

            policy = None
            if prediction_run_ids or candidate_match_ids:
                policy = ensure_market_policy_version_v1(conn)
                market_as_of = max(market_as_of, datetime.now(timezone.utc))

            if prediction_run_ids and policy is not None:
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
                    market_reason_counts = _load_market_reason_counts(
                        conn, prediction_run_id
                    )
                    if market_reason_counts:
                        _log(
                            "MARKET_REASONS "
                            f"prediction_run_id={prediction_run_id} "
                            + " ".join(
                                f"{signal}:{status}:{reason}={count}"
                                for signal, status, reason, count in market_reason_counts
                            )
                        )

            if candidate_match_ids and policy is not None:
                type_b_signals = scan_type_b_value_signals(
                    conn,
                    match_ids=candidate_match_ids,
                    policy=policy,
                    decision_as_of=market_as_of,
                    sharp_reference=_SHARP,
                )
                totals["type_b_value_signals"] = len(type_b_signals)
                for signal in type_b_signals:
                    _log(
                        "TYPE_B_VALUE "
                        f"match_id={signal.match_id} "
                        f"side={signal.selection_side} "
                        f"bookmaker={signal.bookmaker_code} "
                        f"price={signal.offered_price} "
                        f"sharp_fair_odds={signal.sharp_fair_odds} "
                        f"edge={signal.edge_pct} "
                        f"threshold={signal.threshold} "
                        f"min_price={signal.min_acceptable_odds}"
                    )
                _log(
                    "TYPE_B_VALUE_SUMMARY "
                    f"candidates={len(candidate_match_ids)} "
                    f"signals={len(type_b_signals)}"
                )

                ledger = persist_type_b_scan(
                    conn,
                    signals=type_b_signals,
                    candidate_match_ids=candidate_match_ids,
                    market_policy_version_id=policy.market_policy_version_id,
                    horizon_start=args.start_date,
                    horizon_end=args.start_date + timedelta(days=args.days - 1),
                    decision_as_of=market_as_of,
                    sharp_reference=_SHARP,
                )
                _log(
                    "TYPE_B_LEDGER "
                    f"scan_run_id={ledger.scan_run_id} "
                    f"inserted={ledger.inserted_signal_count} "
                    f"persisted={ledger.persisted_signal_count}"
                )

                zero_days = consecutive_zero_signal_days(
                    conn,
                    through_day=market_as_of.date(),
                    required_days=7,
                )
                if zero_days >= 7:
                    _log(
                        "VOLUME_ALERT "
                        "signal=TYPE_B_PRICE_DISPERSION "
                        f"consecutive_zero_days={zero_days}"
                    )

            clv_activation = activation_from_env()
            if clv_activation is None:
                _log("CLV_CAPTURE_DISABLED activation_env_unset=1")
            else:
                close_outcome = finalize_closing_evidence(
                    conn,
                    activation_at=clv_activation,
                    captured_at=datetime.now(timezone.utc),
                    sharp_reference=_SHARP,
                )
                totals["close_finalized"] = close_outcome.finalized_count
                totals["clv_calculated"] = close_outcome.clv_created_count
                _log(
                    "CLV_CLOSE "
                    f"activation_at={close_outcome.activation_at.isoformat()} "
                    f"eligible={close_outcome.eligible_count} "
                    f"finalized={close_outcome.finalized_count} "
                    f"complete={close_outcome.complete_count} "
                    f"partial={close_outcome.partial_count} "
                    f"missing={close_outcome.missing_count} "
                    f"clv_created={close_outcome.clv_created_count} "
                    f"clv_unavailable={close_outcome.clv_unavailable_count}"
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
