"""Production wrapper: run the existing live horizon, then settle matured Type A BETs.

Keeping this wrapper additive avoids changing the already-stable live orchestration path.
A non-zero live-batch result prevents settlement. Settlement evidence conflicts fail the
wrapper closed so Railway surfaces the audit problem instead of silently continuing.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg

from sports_edge_tennis.live.batch import main as live_batch_main
from sports_edge_tennis.settlement import (
    SettlementEvidenceConflict,
    settle_type_a_match_winner_decisions,
)


def _db_dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _log(message: str) -> None:
    print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    live_status = live_batch_main(argv)
    if live_status != 0:
        _log(f"SETTLEMENT_SKIPPED live_batch_status={live_status}")
        return live_status

    dsn = _db_dsn()
    if not dsn:
        _log("SETTLEMENT_BLOCKED reason=NO_DATABASE")
        return 2

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
