"""Production wrapper: prepare schema, run live horizon, then settle matured Type A BETs.

Keeping this wrapper additive avoids changing the already-stable live orchestration path.
A non-zero schema/live-batch result prevents settlement. Settlement evidence conflicts fail
closed so Railway surfaces the audit problem instead of silently continuing.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from sports_edge_tennis.live.batch import main as live_batch_main
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
    """Apply migration 0010 once, then verify its sentinel column and constraints.

    Production Railway pre-deploy runs before the built Python environment is available,
    so this narrow bootstrap executes inside the installed runtime instead. The committed
    migration is transactional. Existing historical decisions remain untouched because
    all new staking columns are nullable and the migration only adds columns/constraints.
    """
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


def main(argv: list[str] | None = None) -> int:
    dsn = _db_dsn()
    if not dsn:
        _log("SCHEMA_BLOCKED reason=NO_DATABASE")
        return 2

    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            schema_status = _ensure_quarter_kelly_schema(conn)
    except (psycopg.Error, OSError, RuntimeError) as exc:
        _log(f"SCHEMA_BOOTSTRAP_FAILED error_type={type(exc).__name__}")
        return 2

    _log(f"SCHEMA_QUARTER_KELLY status={schema_status} migration=0010")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
