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
from typing import Any, Callable

import psycopg

from sports_edge_tennis.clv import activation_from_env, finalize_closing_evidence
from sports_edge_tennis.live.api_tennis_fixture_compat import (
    ingest_api_tennis_source_a_fixtures_with_upcoming_status,
)
from sports_edge_tennis.live.bootstrap import ensure_pinnapi_bookmaker_mapping
from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.oddspapi import (
    ODDSPAPI_PLAN_BOOKMAKERS,
    ODDSPAPI_SHARP_BOOKMAKER,
    fetch_oddspapi_tennis_fixtures,
    fetch_oddspapi_tennis_odds_for_books,
    merge_oddspapi_odds_books,
    parse_oddspapi_tennis_fixture,
)
from sports_edge_tennis.live.oddspapi_budget import (
    OddspapiBudgetError,
    OddspapiBudgetPolicy,
    enforce_oddspapi_budget,
    plan_oddspapi_qualification,
)
from sports_edge_tennis.live.oddspapi_ledger import (
    OddspapiLedgerBinding,
    OddspapiLedgerConfig,
)
from sports_edge_tennis.live.oddspapi_odds import ingest_oddspapi_tennis_odds
from sports_edge_tennis.live.offered import ingest_api_tennis_offered_odds
from sports_edge_tennis.live.orchestrator import (
    LiveMvpError,
    LivePilotApiTennisFailed,
    LivePilotSportradarFailed,
    run_live_mvp,
)
from sports_edge_tennis.live.price_slate import load_price_scanner_candidate_match_ids
from sports_edge_tennis.live.provider_authority import (
    LEGACY_DATA_MODE,
    LEGACY_ODDS_MODE,
    TARGET_DATA_MODE,
    TARGET_ODDS_MODE,
    assert_cutover_ready,
    load_provider_authority,
)
from sports_edge_tennis.live.sharp_slate import normalize_pinnapi_board_for_matches
from sports_edge_tennis.live.target_fixtures import (
    fetch_sportradar_fixture_rows,
    ingest_sportradar_fixture_rows,
)
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


def _load_missing_market_diagnostics(
    conn: psycopg.Connection,
    prediction_run_id: int,
    *,
    decision_as_of: datetime,
    odds_ttl_minutes: int,
    odds_result: dict[str, Any] | None,
) -> tuple[tuple[int, int, str | None, bool, int, int, datetime | None], ...]:
    """Explain only persisted MISSING_MARKET consensuses without changing decisions."""
    cutoff = decision_as_of - timedelta(minutes=odds_ttl_minutes)
    rows = conn.execute(
        """
        SELECT
            d.match_id,
            mcr.selected_side,
            src.source_match_id,
            count(os.odds_snapshot_id) FILTER (
                WHERE os.source_system = 'OFFERED'
            ) AS offered_rows,
            count(os.odds_snapshot_id) FILTER (
                WHERE os.source_system = 'OFFERED'
                  AND os.selection_type = CASE
                      WHEN mcr.selected_side = 1 THEN 'PLAYER_1'::market_selection_type
                      ELSE 'PLAYER_2'::market_selection_type
                  END
                  AND os.market_status = 'OPEN'
                  AND os.decimal_price > 1
                  AND os.source_observed_at >= %s
                  AND os.source_observed_at <= %s
            ) AS fresh_selected_rows,
            max(os.source_observed_at) FILTER (
                WHERE os.source_system = 'OFFERED'
            ) AS latest_offered_at
        FROM decisions d
        JOIN manager_consensus_results mcr
          ON mcr.manager_consensus_result_id = d.manager_consensus_result_id
        LEFT JOIN LATERAL (
            SELECT source_match_id
            FROM match_source_ids
            WHERE match_id = d.match_id
              AND source_system = 'API_TENNIS'
            ORDER BY match_source_id DESC
            LIMIT 1
        ) src ON TRUE
        LEFT JOIN odds_snapshots os
          ON os.match_id = d.match_id
        WHERE d.prediction_run_id = %s
          AND d.market_policy_version_id IS NOT NULL
          AND d.reason_code = 'MISSING_MARKET'
          AND mcr.manager_status = 'CONSENSUS'
        GROUP BY d.match_id, mcr.selected_side, src.source_match_id
        ORDER BY d.match_id
        """,
        (cutoff, decision_as_of, prediction_run_id),
    ).fetchall()

    payload = odds_result or {}
    return tuple(
        (
            int(row[0]),
            int(row[1]),
            str(row[2]) if row[2] is not None else None,
            str(row[2]) in payload if row[2] is not None else False,
            int(row[3]),
            int(row[4]),
            row[5],
        )
        for row in rows
    )


def _format_fixture_rejection_reasons(reasons: tuple[tuple[str, int], ...]) -> str:
    if not reasons:
        return "none"
    return ",".join(f"{reason}={count}" for reason, count in reasons)


def _oddspapi_budget_policy_from_env() -> OddspapiBudgetPolicy:
    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw in (None, ""):
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    return OddspapiBudgetPolicy(
        monthly_limit=_int("ODDSPAPI_MONTHLY_LIMIT", 5000),
        reserve=_int("ODDSPAPI_MONTHLY_RESERVE", 500),
        per_cycle_limit=_int("ODDSPAPI_PER_CYCLE_LIMIT", 5),
    )


def _oddspapi_ledger_config_from_env() -> OddspapiLedgerConfig:
    policy = _oddspapi_budget_policy_from_env()
    return OddspapiLedgerConfig(
        monthly_limit=policy.monthly_limit,
        reserve=policy.reserve,
    )


def _load_manager_consensus_match_ids(
    conn: psycopg.Connection,
    prediction_run_ids: list[int],
) -> list[int]:
    if not prediction_run_ids:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT match_id
        FROM manager_consensus_results
        WHERE prediction_run_id = ANY(%s)
          AND manager_status = 'CONSENSUS'
        """,
        (list(prediction_run_ids),),
    ).fetchall()
    return [int(row[0]) for row in rows]


def _manager_consensus_betradar_ids(
    conn: psycopg.Connection,
    match_ids: list[int],
) -> set[str]:
    if not match_ids:
        return set()
    rows = conn.execute(
        """
        SELECT source_match_id
        FROM match_source_ids
        WHERE source_system = 'SPORTRADAR'
          AND match_id = ANY(%s)
        """,
        (list(match_ids),),
    ).fetchall()
    keys: set[str] = set()
    for row in rows:
        text = str(row[0])
        keys.add(
            text[len("sr:sport_event:") :] if text.startswith("sr:sport_event:") else text
        )
    return keys


def main(
    argv: list[str] | None = None,
    *,
    summary_sink: Callable[[dict[str, int]], None] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Sports Edge Tennis scheduled live horizon")
    parser.add_argument("--start-date", type=date.fromisoformat, default=_today_utc())
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--dsn", default=_db_dsn())
    parser.add_argument("--artifacts-dir", default="artifacts/live_mvp")
    args = parser.parse_args(argv)

    try:
        provider_authority = load_provider_authority(os.environ)
    except ValueError as exc:
        _log(f"BLOCKED_PROVIDER_AUTHORITY: {exc}")
        return 2

    _log(
        "LIVE_BATCH_START "
        f"start_date={args.start_date.isoformat()} days={args.days} "
        f"data_provider={provider_authority.data_mode} "
        f"odds_provider={provider_authority.odds_mode} "
        f"pinnapi_configured={bool(load_secret('PINNAPI_KEY'))}"
    )

    if args.days < 1 or args.days > 7:
        _log("BLOCKED_INVALID_HORIZON: --days must be between 1 and 7")
        return 2
    if not args.dsn:
        _log("BLOCKED_NO_LIVE_MVP_DATABASE: no DSN configured")
        return 2

    if provider_authority.data_mode == LEGACY_DATA_MODE:
        fixture_provider_key = load_secret("API_TENNIS_API_KEY")
        if not fixture_provider_key:
            _log("BLOCKED_MISSING_API_TENNIS_KEY")
            return 2
    elif provider_authority.data_mode == TARGET_DATA_MODE:
        fixture_provider_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
        if not fixture_provider_key:
            _log("BLOCKED_MISSING_SPORTRADAR_TENNIS_API_KEY")
            return 2
    else:  # defensive, load_provider_authority already validates this.
        _log("BLOCKED_UNSUPPORTED_DATA_PROVIDER")
        return 2

    pinnapi_key = (
        load_secret("PINNAPI_KEY")
        if provider_authority.odds_mode == LEGACY_ODDS_MODE
        else None
    )
    oddspapi_key = (
        load_secret("ODDSPAPI_API_KEY")
        if provider_authority.odds_mode == TARGET_ODDS_MODE
        else None
    )
    if provider_authority.odds_mode == TARGET_ODDS_MODE and not oddspapi_key:
        _log("BLOCKED_MISSING_ODDSPAPI_API_KEY")
        return 2
    # The offered-odds leg of the legacy odds authority is served by API-Tennis,
    # independently of which provider supplies fixtures. Load its key
    # separately so a data-only failover never sends the Sportradar key to the
    # legacy odds endpoint.
    legacy_odds_key = (
        load_secret("API_TENNIS_API_KEY")
        if provider_authority.odds_mode == LEGACY_ODDS_MODE
        else None
    )
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
        "oddspapi_events": 0,
        "oddspapi_exact": 0,
        "oddspapi_ambiguous": 0,
        "oddspapi_conflict": 0,
        "bets": 0,
        "type_b_value_signals": 0,
        "close_finalized": 0,
        "clv_calculated": 0,
    }
    prediction_run_ids: list[int] = []
    offered_odds_result: dict[str, Any] | None = None

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
            # Enforce the same cutover gate even when live.batch is invoked
            # directly instead of through production_batch.
            assert_cutover_ready(conn, env=os.environ)
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
                    live_kwargs: dict[str, Any] = {
                        "conn": conn,
                        "slate_date": slate_date,
                        # run_live_mvp keeps this legacy parameter name, but the
                        # injected fetch_fixtures contract is provider-neutral.
                        "api_tennis_key": fixture_provider_key,
                        "pinnapi_key": slate_pinnapi_key,
                        "fetch_board": fetch_board_once,
                        "board_observation_id_factory": lambda: board_observation_id,
                        "artifacts_dir": Path(args.artifacts_dir),
                    }
                    if provider_authority.data_mode == TARGET_DATA_MODE:
                        live_kwargs["fetch_fixtures"] = fetch_sportradar_fixture_rows
                        live_kwargs["ingest_fixtures"] = ingest_sportradar_fixture_rows
                        live_kwargs["fixture_provider_code"] = "SPORTRADAR_V3"
                    else:
                        live_kwargs["ingest_fixtures"] = (
                            ingest_api_tennis_source_a_fixtures_with_upcoming_status
                        )
                    result = run_live_mvp(**live_kwargs)
                except (LivePilotApiTennisFailed, LivePilotSportradarFailed) as exc:
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
                _log(
                    "FIXTURE_RECONCILIATION "
                    f"date={result.slate_date} "
                    f"seen={result.fixture_rows_seen} "
                    f"ingested={result.fixture_rows_ingested} "
                    f"rejected={result.fixture_rows_rejected} "
                    "reasons="
                    f"{_format_fixture_rejection_reasons(result.fixture_rejection_reasons)}"
                )
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
                if summary_sink is not None:
                    summary_sink(dict(totals))
                _log("LIVE_BATCH_FAILED_NO_SUCCESSFUL_SLATES")
                return 2

            market_as_of = datetime.now(timezone.utc)
            candidate_match_ids = load_price_scanner_candidate_match_ids(
                conn,
                horizon_start=args.start_date,
                horizon_days=args.days,
                as_of=market_as_of,
                source_systems=(
                    ("SPORTRADAR",)
                    if provider_authority.data_mode == TARGET_DATA_MODE
                    else ("API_TENNIS",)
                ),
            )
            totals["price_candidates"] = len(candidate_match_ids)
            _log(
                "PRICE_SLATE "
                f"candidates={len(candidate_match_ids)} "
                f"model_targets={totals['targets']}"
            )

            if (
                candidate_match_ids
                and pinnapi_key
                and provider_authority.odds_mode == LEGACY_ODDS_MODE
            ):
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
                if provider_authority.odds_mode == LEGACY_ODDS_MODE:
                    if not legacy_odds_key:
                        _log("OFFERED_ODDS_UNAVAILABLE kind=MISSING_LEGACY_ODDS_KEY")
                    else:
                        try:
                            odds_received_at, odds_result = fetch_api_tennis_odds(
                                legacy_odds_key,
                                args.start_date,
                                args.start_date + timedelta(days=args.days - 1),
                            )
                            totals["offered_requests"] = 1
                            offered_odds_result = dict(odds_result)
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
                                f"provider=API_TENNIS "
                                f"candidates={offered.candidate_matches} "
                                f"events={offered.events_with_odds} "
                                f"quotes={offered.normalized_quotes} "
                                f"missing={offered.missing_events} "
                                f"bookmakers={offered.bookmaker_count}"
                            )
                        except TransportError as exc:
                            _log(f"OFFERED_ODDS_UNAVAILABLE kind={exc.kind}")
                elif provider_authority.odds_mode == TARGET_ODDS_MODE:
                    budget_policy = _oddspapi_budget_policy_from_env()
                    consensus_match_ids = _load_manager_consensus_match_ids(
                        conn, prediction_run_ids
                    )
                    if not consensus_match_ids:
                        # Rule A: no Manager consensus means no qualification
                        # odds spend.
                        _log(
                            "ODDSPAPI_QUALIFICATION_SKIPPED "
                            "reason=ZERO_CONSENSUS requests=0"
                        )
                    else:
                        ledger_config = _oddspapi_ledger_config_from_env()
                        correlation_id = f"live:{args.start_date.isoformat()}"
                        discovery_ledger = OddspapiLedgerBinding(
                            conn=conn,
                            config=ledger_config,
                            request_purpose="DISCOVERY",
                            classification="PRODUCTION",
                            correlation_id=correlation_id,
                        )
                        qualification_ledger = OddspapiLedgerBinding(
                            conn=conn,
                            config=ledger_config,
                            request_purpose="QUALIFICATION",
                            classification="PRODUCTION",
                            correlation_id=correlation_id,
                        )
                        try:
                            window_start = datetime.combine(
                                args.start_date,
                                datetime.min.time(),
                                tzinfo=timezone.utc,
                            )
                            window_end = window_start + timedelta(days=args.days)
                            discovery_received_at, discovery_rows = (
                                fetch_oddspapi_tennis_fixtures(
                                    oddspapi_key,
                                    from_at=window_start,
                                    to_at=window_end,
                                    bookmakers=(ODDSPAPI_SHARP_BOOKMAKER,),
                                    ledger=discovery_ledger,
                                )
                            )
                            totals["offered_requests"] += 1
                            consensus_betradar = _manager_consensus_betradar_ids(
                                conn, consensus_match_ids
                            )
                            tournament_ids: list[int] = []
                            for payload in discovery_rows:
                                try:
                                    envelope = parse_oddspapi_tennis_fixture(payload)
                                except ValueError:
                                    continue
                                if envelope.betradar_id and envelope.betradar_id in consensus_betradar:
                                    tournament_ids.append(envelope.tournament_id)

                            plan = plan_oddspapi_qualification(
                                tournament_ids=tournament_ids,
                                books=ODDSPAPI_PLAN_BOOKMAKERS,
                                discovery_book=ODDSPAPI_SHARP_BOOKMAKER,
                                policy=budget_policy,
                                include_discovery=False,
                            )
                            enforce_oddspapi_budget(plan, policy=budget_policy)

                            odds_received_at = discovery_received_at
                            odds_rows: list[dict[str, Any]] = []
                            if plan.tournament_ids:
                                (
                                    odds_received_at,
                                    board_rows,
                                    odds_requests,
                                ) = fetch_oddspapi_tennis_odds_for_books(
                                    oddspapi_key,
                                    tournament_ids=plan.tournament_ids,
                                    bookmakers=ODDSPAPI_PLAN_BOOKMAKERS,
                                    ledger=qualification_ledger,
                                )
                                totals["offered_requests"] += odds_requests
                                odds_rows = merge_oddspapi_odds_books(
                                    discovery_rows,
                                    board_rows,
                                )

                            oddspapi = ingest_oddspapi_tennis_odds(
                                conn,
                                payload_rows=odds_rows,
                                received_at=odds_received_at,
                                candidate_match_ids=candidate_match_ids,
                            )
                            totals["offered_quotes"] = oddspapi.normalized_quotes
                            totals["oddspapi_events"] = oddspapi.provider_events_seen
                            totals["oddspapi_exact"] = (
                                oddspapi.exact_events + oddspapi.existing_events
                            )
                            totals["oddspapi_ambiguous"] = oddspapi.ambiguous_events
                            totals["oddspapi_conflict"] = oddspapi.conflict_events
                            market_as_of = max(market_as_of, odds_received_at)
                            _log(
                                "OFFERED_ODDS "
                                f"provider=ODDSPAPI_TENNIS "
                                f"requests={totals['offered_requests']} "
                                f"planned={plan.total_requests} "
                                f"consensus_matches={len(consensus_match_ids)} "
                                f"tournaments={len(plan.tournament_ids)} "
                                f"events={oddspapi.provider_events_seen} "
                                f"exact={oddspapi.exact_events} "
                                f"existing={oddspapi.existing_events} "
                                f"unmatched={oddspapi.unmatched_events} "
                                f"ambiguous={oddspapi.ambiguous_events} "
                                f"conflict={oddspapi.conflict_events} "
                                f"quotes={oddspapi.normalized_quotes} "
                                f"unknown_bookmaker={oddspapi.unknown_bookmaker_groups} "
                                f"unknown_participant={oddspapi.unknown_participant_groups}"
                            )
                        except OddspapiBudgetError as exc:
                            _log(f"ODDSPAPI_BUDGET_BLOCKED {exc}")
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
                    if (
                        provider_authority.odds_mode == LEGACY_ODDS_MODE
                        and any(
                            reason == "MISSING_MARKET"
                            for _signal, _status, reason, _count in market_reason_counts
                        )
                    ):
                        for (
                            match_id,
                            selected_side,
                            api_event_id,
                            provider_event_present,
                            offered_rows,
                            fresh_selected_rows,
                            latest_offered_at,
                        ) in _load_missing_market_diagnostics(
                            conn,
                            prediction_run_id,
                            decision_as_of=market_as_of,
                            odds_ttl_minutes=policy.odds_max_staleness_minutes,
                            odds_result=offered_odds_result,
                        ):
                            _log(
                                "OFFERED_MARKET_GAP "
                                f"prediction_run_id={prediction_run_id} "
                                f"match_id={match_id} selected_side={selected_side} "
                                f"api_event_id={api_event_id or '-'} "
                                f"provider_event_present={int(provider_event_present)} "
                                f"offered_rows={offered_rows} "
                                f"fresh_selected_rows={fresh_selected_rows} "
                                f"latest_offered_at={latest_offered_at.isoformat() if latest_offered_at else '-'}"
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

    if summary_sink is not None:
        summary_sink(dict(totals))
    _log(
        "LIVE_BATCH_SUCCESS "
        + " ".join(f"{key}={value}" for key, value in totals.items())
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
