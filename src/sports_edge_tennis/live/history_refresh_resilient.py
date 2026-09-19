"""Resilient, resumable API-Tennis historical result refresh orchestration.

This wrapper keeps the canonical chunk ingest semantics from ``history_refresh`` and
adds bounded transient retries, exponential backoff, adaptive chunk splitting,
durable PostgreSQL progress, and a single-worker advisory lock. The checkpoint table
is operational state only; canonical historical truth remains in the append-only
fixture/result tables written by ``refresh_history_chunk``.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Callable

import psycopg

from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.history_refresh import (
    MAX_CHUNK_DAYS,
    MAX_WINDOW_DAYS,
    HistoryRefreshChunkOutcome,
    refresh_history_chunk,
)
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.market.contracts import (
    MarketPolicyV1,
    OddsSnapshotV1,
    PersistedManagerConsensusRow,
    SharpReferenceSpec,
)
from sports_edge_tennis.market.evaluator import evaluate_market_match

CHECKPOINT_SCHEMA_VERSION = "history_refresh_checkpoint_v1"
DEFAULT_CHECKPOINT_KEY = "API_TENNIS_RECENT_HISTORY_V1"
DEFAULT_CHUNK_DAYS = 7
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_MIN_CHUNK_DAYS = 1
A2_STATS_DRY_RUN_ENABLED_ENV = "A2_STATS_BACKFILL_DRY_RUN_ENABLED"
A2_STATS_DRY_RUN_DAYS_ENV = "A2_STATS_BACKFILL_DRY_RUN_DAYS"
A2_STATS_DRY_RUN_CHUNK_DAYS_ENV = "A2_STATS_BACKFILL_DRY_RUN_CHUNK_DAYS"
A2_STATS_DRY_RUN_END_DATE_ENV = "A2_STATS_BACKFILL_DRY_RUN_END_DATE"
A2_STATS_DRY_RUN_DEFAULT_DAYS = 7
A2_STATS_DRY_RUN_MAX_DAYS = 31
A2_STATS_DRY_RUN_DEFAULT_CHUNK_DAYS = 7
A2_STATS_DRY_RUN_MAX_CHUNK_DAYS = 7
A2_REGIME_AUDIT_ENABLED_ENV = "A2_HISTORY_REGIME_AUDIT_ENABLED"
A2_REGIME_AUDIT_CODE_ENV = "A2_HISTORY_REGIME_AUDIT_CODE"
MARKET_STALE_SELFTEST_ENABLED_ENV = "MARKET_STALE_SELFTEST_ENABLED"
PINNACLE_SCHEDULE_AUDIT_ENABLED_ENV = "PINNACLE_SCHEDULE_AUDIT_ENABLED"
PINNACLE_SCHEDULE_AUDIT_MATCH_IDS_ENV = "PINNACLE_SCHEDULE_AUDIT_MATCH_IDS"
PINNACLE_SCHEDULE_AUDIT_MAX_MATCHES = 10
LIVE_TENNIS_SURFACE_PROBE_ENABLED_ENV = "LIVE_TENNIS_SURFACE_PROBE_ENABLED"
LIVE_TENNIS_SURFACE_PROBE_MAX_PAGES_ENV = "LIVE_TENNIS_SURFACE_PROBE_MAX_PAGES"
LIVE_TENNIS_SURFACE_PROBE_DEFAULT_PAGES = 2
LIVE_TENNIS_SURFACE_PROBE_MAX_PAGES = 5
OFFERED_MARKET_AUDIT_ONLY_ENV = "OFFERED_MARKET_AUDIT_ONLY"
OFFERED_MARKET_AUDIT_MAX_DECISIONS_ENV = "OFFERED_MARKET_AUDIT_MAX_DECISIONS"
OFFERED_MARKET_AUDIT_DEFAULT_MAX_DECISIONS = 5
OFFERED_MARKET_AUDIT_MAX_DECISIONS = 20
_ADVISORY_LOCK_KEY = 702_260_913
_RETRYABLE_KINDS = {
    "TIMEOUT", "NETWORK", "HTTP_429", "HTTP_500", "HTTP_502", "HTTP_503", "HTTP_504",
}

_CHECKPOINT_DDL = """
CREATE TABLE IF NOT EXISTS history_refresh_checkpoints (
    checkpoint_key TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    window_start DATE NOT NULL,
    window_end DATE NOT NULL,
    requested_chunk_days INTEGER NOT NULL CHECK (requested_chunk_days BETWEEN 1 AND 31),
    next_start_date DATE NOT NULL,
    completed_chunks BIGINT NOT NULL DEFAULT 0 CHECK (completed_chunks >= 0),
    last_completed_end DATE,
    last_error_kind TEXT,
    last_error_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@dataclass(frozen=True)
class HistoryRefreshCheckpoint:
    checkpoint_key: str
    window_start: date
    window_end: date
    requested_chunk_days: int
    next_start_date: date
    completed_chunks: int
    last_completed_end: date | None
    last_error_kind: str | None


@dataclass
class HistoryRefreshTotals:
    fixtures_seen: int = 0
    fixtures_ingested: int = 0
    fixture_rejections: int = 0
    terminal_results_seen: int = 0
    results_inserted: int = 0
    results_unchanged: int = 0
    result_rejections: int = 0
    completed_chunks: int = 0

    def add(self, outcome: HistoryRefreshChunkOutcome) -> None:
        self.fixtures_seen += outcome.fixtures_seen
        self.fixtures_ingested += outcome.fixtures_ingested
        self.fixture_rejections += outcome.fixture_rejections
        self.terminal_results_seen += outcome.terminal_results_seen
        self.results_inserted += outcome.results_inserted
        self.results_unchanged += outcome.results_unchanged
        self.result_rejections += outcome.result_rejections
        self.completed_chunks += 1


RefreshChunk = Callable[..., HistoryRefreshChunkOutcome]
Sleep = Callable[[float], None]


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _default_end_date() -> date:
    return datetime.now(UTC).date() - timedelta(days=1)


def _flag_enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _run_optional_a2_stats_dry_run(
    conn: psycopg.Connection,
    *,
    api_key: str,
    end_date: date,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., object] | None = None,
) -> None:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(A2_STATS_DRY_RUN_ENABLED_ENV)):
        return

    try:
        days = int(
            str(values.get(A2_STATS_DRY_RUN_DAYS_ENV) or A2_STATS_DRY_RUN_DEFAULT_DAYS).strip()
        )
        chunk_days = int(
            str(
                values.get(A2_STATS_DRY_RUN_CHUNK_DAYS_ENV)
                or A2_STATS_DRY_RUN_DEFAULT_CHUNK_DAYS
            ).strip()
        )
    except ValueError as exc:
        raise ValueError("A2 stats dry-run bounds must be integers") from exc
    if not 1 <= days <= A2_STATS_DRY_RUN_MAX_DAYS:
        raise ValueError(
            f"A2 stats dry-run days must be between 1 and {A2_STATS_DRY_RUN_MAX_DAYS}"
        )
    if not 1 <= chunk_days <= A2_STATS_DRY_RUN_MAX_CHUNK_DAYS:
        raise ValueError(
            "A2 stats dry-run chunk days must be between 1 and "
            f"{A2_STATS_DRY_RUN_MAX_CHUNK_DAYS}"
        )

    effective_end_date = end_date
    end_override = str(values.get(A2_STATS_DRY_RUN_END_DATE_ENV) or "").strip()
    if end_override:
        try:
            effective_end_date = date.fromisoformat(end_override)
        except ValueError as exc:
            raise ValueError("A2 stats dry-run end date must be ISO YYYY-MM-DD") from exc
        if effective_end_date > end_date:
            raise ValueError("A2 stats dry-run end date may not exceed validation end date")

    start_date = effective_end_date - timedelta(days=days - 1)
    if runner is None:
        from sports_edge_tennis.live.api_tennis_stats_backfill import (
            backfill_stats_range as dry_run_runner,
        )

        runner = dry_run_runner

    print(
        "A2_STATS_BACKFILL_DRY_RUN_DISPATCH "
        f"start={start_date.isoformat()} end={effective_end_date.isoformat()} "
        f"days={days} chunk_days={min(chunk_days, days)} writes=0",
        flush=True,
    )
    runner(
        conn,
        api_key=api_key,
        start_date=start_date,
        end_date=effective_end_date,
        chunk_days=min(chunk_days, days),
        dry_run=True,
    )


def _run_optional_a2_regime_audit(
    conn: psycopg.Connection,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(A2_REGIME_AUDIT_ENABLED_ENV)):
        return

    regime_code = str(values.get(A2_REGIME_AUDIT_CODE_ENV) or "").strip()
    if not regime_code:
        raise ValueError("A2 history regime audit code is required")

    regime = conn.execute(
        """
        SELECT regime_code, source_system, window_start, window_end,
               chunk_days, activated_at, activation_note
        FROM a2_history_data_regimes
        WHERE regime_code = %s
        """,
        (regime_code,),
    ).fetchone()
    if regime is None:
        print(
            f"A2_HISTORY_REGIME_AUDIT status=NOT_FOUND regime={regime_code}",
            flush=True,
        )
        return

    counts = conn.execute(
        """
        WITH scoped AS (
            SELECT
                mso.match_id,
                mso.player_id,
                mso.source_observed_at,
                mso.as_of
            FROM match_stat_observations mso
            JOIN LATERAL (
                SELECT mv.match_start_at
                FROM match_versions mv
                WHERE mv.match_id = mso.match_id
                  AND mv.as_of <= mso.as_of
                  AND mv.source_observed_at <= mso.as_of
                ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
            WHERE mso.source_system = %s
              AND (mv.match_start_at AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
        ),
        per_match AS (
            SELECT
                match_id,
                count(*) AS row_count,
                count(DISTINCT player_id) AS player_count
            FROM scoped
            GROUP BY match_id
        )
        SELECT
            (SELECT count(*) FROM scoped),
            (SELECT count(*) FROM scoped WHERE source_observed_at >= %s),
            (SELECT count(*) FROM scoped WHERE source_observed_at < %s),
            (SELECT count(DISTINCT match_id) FROM scoped),
            (SELECT count(*) FROM per_match WHERE row_count = 2 AND player_count = 2),
            (SELECT min(source_observed_at) FROM scoped),
            (SELECT max(source_observed_at) FROM scoped)
        """,
        (
            regime[1],
            regime[2],
            regime[3],
            regime[5],
            regime[5],
        ),
    ).fetchone()
    assert counts is not None

    print(
        "A2_HISTORY_REGIME_AUDIT "
        f"status=FOUND regime={regime[0]} source={regime[1]} "
        f"start={regime[2].isoformat()} end={regime[3].isoformat()} "
        f"chunk_days={int(regime[4])} activated_at={regime[5].isoformat()} "
        f"total_rows={int(counts[0])} post_activation_rows={int(counts[1])} "
        f"pre_activation_rows={int(counts[2])} distinct_matches={int(counts[3])} "
        f"complete_two_player_matches={int(counts[4])} "
        f"first_observed_at={counts[5].isoformat() if counts[5] else '-'} "
        f"last_observed_at={counts[6].isoformat() if counts[6] else '-'}",
        flush=True,
    )


def _run_optional_stale_market_selftest(
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(MARKET_STALE_SELFTEST_ENABLED_ENV)):
        return

    decision_as_of = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)
    stale_observed_at = decision_as_of - timedelta(minutes=16)
    policy = MarketPolicyV1(
        market_policy_version_id=1,
        policy_key="SELFTEST_MARKET",
        policy_version="STALE_SELFTEST_V1",
        atp_main_min_edge_pct=Decimal("0.05"),
        challenger_min_edge_pct=Decimal("0.05"),
        long_price_threshold=Decimal("4.00"),
        long_price_min_edge_pct=Decimal("0.10"),
        default_devig_method="POWER",
        sharp_reference_policy_code="PINNACLE_SHARP",
        odds_max_staleness_minutes=15,
        type_b_enabled=False,
    )
    manager = PersistedManagerConsensusRow(
        manager_consensus_result_id=1,
        prediction_run_id=1,
        match_id=1,
        manager_policy_version_id=1,
        manager_status="CONSENSUS",
        selected_side=1,
        consensus_probability=Decimal("0.60"),
        active_agent_count=3,
        abstaining_agent_count=0,
        probability_spread=Decimal("0.01"),
        reason_code="CONSENSUS",
        as_of=decision_as_of,
    )
    sharp = SharpReferenceSpec(
        source_system="SHARP",
        bookmaker_code="PINNACLE",
    )
    snapshots = [
        OddsSnapshotV1(
            odds_snapshot_id=1,
            match_id=1,
            source_system="SHARP",
            bookmaker_code="PINNACLE",
            market_type="MATCH_WINNER",
            selection_type="PLAYER_1",
            decimal_price=Decimal("2.00"),
            market_status="OPEN",
            booksum=None,
            source_snapshot_id="stale-selftest",
            source_observed_at=stale_observed_at,
            as_of=stale_observed_at,
        ),
        OddsSnapshotV1(
            odds_snapshot_id=2,
            match_id=1,
            source_system="SHARP",
            bookmaker_code="PINNACLE",
            market_type="MATCH_WINNER",
            selection_type="PLAYER_2",
            decimal_price=Decimal("2.00"),
            market_status="OPEN",
            booksum=None,
            source_snapshot_id="stale-selftest",
            source_observed_at=stale_observed_at,
            as_of=stale_observed_at,
        ),
    ]

    decision = evaluate_market_match(
        manager_row=manager,
        policy=policy,
        competition_tier="ATP_250",
        sharp_reference=sharp,
        odds_snapshots=snapshots,
        decision_as_of=decision_as_of,
    )
    if (
        decision.decision_status != "NO_BET"
        or decision.reason_code != "STALE_MARKET"
        or decision.suggested_stake_fraction is not None
    ):
        raise RuntimeError(
            "stale market self-test failed closed-safety invariant "
            f"status={decision.decision_status} reason={decision.reason_code} "
            f"stake={decision.suggested_stake_fraction}"
        )

    print(
        "MARKET_STALE_SELFTEST status=PASS "
        "decision=NO_BET reason=STALE_MARKET stake=- writes=0",
        flush=True,
    )


def _run_optional_pinnacle_schedule_audit(
    conn: psycopg.Connection,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(PINNACLE_SCHEDULE_AUDIT_ENABLED_ENV)):
        return

    raw_ids = str(values.get(PINNACLE_SCHEDULE_AUDIT_MATCH_IDS_ENV) or "").strip()
    if not raw_ids:
        raise ValueError("Pinnacle schedule audit match IDs are required")

    match_ids: list[int] = []
    for token in raw_ids.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            match_id = int(token)
        except ValueError as exc:
            raise ValueError("Pinnacle schedule audit match IDs must be integers") from exc
        if match_id <= 0:
            raise ValueError("Pinnacle schedule audit match IDs must be positive")
        if match_id not in match_ids:
            match_ids.append(match_id)

    if not match_ids:
        raise ValueError("Pinnacle schedule audit match IDs are required")
    if len(match_ids) > PINNACLE_SCHEDULE_AUDIT_MAX_MATCHES:
        raise ValueError(
            "Pinnacle schedule audit match count exceeds "
            f"{PINNACLE_SCHEDULE_AUDIT_MAX_MATCHES}"
        )

    version_rows = conn.execute(
        """
        SELECT
            mv.match_version_id,
            mv.match_id,
            p1.canonical_name,
            p2.canonical_name,
            t.canonical_name,
            mv.match_start_at,
            mv.match_status::text,
            mv.source_observed_at,
            mv.as_of,
            mvs.source_system,
            mvs.source_match_id,
            mvs.source_observed_at,
            mvs.as_of
        FROM match_versions mv
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        LEFT JOIN match_version_sources mvs
          ON mvs.match_version_id = mv.match_version_id
        WHERE mv.match_id = ANY(%s)
        ORDER BY
            mv.match_id,
            mv.source_observed_at,
            mv.as_of,
            mv.match_version_id,
            mvs.match_version_source_id
        """,
        (match_ids,),
    ).fetchall()

    source_rows = conn.execute(
        """
        SELECT
            match_id,
            source_system,
            source_match_id,
            match_version_id,
            created_at
        FROM match_source_ids
        WHERE match_id = ANY(%s)
        ORDER BY match_id, source_system, source_match_id, match_source_id
        """,
        (match_ids,),
    ).fetchall()

    print(
        "PINNACLE_SCHEDULE_AUDIT_START "
        f"matches={','.join(str(match_id) for match_id in match_ids)} "
        f"version_rows={len(version_rows)} source_rows={len(source_rows)} writes=0",
        flush=True,
    )

    for row in version_rows:
        print(
            "PINNACLE_SCHEDULE_AUDIT_VERSION "
            f"match_id={int(row[1])} match_version_id={int(row[0])} "
            f"player1={str(row[2]).replace(' ', '_')} "
            f"player2={str(row[3]).replace(' ', '_')} "
            f"tournament={str(row[4]).replace(' ', '_')} "
            f"match_start_at={row[5].isoformat()} status={row[6]} "
            f"version_observed_at={row[7].isoformat()} version_as_of={row[8].isoformat()} "
            f"source_system={row[9] or '-'} source_match_id={row[10] or '-'} "
            f"source_observed_at={row[11].isoformat() if row[11] else '-'} "
            f"source_as_of={row[12].isoformat() if row[12] else '-'} writes=0",
            flush=True,
        )

    for row in source_rows:
        print(
            "PINNACLE_SCHEDULE_AUDIT_SOURCE "
            f"match_id={int(row[0])} source_system={row[1]} "
            f"source_match_id={row[2] or '-'} "
            f"match_version_id={row[3] if row[3] is not None else '-'} "
            f"created_at={row[4].isoformat()} writes=0",
            flush=True,
        )

    if not version_rows:
        print(
            "PINNACLE_SCHEDULE_AUDIT_EMPTY "
            f"matches={','.join(str(match_id) for match_id in match_ids)} writes=0",
            flush=True,
        )


def _run_optional_live_tennis_surface_probe(
    conn: psycopg.Connection,
    *,
    target_date: date,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., int] | None = None,
) -> None:
    values = os.environ if env is None else env
    if not _flag_enabled(values.get(LIVE_TENNIS_SURFACE_PROBE_ENABLED_ENV)):
        return

    try:
        max_pages = int(
            str(
                values.get(LIVE_TENNIS_SURFACE_PROBE_MAX_PAGES_ENV)
                or LIVE_TENNIS_SURFACE_PROBE_DEFAULT_PAGES
            ).strip()
        )
    except ValueError as exc:
        raise ValueError("Live Tennis surface probe max pages must be an integer") from exc
    if not 1 <= max_pages <= LIVE_TENNIS_SURFACE_PROBE_MAX_PAGES:
        raise ValueError(
            "Live Tennis surface probe max pages must be between 1 and "
            f"{LIVE_TENNIS_SURFACE_PROBE_MAX_PAGES}"
        )

    try:
        api_key = load_secret("LIVE_TENNIS_API_KEY")
    except MissingCredentialError as exc:
        raise ValueError("Live Tennis surface probe requires LIVE_TENNIS_API_KEY") from exc
    if not api_key:
        raise ValueError("Live Tennis surface probe requires LIVE_TENNIS_API_KEY")

    if runner is None:
        from sports_edge_tennis.live.live_tennis_surface_probe import (
            run_probe as live_tennis_runner,
        )

        runner = live_tennis_runner

    print(
        "LIVE_TENNIS_SURFACE_PROBE_DISPATCH "
        f"date={target_date.isoformat()} max_pages={max_pages} writes=0",
        flush=True,
    )
    runner(
        conn,
        api_key=api_key,
        target_date=target_date,
        max_pages=max_pages,
    )


def _run_offered_market_history_audit(
    conn: psycopg.Connection,
    *,
    max_decisions: int = OFFERED_MARKET_AUDIT_DEFAULT_MAX_DECISIONS,
) -> int:
    if not 1 <= max_decisions <= OFFERED_MARKET_AUDIT_MAX_DECISIONS:
        raise ValueError(
            "offered market audit max decisions must be between 1 and "
            f"{OFFERED_MARKET_AUDIT_MAX_DECISIONS}"
        )

    rows = conn.execute(
        """
        SELECT
            d.decision_id,
            d.prediction_run_id,
            d.match_id,
            d.as_of,
            mcr.selected_side,
            src.source_match_id,
            mpv.odds_max_staleness_minutes
        FROM decisions d
        JOIN manager_consensus_results mcr
          ON mcr.manager_consensus_result_id = d.manager_consensus_result_id
        JOIN market_policy_versions mpv
          ON mpv.market_policy_version_id = d.market_policy_version_id
        LEFT JOIN LATERAL (
            SELECT source_match_id
            FROM match_source_ids
            WHERE match_id = d.match_id
              AND source_system = 'API_TENNIS'
            ORDER BY match_source_id DESC
            LIMIT 1
        ) src ON TRUE
        WHERE d.market_policy_version_id IS NOT NULL
          AND d.reason_code = 'MISSING_MARKET'
          AND mcr.manager_status = 'CONSENSUS'
          AND mcr.selected_side IN (1, 2)
        ORDER BY d.as_of DESC, d.decision_id DESC
        LIMIT %s
        """,
        (max_decisions,),
    ).fetchall()

    print(
        "OFFERED_MARKET_HISTORY_AUDIT_START "
        f"decisions={len(rows)} max_decisions={max_decisions} writes=0 provider_requests=0",
        flush=True,
    )

    for row in rows:
        (
            decision_id,
            prediction_run_id,
            match_id,
            decision_as_of,
            selected_side,
            source_match_id,
            odds_ttl_minutes,
        ) = row
        cutoff = decision_as_of - timedelta(minutes=int(odds_ttl_minutes))
        selected_type = "PLAYER_1" if int(selected_side) == 1 else "PLAYER_2"

        raw_event_rows = 0
        latest_raw_at = None
        if source_match_id is not None:
            raw = conn.execute(
                """
                SELECT count(*), max(received_at)
                FROM raw_odds_observations
                WHERE provider_code = 'API_TENNIS'
                  AND provider_event_id = %s
                  AND received_at >= %s
                  AND received_at <= %s
                """,
                (str(source_match_id), cutoff, decision_as_of),
            ).fetchone()
            if raw is not None:
                raw_event_rows = int(raw[0])
                latest_raw_at = raw[1]

        offered = conn.execute(
            """
            SELECT
                count(*) FILTER (
                    WHERE source_system = 'OFFERED'
                ) AS total_offered_rows,
                count(*) FILTER (
                    WHERE source_system = 'OFFERED'
                      AND selection_type = %s::market_selection_type
                      AND market_status = 'OPEN'
                      AND decimal_price > 1
                      AND source_observed_at >= %s
                      AND source_observed_at <= %s
                ) AS fresh_selected_rows,
                max(source_observed_at) FILTER (
                    WHERE source_system = 'OFFERED'
                      AND source_observed_at <= %s
                ) AS latest_offered_at
            FROM odds_snapshots
            WHERE match_id = %s
            """,
            (
                selected_type,
                cutoff,
                decision_as_of,
                decision_as_of,
                int(match_id),
            ),
        ).fetchone()
        assert offered is not None
        total_offered_rows = int(offered[0])
        fresh_selected_rows = int(offered[1])
        latest_offered_at = offered[2]

        if source_match_id is None:
            classification = "NO_API_TENNIS_EVENT_ID"
        elif raw_event_rows == 0:
            classification = "PROVIDER_EVENT_ABSENT"
        elif total_offered_rows == 0:
            classification = "PROVIDER_PRESENT_NO_NORMALIZED_MARKET"
        elif fresh_selected_rows == 0:
            if latest_offered_at is None:
                classification = "PROVIDER_PRESENT_NO_NORMALIZED_MARKET"
            elif latest_offered_at < cutoff:
                classification = "PROVIDER_PRESENT_STALE_SELECTED_SIDE"
            else:
                classification = "PROVIDER_PRESENT_SELECTED_SIDE_GAP"
        else:
            classification = "OTHER"

        print(
            "OFFERED_MARKET_HISTORY_AUDIT "
            f"decision_id={int(decision_id)} prediction_run_id={int(prediction_run_id)} "
            f"match_id={int(match_id)} as_of={decision_as_of.isoformat()} "
            f"selected_side={int(selected_side)} api_event_id={source_match_id or '-'} "
            f"ttl_minutes={int(odds_ttl_minutes)} raw_event_rows={raw_event_rows} "
            f"latest_raw_at={latest_raw_at.isoformat() if latest_raw_at else '-'} "
            f"total_offered_rows={total_offered_rows} "
            f"fresh_selected_rows={fresh_selected_rows} "
            f"latest_offered_at={latest_offered_at.isoformat() if latest_offered_at else '-'} "
            f"classification={classification} writes=0 provider_requests=0",
            flush=True,
        )

    print(
        "OFFERED_MARKET_HISTORY_AUDIT_SUMMARY "
        f"decisions={len(rows)} writes=0 provider_requests=0",
        flush=True,
    )
    return len(rows)


def ensure_checkpoint_table(conn: psycopg.Connection) -> None:
    conn.execute(_CHECKPOINT_DDL)


def _load_checkpoint(
    conn: psycopg.Connection, *, checkpoint_key: str
) -> HistoryRefreshCheckpoint | None:
    row = conn.execute(
        """
        SELECT checkpoint_key, window_start, window_end, requested_chunk_days,
               next_start_date, completed_chunks, last_completed_end, last_error_kind
        FROM history_refresh_checkpoints
        WHERE checkpoint_key = %s AND schema_version = %s
        """,
        (checkpoint_key, CHECKPOINT_SCHEMA_VERSION),
    ).fetchone()
    if row is None:
        return None
    return HistoryRefreshCheckpoint(
        checkpoint_key=row[0], window_start=row[1], window_end=row[2],
        requested_chunk_days=int(row[3]), next_start_date=row[4],
        completed_chunks=int(row[5]), last_completed_end=row[6], last_error_kind=row[7],
    )


def _create_checkpoint(
    conn: psycopg.Connection, *, checkpoint_key: str,
    start_date: date, end_date: date, chunk_days: int,
) -> HistoryRefreshCheckpoint:
    conn.execute(
        """
        INSERT INTO history_refresh_checkpoints (
            checkpoint_key, schema_version, window_start, window_end,
            requested_chunk_days, next_start_date, completed_chunks,
            last_completed_end, last_error_kind, last_error_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, 0, NULL, NULL, NULL, CURRENT_TIMESTAMP)
        ON CONFLICT (checkpoint_key) DO UPDATE
        SET schema_version = EXCLUDED.schema_version,
            window_start = EXCLUDED.window_start,
            window_end = EXCLUDED.window_end,
            requested_chunk_days = EXCLUDED.requested_chunk_days,
            next_start_date = EXCLUDED.next_start_date,
            completed_chunks = 0,
            last_completed_end = NULL,
            last_error_kind = NULL,
            last_error_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        """,
        (checkpoint_key, CHECKPOINT_SCHEMA_VERSION, start_date, end_date, chunk_days, start_date),
    )
    checkpoint = _load_checkpoint(conn, checkpoint_key=checkpoint_key)
    assert checkpoint is not None
    return checkpoint


def prepare_checkpoint(
    conn: psycopg.Connection, *, checkpoint_key: str,
    start_date: date, end_date: date, chunk_days: int,
    restart_window: bool = False,
) -> HistoryRefreshCheckpoint:
    """Load progress and extend the same window without replaying completed chunks."""
    ensure_checkpoint_table(conn)
    existing = _load_checkpoint(conn, checkpoint_key=checkpoint_key)
    if restart_window or existing is None or existing.window_start != start_date:
        return _create_checkpoint(
            conn, checkpoint_key=checkpoint_key, start_date=start_date,
            end_date=end_date, chunk_days=chunk_days,
        )
    if end_date < existing.window_end and existing.next_start_date > end_date + timedelta(days=1):
        raise ValueError("requested end_date is behind completed checkpoint progress")
    conn.execute(
        """
        UPDATE history_refresh_checkpoints
        SET window_end = %s, requested_chunk_days = %s, updated_at = CURRENT_TIMESTAMP
        WHERE checkpoint_key = %s
        """,
        (end_date, chunk_days, checkpoint_key),
    )
    checkpoint = _load_checkpoint(conn, checkpoint_key=checkpoint_key)
    assert checkpoint is not None
    return checkpoint


def _record_chunk_success(
    conn: psycopg.Connection, *, checkpoint_key: str, chunk_end: date
) -> None:
    conn.execute(
        """
        UPDATE history_refresh_checkpoints
        SET next_start_date = %s,
            completed_chunks = completed_chunks + 1,
            last_completed_end = %s,
            last_error_kind = NULL,
            last_error_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE checkpoint_key = %s
        """,
        (chunk_end + timedelta(days=1), chunk_end, checkpoint_key),
    )


def _record_failure(conn: psycopg.Connection, *, checkpoint_key: str, kind: str) -> None:
    conn.execute(
        """
        UPDATE history_refresh_checkpoints
        SET last_error_kind = %s, last_error_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE checkpoint_key = %s
        """,
        (kind, checkpoint_key),
    )


def _attempt_chunk(
    conn: psycopg.Connection, *, api_key: str, start_date: date, end_date: date,
    max_attempts: int, backoff_seconds: float,
    refresh_chunk: RefreshChunk, sleep: Sleep,
) -> HistoryRefreshChunkOutcome:
    for attempt in range(1, max_attempts + 1):
        try:
            return refresh_chunk(
                conn, api_key=api_key, start_date=start_date, end_date=end_date
            )
        except TransportError as exc:
            if exc.kind not in _RETRYABLE_KINDS or attempt >= max_attempts:
                raise
            delay = backoff_seconds * (2 ** (attempt - 1))
            print(
                "HISTORY_REFRESH_RETRY "
                f"start={start_date.isoformat()} end={end_date.isoformat()} "
                f"attempt={attempt + 1}/{max_attempts} kind={exc.kind} "
                f"sleep_seconds={delay:g}",
                flush=True,
            )
            sleep(delay)
    raise AssertionError("unreachable")


def _process_resilient_chunk(
    conn: psycopg.Connection, *, api_key: str, start_date: date, end_date: date,
    checkpoint_key: str, max_attempts: int, backoff_seconds: float,
    min_chunk_days: int, refresh_chunk: RefreshChunk, sleep: Sleep,
    totals: HistoryRefreshTotals,
) -> None:
    try:
        outcome = _attempt_chunk(
            conn, api_key=api_key, start_date=start_date, end_date=end_date,
            max_attempts=max_attempts, backoff_seconds=backoff_seconds,
            refresh_chunk=refresh_chunk, sleep=sleep,
        )
    except TransportError as exc:
        span_days = (end_date - start_date).days + 1
        if exc.kind in _RETRYABLE_KINDS and span_days > min_chunk_days:
            left_days = span_days // 2
            split_end = start_date + timedelta(days=left_days - 1)
            right_start = split_end + timedelta(days=1)
            print(
                "HISTORY_REFRESH_SPLIT "
                f"start={start_date.isoformat()} end={end_date.isoformat()} kind={exc.kind} "
                f"left={start_date.isoformat()}..{split_end.isoformat()} "
                f"right={right_start.isoformat()}..{end_date.isoformat()}",
                flush=True,
            )
            _process_resilient_chunk(
                conn, api_key=api_key, start_date=start_date, end_date=split_end,
                checkpoint_key=checkpoint_key, max_attempts=max_attempts,
                backoff_seconds=backoff_seconds, min_chunk_days=min_chunk_days,
                refresh_chunk=refresh_chunk, sleep=sleep, totals=totals,
            )
            _process_resilient_chunk(
                conn, api_key=api_key, start_date=right_start, end_date=end_date,
                checkpoint_key=checkpoint_key, max_attempts=max_attempts,
                backoff_seconds=backoff_seconds, min_chunk_days=min_chunk_days,
                refresh_chunk=refresh_chunk, sleep=sleep, totals=totals,
            )
            return
        _record_failure(conn, checkpoint_key=checkpoint_key, kind=exc.kind)
        print(
            "HISTORY_REFRESH_FAILED "
            f"kind={exc.kind} failed_start={start_date.isoformat()} "
            f"failed_end={end_date.isoformat()} checkpoint_key={checkpoint_key}",
            flush=True,
        )
        raise

    totals.add(outcome)
    _record_chunk_success(conn, checkpoint_key=checkpoint_key, chunk_end=end_date)
    print(
        "HISTORY_REFRESH_CHUNK "
        f"start={outcome.start_date.isoformat()} end={outcome.end_date.isoformat()} "
        f"fixtures_seen={outcome.fixtures_seen} fixtures_ingested={outcome.fixtures_ingested} "
        f"fixture_rejections={outcome.fixture_rejections} "
        f"terminal_seen={outcome.terminal_results_seen} "
        f"results_inserted={outcome.results_inserted} "
        f"results_unchanged={outcome.results_unchanged} "
        f"result_rejections={outcome.result_rejections}",
        flush=True,
    )


def run_resilient_history_refresh(
    conn: psycopg.Connection, *, api_key: str, start_date: date, end_date: date,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    checkpoint_key: str = DEFAULT_CHECKPOINT_KEY,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    min_chunk_days: int = DEFAULT_MIN_CHUNK_DAYS,
    restart_window: bool = False,
    refresh_chunk: RefreshChunk = refresh_history_chunk,
    sleep: Sleep = time.sleep,
) -> HistoryRefreshTotals:
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    if not 1 <= chunk_days <= MAX_CHUNK_DAYS:
        raise ValueError(f"chunk_days must be between 1 and {MAX_CHUNK_DAYS}")
    if not 1 <= min_chunk_days <= chunk_days:
        raise ValueError("min_chunk_days must be between 1 and chunk_days")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds must be >= 0")
    if (end_date - start_date).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError(f"window may not exceed {MAX_WINDOW_DAYS} days")

    lock_row = conn.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)).fetchone()
    if not lock_row or not bool(lock_row[0]):
        raise RuntimeError("history_refresh_already_running")
    try:
        checkpoint = prepare_checkpoint(
            conn, checkpoint_key=checkpoint_key, start_date=start_date,
            end_date=end_date, chunk_days=chunk_days, restart_window=restart_window,
        )
        resume_from = max(start_date, checkpoint.next_start_date)
        totals = HistoryRefreshTotals()
        print(
            "HISTORY_REFRESH_RESUME "
            f"checkpoint_key={checkpoint_key} resume_from={resume_from.isoformat()} "
            f"requested_start={start_date.isoformat()} requested_end={end_date.isoformat()} "
            f"completed_chunks={checkpoint.completed_chunks}",
            flush=True,
        )
        cursor = resume_from
        while cursor <= end_date:
            chunk_end = min(end_date, cursor + timedelta(days=chunk_days - 1))
            _process_resilient_chunk(
                conn, api_key=api_key, start_date=cursor, end_date=chunk_end,
                checkpoint_key=checkpoint_key, max_attempts=max_attempts,
                backoff_seconds=backoff_seconds, min_chunk_days=min_chunk_days,
                refresh_chunk=refresh_chunk, sleep=sleep, totals=totals,
            )
            cursor = chunk_end + timedelta(days=1)
        return totals
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))


def main(argv: list[str] | None = None) -> int:
    default_end = _default_end_date()
    parser = argparse.ArgumentParser(
        description="Resilient resumable API-Tennis historical result refresh"
    )
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat, default=default_end)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    parser.add_argument("--min-chunk-days", type=int, default=DEFAULT_MIN_CHUNK_DAYS)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--backoff-seconds", type=float, default=DEFAULT_BACKOFF_SECONDS)
    parser.add_argument("--checkpoint-key", default=DEFAULT_CHECKPOINT_KEY)
    parser.add_argument("--restart-window", action="store_true")
    parser.add_argument("--dsn", default=_db_dsn())
    args = parser.parse_args(argv)

    start_date = args.start_date or (args.end_date - timedelta(days=2))
    if args.end_date < start_date:
        print("HISTORY_REFRESH_BLOCKED reason=INVALID_DATE_RANGE", flush=True)
        return 2
    if not 1 <= args.chunk_days <= MAX_CHUNK_DAYS:
        print(f"HISTORY_REFRESH_BLOCKED reason=CHUNK_DAYS_MAX_{MAX_CHUNK_DAYS}", flush=True)
        return 2
    if not 1 <= args.min_chunk_days <= args.chunk_days:
        print("HISTORY_REFRESH_BLOCKED reason=INVALID_MIN_CHUNK_DAYS", flush=True)
        return 2
    if args.max_attempts < 1 or args.backoff_seconds < 0:
        print("HISTORY_REFRESH_BLOCKED reason=INVALID_RETRY_CONFIG", flush=True)
        return 2
    window_days = (args.end_date - start_date).days + 1
    if window_days > MAX_WINDOW_DAYS:
        print(f"HISTORY_REFRESH_BLOCKED reason=WINDOW_DAYS_MAX_{MAX_WINDOW_DAYS}", flush=True)
        return 2
    if not args.dsn:
        print("HISTORY_REFRESH_BLOCKED reason=NO_DATABASE", flush=True)
        return 2

    if _flag_enabled(os.environ.get(OFFERED_MARKET_AUDIT_ONLY_ENV)):
        try:
            max_decisions = int(
                str(
                    os.environ.get(OFFERED_MARKET_AUDIT_MAX_DECISIONS_ENV)
                    or OFFERED_MARKET_AUDIT_DEFAULT_MAX_DECISIONS
                ).strip()
            )
        except ValueError:
            print(
                "OFFERED_MARKET_HISTORY_AUDIT_BLOCKED reason=INVALID_MAX_DECISIONS",
                flush=True,
            )
            return 2
        try:
            with psycopg.connect(args.dsn, autocommit=True) as conn:
                _run_offered_market_history_audit(
                    conn,
                    max_decisions=max_decisions,
                )
        except (ValueError, RuntimeError, psycopg.Error) as exc:
            print(
                "OFFERED_MARKET_HISTORY_AUDIT_BLOCKED "
                f"reason={str(exc).upper()}",
                flush=True,
            )
            return 2
        return 0

    try:
        api_key = load_secret("API_TENNIS_API_KEY")
    except MissingCredentialError:
        api_key = None
    if not api_key:
        print("HISTORY_REFRESH_BLOCKED reason=NO_API_TENNIS_KEY", flush=True)
        return 2

    print(
        "HISTORY_REFRESH_START "
        f"start_date={start_date.isoformat()} end_date={args.end_date.isoformat()} "
        f"chunk_days={args.chunk_days} min_chunk_days={args.min_chunk_days} "
        f"max_attempts={args.max_attempts} checkpoint_key={args.checkpoint_key}",
        flush=True,
    )
    try:
        with psycopg.connect(args.dsn, autocommit=True) as conn:
            totals = run_resilient_history_refresh(
                conn, api_key=api_key, start_date=start_date, end_date=args.end_date,
                chunk_days=args.chunk_days, checkpoint_key=args.checkpoint_key,
                max_attempts=args.max_attempts, backoff_seconds=args.backoff_seconds,
                min_chunk_days=args.min_chunk_days, restart_window=args.restart_window,
            )
            _run_optional_a2_stats_dry_run(
                conn,
                api_key=api_key,
                end_date=args.end_date,
            )
            _run_optional_a2_regime_audit(conn)
            _run_optional_stale_market_selftest()
            _run_optional_pinnacle_schedule_audit(conn)
            _run_optional_live_tennis_surface_probe(
                conn,
                target_date=datetime.now(UTC).date(),
            )
    except TransportError:
        return 2
    except (ValueError, RuntimeError) as exc:
        print(f"HISTORY_REFRESH_BLOCKED reason={str(exc).upper()}", flush=True)
        return 2

    print(
        "HISTORY_REFRESH_SUCCESS "
        f"completed_chunks={totals.completed_chunks} fixtures_seen={totals.fixtures_seen} "
        f"fixtures_ingested={totals.fixtures_ingested} fixture_rejections={totals.fixture_rejections} "
        f"terminal_seen={totals.terminal_results_seen} results_inserted={totals.results_inserted} "
        f"results_unchanged={totals.results_unchanged} result_rejections={totals.result_rejections}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
