"""Production wrapper: prepare schema, run live horizon, then settle matured Type A BETs.

Keeping this wrapper additive avoids changing the already-stable live orchestration path.
Production settlement remains fail-closed. The prospective shadow strategy and runtime
model/provider diagnostics are intentionally fail-isolated: they record evidence and
analytics without changing Today, Manager, or production decision/publication behavior.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timezone
from pathlib import Path

import psycopg

from sports_edge_tennis.data.model_identity_bridge import ensure_schema as ensure_model_identity_bridge_schema
from sports_edge_tennis.live import production_lock, provider_circuit
from sports_edge_tennis.live.batch import main as live_batch_main
from sports_edge_tennis.live.provider_authority import (
    TARGET_ODDS_MODE,
    load_provider_authority,
)
from sports_edge_tennis.live.validation import validation_mode_enabled
from sports_edge_tennis.live.operational_health import (
    evaluate_live_operational_health,
    persist_live_operational_health,
)
from sports_edge_tennis.market import SharpReferenceSpec
from sports_edge_tennis.settlement import (
    SettlementEvidenceConflict,
    settle_type_a_match_winner_decisions,
)


_QUARTER_KELLY_MIGRATION = Path("db/migrations/0010_quarter_kelly_stake_v1.up.sql")
_PUBLICATION_CONTROL_MIGRATION = Path("db/migrations/0012_publication_control_v1.up.sql")
_STRICT_MANAGER_V2_MIGRATION = Path("db/migrations/0014_strict_three_manager_policy_v2.up.sql")
_ODDSPAPI_LEDGER_MIGRATION = Path("db/migrations/0015_oddspapi_request_ledger_v1.up.sql")
_PUBLICATION_CONTROL_TRIGGER = "trg_publication_control_events_append_only_mutation"
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
LIVE_BATCH_GAP_ALERT_MINUTES = 90


def _previous_completed_production_run(
    conn: psycopg.Connection,
    *,
    before: datetime,
) -> tuple[int, datetime] | None:
    row = conn.execute(
        """
        SELECT prediction_run_id, prediction_finalized_at
        FROM prediction_runs
        WHERE run_status = 'COMPLETED'
          AND experiment_id IS NULL
          AND prediction_finalized_at IS NOT NULL
          AND prediction_finalized_at < %s
        ORDER BY prediction_finalized_at DESC, prediction_run_id DESC
        LIMIT 1
        """,
        (before,),
    ).fetchone()
    if row is None:
        return None
    return int(row[0]), row[1]


def _live_batch_gap_cycle(
    dsn: str,
    *,
    cycle_started_at: datetime,
    threshold_minutes: int = LIVE_BATCH_GAP_ALERT_MINUTES,
) -> None:
    """Emit a fail-isolated operational alert when an hourly production cycle was missed."""
    if threshold_minutes <= 0:
        raise ValueError("threshold_minutes must be positive")

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            previous = _previous_completed_production_run(
                conn,
                before=cycle_started_at,
            )
    except Exception as exc:
        _log(f"LIVE_BATCH_GAP_CHECK_FAILED error_type={type(exc).__name__}")
        return

    if previous is None:
        _log("LIVE_BATCH_GAP_CHECK status=NO_PREVIOUS_COMPLETED_RUN")
        return

    prediction_run_id, finalized_at = previous
    age_minutes = max(
        0,
        int((cycle_started_at - finalized_at).total_seconds() // 60),
    )
    if age_minutes > threshold_minutes:
        _log(
            "LIVE_BATCH_GAP_ALERT "
            f"previous_prediction_run_id={prediction_run_id} "
            f"previous_finalized_at={finalized_at.isoformat()} "
            f"age_minutes={age_minutes} threshold_minutes={threshold_minutes}"
        )
        return

    _log(
        "LIVE_BATCH_GAP_OK "
        f"previous_prediction_run_id={prediction_run_id} "
        f"age_minutes={age_minutes} threshold_minutes={threshold_minutes}"
    )


def _live_operational_health_cycle(
    dsn: str,
    *,
    cycle_started_at: datetime,
    live_status: int,
    failed_slates: int = 0,
) -> None:
    checked_at = datetime.now(timezone.utc)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            health = evaluate_live_operational_health(
                conn,
                cycle_started_at=cycle_started_at,
                live_status=live_status,
                failed_slates=failed_slates,
            )
            persist_live_operational_health(
                conn,
                health=health,
                checked_at=checked_at,
            )
    except Exception as exc:
        _log(f"LIVE_OPERATIONAL_HEALTH_WRITE_FAILED error_type={type(exc).__name__}")
        return

    _log(
        "LIVE_OPERATIONAL_HEALTH "
        f"status={health.status} freshness={health.freshness_state} "
        f"completed_runs={health.completed_runs} targets={health.target_count} "
        f"errors={health.error_count} notes={health.notes.replace(' ', '_')}"
    )


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


def _publication_control_schema_state(
    conn: psycopg.Connection,
) -> tuple[bool, bool]:
    table_present = bool(
        conn.execute(
            "SELECT to_regclass('public.publication_control_events') IS NOT NULL"
        ).fetchone()[0]
    )
    if not table_present:
        return False, False
    trigger_present = bool(
        conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgrelid = 'public.publication_control_events'::regclass
                  AND tgname = %s
                  AND NOT tgisinternal
            )
            """,
            (_PUBLICATION_CONTROL_TRIGGER,),
        ).fetchone()[0]
    )
    return True, trigger_present


def _ensure_publication_control_schema(
    conn: psycopg.Connection,
    *,
    migration_path: Path = _PUBLICATION_CONTROL_MIGRATION,
) -> str:
    """Apply migration 0012 once, then verify the table + append-only trigger."""
    table_present, trigger_present = _publication_control_schema_state(conn)
    if table_present and trigger_present:
        return "EXISTING"
    if table_present or trigger_present:
        raise RuntimeError(
            "publication-control schema is partially applied "
            f"table_present={table_present} trigger_present={trigger_present}"
        )
    if not migration_path.is_file():
        raise RuntimeError(f"required migration file missing: {migration_path}")

    conn.execute(migration_path.read_text(encoding="utf-8"))
    table_present, trigger_present = _publication_control_schema_state(conn)
    if not table_present or not trigger_present:
        raise RuntimeError(
            "publication-control schema verification failed "
            f"table_present={table_present} trigger_present={trigger_present}"
        )
    return "APPLIED"



def _strict_manager_v2_schema_state(
    conn: psycopg.Connection,
) -> tuple[bool, bool]:
    row = conn.execute(
        """
        SELECT
            allow_agent3_abstention,
            three_agent_max_probability_spread,
            agent3_abstention_max_probability_spread,
            min_active_agent_probability
        FROM manager_policy_versions
        WHERE policy_key = 'tennis_manager_consensus'
          AND policy_version = 'v2'
        """
    ).fetchone()
    if row is None:
        return False, False
    valid = (
        row[0] is False
        and str(row[1]) == "0.080000"
        and str(row[2]) == "0.000000"
        and str(row[3]) == "0.500000"
    )
    return True, valid


def _ensure_oddspapi_ledger_schema(
    conn: psycopg.Connection,
    *,
    migration_path: Path = _ODDSPAPI_LEDGER_MIGRATION,
) -> str:
    """Apply migration 0015 once, then verify the ledger + identity-cache tables."""
    if not migration_path.is_file():
        raise RuntimeError(f"required migration file missing: {migration_path}")
    conn.execute(migration_path.read_text(encoding="utf-8"))
    row = conn.execute(
        """
        SELECT
            to_regclass('public.oddspapi_request_ledger') IS NOT NULL,
            to_regclass('public.oddspapi_event_identity_cache') IS NOT NULL
        """
    ).fetchone()
    if not row or not row[0] or not row[1]:
        raise RuntimeError("oddspapi ledger schema incomplete")
    return "READY"


def _ensure_strict_manager_v2_schema(
    conn: psycopg.Connection,
    *,
    migration_path: Path = _STRICT_MANAGER_V2_MIGRATION,
) -> str:
    """Prepare strict Manager v2 without selecting it for live production."""
    present, valid = _strict_manager_v2_schema_state(conn)
    if present and valid:
        return "EXISTING"
    if present and not valid:
        raise RuntimeError("strict Manager v2 policy row exists with unexpected values")
    if not migration_path.is_file():
        raise RuntimeError(f"required migration file missing: {migration_path}")

    conn.execute(migration_path.read_text(encoding="utf-8"))
    present, valid = _strict_manager_v2_schema_state(conn)
    if not present or not valid:
        raise RuntimeError(
            "strict Manager v2 schema verification failed "
            f"present={present} valid={valid}"
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
            f"strategy={SHADOW_STRATEGY_CODE} error_type={type(exc).__name__} "
            f"reason={str(exc).replace(' ', '_').replace(chr(10), '_')}"
        )
        return

    bankroll = summary.bankroll
    _log(
        "SHADOW_SETTLEMENT "
        f"strategy={SHADOW_STRATEGY_CODE} "
        f"eligible={settlement.eligible} pending={settlement.pending} "
        f"inserted={settlement.inserted} existing={settlement.existing} "
        f"reconciled={settlement.reconciled} "
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


def _pending_result_refresh_cycle(dsn: str) -> None:
    """Resolve same-day pending Model Pick results without affecting batch health.

    Additive result-ingestion only. Never raises: a provider or persistence problem
    is reported and the prediction batch continues.
    """
    try:
        from sports_edge_tennis.live.credentials import (
            MissingCredentialError,
            load_secret,
        )
        from sports_edge_tennis.live.pending_result_refresh import (
            refresh_pending_results,
        )

        try:
            api_key = load_secret("API_TENNIS_API_KEY")
        except MissingCredentialError:
            api_key = None
        if not api_key:
            _log("PENDING_RESULT_REFRESH_SKIPPED reason=MISSING_API_TENNIS_KEY")
            return

        with psycopg.connect(dsn, autocommit=True) as conn:
            summary = refresh_pending_results(
                conn,
                api_key=api_key,
                as_of=datetime.now(UTC),
            )
        _log(summary.log_line())
    except Exception as exc:  # noqa: BLE001 - fail-isolated operational lane
        _log(f"PENDING_RESULT_REFRESH_FAILED error_type={type(exc).__name__}")


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


def _pncl_concordance_cycle(dsn: str, *, captured_at: datetime) -> None:
    """Compare Pncl/Pinnacle persisted quotes without changing provider classification."""
    try:
        from sports_edge_tennis.performance.pncl_concordance import (
            build_pncl_concordance_lines,
            load_pncl_pinnacle_concordance,
        )

        with psycopg.connect(dsn, autocommit=True) as conn:
            evidence = load_pncl_pinnacle_concordance(
                conn,
                as_of=captured_at,
                lookback_hours=24,
                max_gap_minutes=15,
            )
        lines = build_pncl_concordance_lines(evidence)
    except Exception as exc:
        _log(f"PNCL_PINNACLE_CONCORDANCE_FAILED error_type={type(exc).__name__}")
        return

    for line in lines:
        _log(line)


def main(argv: list[str] | None = None) -> int:
    dsn = _db_dsn()
    if not dsn:
        _log("SCHEMA_BLOCKED reason=NO_DATABASE")
        return 2

    # Same ownership rule as the scheduled cron entrypoint: acquire the single
    # authoritative environment lock before schema work or provider calls. A
    # second concurrent invocation exits before doing any production work.
    try:
        lock_info = production_lock.acquire_production_batch_lock(dsn)
    except psycopg.Error as exc:
        _log(f"PRODUCTION_LOCK_UNAVAILABLE error_type={type(exc).__name__}")
        return 2
    if lock_info is None:
        _log(
            "PRODUCTION_BATCH_SKIPPED "
            f"status={production_lock.SKIPPED_ALREADY_RUNNING} "
            f"environment={production_lock.default_environment()} "
            f"owner={production_lock.default_owner()}"
        )
        return 0
    _log(
        "PRODUCTION_LOCK_ACQUIRED "
        f"entrypoint=production_batch environment={lock_info.environment} "
        f"owner={lock_info.owner} reentrant={int(lock_info.reentrant)}"
    )
    try:
        return _run_batch(dsn, argv)
    finally:
        production_lock.release_production_batch_lock(lock_info)
        _log(
            "PRODUCTION_LOCK_RELEASED "
            f"entrypoint=production_batch environment={lock_info.environment} "
            f"owner={lock_info.owner}"
        )


def _resolve_provider_decision(
    conn: psycopg.Connection,
    configured_authority,
    *,
    env,
) -> provider_circuit.CircuitDecision:
    """Resolve ONE atomic provider pair and validate readiness for that pair.

    The circuit may intentionally select the fallback pair when the primary is
    rate-limited or unavailable; readiness is then validated against the
    fallback pair, never against primary availability.
    """
    decision = provider_circuit.resolve_provider_pair(
        store=provider_circuit.PostgresCircuitStateStore(conn),
        configured=configured_authority,
        env=env,
        now=datetime.now(timezone.utc),
        probe=provider_circuit.sportradar_health_probe(env),
    )
    provider_circuit.assert_decision_ready(conn, decision=decision, env=env)
    return decision


def _run_batch(dsn: str, argv: list[str] | None = None) -> int:
    shadow_cycle_started_at = datetime.now(timezone.utc)

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            configured_authority = load_provider_authority(os.environ)
            provider_decision = _resolve_provider_decision(
                conn, configured_authority, env=os.environ
            )
    except (psycopg.Error, ValueError, RuntimeError) as exc:
        _log(
            "PROVIDER_AUTHORITY_BLOCKED "
            f"error_type={type(exc).__name__} reason={str(exc).replace(' ', '_')}"
        )
        return 2

    provider_authority = provider_decision.authority
    _log(
        "PROVIDER_AUTHORITY "
        f"data={provider_authority.data_mode} odds={provider_authority.odds_mode} "
        f"circuit={provider_decision.state} reason={provider_decision.reason}"
    )

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            schema_status = _ensure_quarter_kelly_schema(conn)
            publication_control_status = _ensure_publication_control_schema(conn)
            model_identity_bridge_status = ensure_model_identity_bridge_schema(conn)
            strict_manager_v2_status = _ensure_strict_manager_v2_schema(conn)
            oddspapi_ledger_status = "SKIPPED_LEGACY_ODDS"
            if provider_authority.odds_mode == TARGET_ODDS_MODE:
                oddspapi_ledger_status = _ensure_oddspapi_ledger_schema(conn)
    except (psycopg.Error, OSError, RuntimeError) as exc:
        _log(f"SCHEMA_BOOTSTRAP_FAILED error_type={type(exc).__name__}")
        return 2

    _log(f"SCHEMA_QUARTER_KELLY status={schema_status} migration=0010")
    _log(
        "SCHEMA_PUBLICATION_CONTROL "
        f"status={publication_control_status} migration=0012"
    )
    _log(
        "SCHEMA_MODEL_IDENTITY_BRIDGE "
        f"status={model_identity_bridge_status} migration=0013"
    )
    _log(
        "SCHEMA_STRICT_MANAGER_V2 "
        f"status={strict_manager_v2_status} migration=0014 selected={os.environ.get('TENNIS_MANAGER_POLICY_VERSION', 'v1')}"
    )
    _log(f"SCHEMA_ODDSPAPI_LEDGER status={oddspapi_ledger_status} migration=0015")

    _live_batch_gap_cycle(
        dsn,
        cycle_started_at=shadow_cycle_started_at,
    )

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

    live_summary: dict[str, int] = {}

    def capture_live_summary(summary: dict[str, int]) -> None:
        live_summary.update(summary)

    live_status = live_batch_main(
        argv,
        summary_sink=capture_live_summary,
        provider_decision=provider_decision,
    )
    _live_operational_health_cycle(
        dsn,
        cycle_started_at=shadow_cycle_started_at,
        live_status=live_status,
        failed_slates=int(live_summary.get("failed_slates", 0)),
    )
    if live_status != 0:
        _log(f"SETTLEMENT_SKIPPED live_batch_status={live_status}")
        return live_status

    if validation_mode_enabled():
        # Validation runs collect real evidence and rebuild model state but must
        # never mutate settlement truth, publish shadow positions, or notify.
        _log(
            "VALIDATION_MODE "
            "publication=blocked settlement=blocked shadow=blocked notifications=blocked"
        )
        return 0

    # Same-day pending Model Pick result refresh. Runs before settlement so newly
    # persisted terminal results are visible to this cycle's settlement pass.
    _pending_result_refresh_cycle(dsn)

    settled_at = datetime.now(timezone.utc)
    _pncl_concordance_cycle(dsn, captured_at=settled_at)

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
        f"reconciled={outcome.reconciled} "
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
