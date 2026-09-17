"""Production wrapper: prepare schema, run live horizon, then settle matured Type A BETs.

Keeping this wrapper additive avoids changing the already-stable live orchestration path.
Production settlement remains fail-closed. The prospective shadow strategy and runtime
model diagnostics are intentionally fail-isolated: they record evidence and analytics
without changing Today, Manager, or production decision/publication behavior.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from sports_edge_tennis.live.batch import main as live_batch_main
from sports_edge_tennis.market import SharpReferenceSpec
from sports_edge_tennis.settlement import (
    SettlementEvidenceConflict,
    settle_type_a_match_winner_decisions,
)


_QUARTER_KELLY_MIGRATION = Path("db/migrations/0010_quarter_kelly_stake_v1.up.sql")
_STAKING_CONSTRAINTS = frozenset(
    {
        "chk_decisions_staking_probability",
        "chk_decisions_staking_decimal_price",
        "chk_decisions_suggested_stake_fraction",
        "chk_decisions_no_positive_stake_without_bet",
        "chk_decisions_staking_rule_coherent",
    }
)
_SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")
SHADOW_STRATEGY_CODE = "A1_A2_AGREE_A3_ABSTAIN_V1"


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _log(message: str) -> None:
    print(message, flush=True)


def _quarter_kelly_schema_state(conn: psycopg.Connection) -> tuple[bool, frozenset[str]]:
    column_present = bool(
        conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'decisions'
                  AND column_name = 'staking_rule_version'
            )
            """
        ).fetchone()[0]
    )
    rows = conn.execute(
        """
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.decisions'::regclass
          AND conname = ANY(%s)
        """,
        (list(_STAKING_CONSTRAINTS),),
    ).fetchall()
    return column_present, frozenset(str(row[0]) for row in rows)


def _ensure_quarter_kelly_schema(
    conn: psycopg.Connection,
    *,
    migration_path: Path = _QUARTER_KELLY_MIGRATION,
) -> str:
    """Apply migration 0010 once, then verify its sentinel column and constraints."""
    column_present, constraints = _quarter_kelly_schema_state(conn)
    if column_present and constraints == _STAKING_CONSTRAINTS:
        return "EXISTING"
    if column_present or constraints:
        missing = sorted(_STAKING_CONSTRAINTS - constraints)
        raise RuntimeError(
            "quarter-Kelly schema is partially applied "
            f"column_present={column_present} missing_constraints={','.join(missing) or 'none'}"
        )
    if not migration_path.is_file():
        raise RuntimeError(f"required migration file missing: {migration_path}")

    conn.execute(migration_path.read_text(encoding="utf-8"))
    column_present, constraints = _quarter_kelly_schema_state(conn)
    if not column_present or constraints != _STAKING_CONSTRAINTS:
        missing = sorted(_STAKING_CONSTRAINTS - constraints)
        raise RuntimeError(
            "quarter-Kelly schema verification failed "
            f"column_present={column_present} missing_constraints={','.join(missing) or 'none'}"
        )
    return "APPLIED"


def _shadow_cycle(
    dsn: str,
    *,
    cycle_started_at: datetime,
    captured_at: datetime,
) -> None:
    """Run the entire shadow lane without allowing it to affect production success."""
    try:
        from sports_edge_tennis.performance.live_shadow import (
            observe_pending_live_shadow_runs,
        )
        from sports_edge_tennis.performance.shadow_strategy import (
            activate_shadow_strategy,
            finalize_shadow_clv,
            load_shadow_strategy_summary,
            settle_shadow_positions,
        )

        with psycopg.connect(dsn, autocommit=True) as conn:
            activated_at = activate_shadow_strategy(
                conn,
                activated_at=cycle_started_at,
            )
            outcomes = observe_pending_live_shadow_runs(
                conn,
                observed_at=captured_at,
                sharp_reference=_SHARP,
            )
            for outcome in outcomes:
                _log(
                    "SHADOW_OBSERVATION "
                    f"strategy={SHADOW_STRATEGY_CODE} "
                    f"prediction_run_id={outcome.prediction_run_id} "
                    f"candidates={outcome.candidates} "
                    f"observations={outcome.observations} "
                    f"market_bets={outcome.market_bets} "
                    f"vetoed={outcome.vetoed} blocked={outcome.blocked} "
                    f"positions_opened={outcome.positions_opened}"
                )

            settlement = settle_shadow_positions(
                conn,
                settled_at=captured_at,
            )
            clv = finalize_shadow_clv(
                conn,
                captured_at=captured_at,
                sharp_reference=_SHARP,
            )
            summary = load_shadow_strategy_summary(conn)
    except Exception as exc:
        _log(
            "SHADOW_LANE_FAILED "
            f"strategy={SHADOW_STRATEGY_CODE} error_type={type(exc).__name__}"
        )
        return

    bankroll = summary.bankroll
    _log(
        "SHADOW_SETTLEMENT "
        f"strategy={SHADOW_STRATEGY_CODE} "
        f"eligible={settlement.eligible} pending={settlement.pending} "
        f"inserted={settlement.inserted} existing={settlement.existing} "
        f"won={settlement.won} lost={settlement.lost} "
        f"void={settlement.void} cancelled={settlement.cancelled}"
    )
    _log(
        "SHADOW_CLV "
        f"strategy={SHADOW_STRATEGY_CODE} "
        f"eligible={clv.eligible} inserted={clv.inserted} "
        f"available={clv.available} unavailable={clv.unavailable}"
    )
    _log(
        "SHADOW_SUMMARY "
        f"strategy={SHADOW_STRATEGY_CODE} "
        f"activated_at={activated_at.isoformat()} "
        f"runs={summary.observed_runs} observations={summary.observations} "
        f"positions={summary.positions} settled={summary.settled} "
        f"wins={summary.wins} losses={summary.losses} "
        f"bankroll={bankroll.current_bankroll} roi={bankroll.roi} "
        f"max_drawdown={bankroll.max_drawdown} "
        f"clv_records={summary.clv_records} mean_clv={summary.mean_clv}"
    )


def _a1_evidence_cycle(dsn: str) -> None:
    """Emit cohort-clean A1 Challenger diagnostics without affecting production success."""
    try:
        from sports_edge_tennis.performance.a1_diagnostic import (
            load_a1_live_challenger_diagnostic,
        )
        from sports_edge_tennis.performance.a1_runtime_log import (
            build_a1_runtime_evidence_lines,
        )

        with psycopg.connect(dsn, autocommit=True) as conn:
            diagnostic = load_a1_live_challenger_diagnostic(conn)
        lines = build_a1_runtime_evidence_lines(diagnostic)
    except Exception as exc:
        _log(f"A1_LIVE_EVIDENCE_FAILED error_type={type(exc).__name__}")
        return

    for line in lines:
        _log(line)


def main(argv: list[str] | None = None) -> int:
    dsn = _db_dsn()
    if not dsn:
        _log("SCHEMA_BLOCKED reason=NO_DATABASE")
        return 2

    shadow_cycle_started_at = datetime.now(timezone.utc)

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            schema_status = _ensure_quarter_kelly_schema(conn)
    except (psycopg.Error, OSError, RuntimeError) as exc:
        _log(f"SCHEMA_BOOTSTRAP_FAILED error_type={type(exc).__name__}")
        return 2

    _log(f"SCHEMA_QUARTER_KELLY status={schema_status} migration=0010")

    try:
        from sports_edge_tennis.performance.shadow_strategy import activate_shadow_strategy

        with psycopg.connect(dsn, autocommit=True) as conn:
            activated_at = activate_shadow_strategy(
                conn,
                activated_at=shadow_cycle_started_at,
            )
        _log(
            "SHADOW_ACTIVATION "
            f"strategy={SHADOW_STRATEGY_CODE} activated_at={activated_at.isoformat()}"
        )
    except Exception as exc:
        _log(
            "SHADOW_ACTIVATION_DEFERRED "
            f"strategy={SHADOW_STRATEGY_CODE} error_type={type(exc).__name__}"
        )

    live_status = live_batch_main(argv)
    if live_status != 0:
        _log(f"SETTLEMENT_SKIPPED live_batch_status={live_status}")
        return live_status

    settled_at = datetime.now(timezone.utc)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            outcome = settle_type_a_match_winner_decisions(
                conn,
                settled_at=settled_at,
            )
    except SettlementEvidenceConflict as exc:
        _log(f"SETTLEMENT_CONFLICT error={exc}")
        return 2
    except psycopg.Error as exc:
        _log(f"SETTLEMENT_DB_FAILED error_type={type(exc).__name__}")
        return 2

    _log(
        "SETTLEMENT "
        f"eligible={outcome.eligible_decisions} "
        f"pending_results={outcome.pending_results} "
        f"inserted={outcome.inserted} existing={outcome.existing} "
        f"won={outcome.won} lost={outcome.lost} "
        f"void={outcome.void} cancelled={outcome.cancelled}"
    )

    _shadow_cycle(
        dsn,
        cycle_started_at=shadow_cycle_started_at,
        captured_at=datetime.now(timezone.utc),
    )
    _a1_evidence_cycle(dsn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
