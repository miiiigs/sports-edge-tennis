from __future__ import annotations

"""Controlled live-Postgres test for the OddsPapi request ledger.

Applies migration 0015, verifies the schema, then exercises the atomic budget
reservation against real Postgres. All provider-binding rows use a synthetic
future billing month and are removed afterwards; DIAGNOSTIC rows are excluded
from the monthly count entirely. No provider calls are made.
"""

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from sports_edge_tennis.live.oddspapi_ledger import (
    OddspapiBudgetExceeded,
    OddspapiLedgerConfig,
    billing_month,
    month_to_date_count,
    reserve_attempt,
)

MIGRATION = Path("db/migrations/0015_oddspapi_request_ledger_v1.up.sql")
SYNTH_COUNT_MONTH = "2099-01"
SYNTH_DIAG_MONTH = "2099-02"


def _now(month: str) -> datetime:
    return datetime(int(month[:4]), int(month[5:7]), 15, 12, tzinfo=UTC)


def _ensure_schema(conn) -> str:
    conn.execute(MIGRATION.read_text(encoding="utf-8"))
    row = conn.execute(
        """
        SELECT to_regclass('public.oddspapi_request_ledger') IS NOT NULL,
               to_regclass('public.oddspapi_event_identity_cache') IS NOT NULL
        """
    ).fetchone()
    return "READY" if row and row[0] and row[1] else "INCOMPLETE"


def _cleanup(dsn: str) -> int:
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(
            "DELETE FROM oddspapi_request_ledger WHERE billing_month = ANY(%s)",
            ([SYNTH_COUNT_MONTH, SYNTH_DIAG_MONTH],),
        )
        return cur.rowcount or 0


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("LEDGER_DB_TEST_BLOCKED missing_dsn")
        return 2

    with psycopg.connect(dsn, autocommit=True) as conn:
        schema = _ensure_schema(conn)
    removed = _cleanup(dsn)

    config = OddspapiLedgerConfig(monthly_limit=5, reserve=0)

    # Seed 4 countable (SHADOW) rows for the synthetic month -> one slot left.
    with psycopg.connect(dsn, autocommit=True) as conn:
        for _ in range(4):
            reserve_attempt(
                conn,
                config=OddspapiLedgerConfig(monthly_limit=1000, reserve=0),
                endpoint="synthetic",
                request_purpose="DIAGNOSTIC",
                classification="SHADOW",
                now=_now(SYNTH_COUNT_MONTH),
            )
    with psycopg.connect(dsn, autocommit=True) as conn:
        used = month_to_date_count(conn, month=SYNTH_COUNT_MONTH)

    # Two concurrent reservations compete for the final slot on real Postgres.
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            with psycopg.connect(dsn, autocommit=False) as c:
                barrier.wait(timeout=10)
                reserve_attempt(
                    c,
                    config=config,
                    endpoint="synthetic",
                    request_purpose="QUALIFICATION",
                    classification="SHADOW",
                    now=_now(SYNTH_COUNT_MONTH),
                    correlation_id="concurrency-test",
                )
                c.commit()
            with lock:
                results.append("RESERVED")
        except OddspapiBudgetExceeded:
            with lock:
                results.append("REFUSED")
        except Exception as exc:  # pragma: no cover - defensive
            with lock:
                results.append(f"ERROR:{type(exc).__name__}")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    successes = results.count("RESERVED")
    refusals = results.count("REFUSED")

    # DIAGNOSTIC rows must not consume allowance.
    with psycopg.connect(dsn, autocommit=True) as conn:
        for _ in range(3):
            reserve_attempt(
                conn,
                config=OddspapiLedgerConfig(monthly_limit=1000, reserve=0),
                endpoint="synthetic",
                request_purpose="DIAGNOSTIC",
                classification="DIAGNOSTIC",
                now=_now(SYNTH_DIAG_MONTH),
            )
        diag_counted = month_to_date_count(conn, month=SYNTH_DIAG_MONTH)

    cleaned = _cleanup(dsn)

    print(
        "ODDSPAPI_LEDGER_DB_TEST "
        + json.dumps(
            {
                "schema_status": schema,
                "synthetic_rows_removed_before": removed,
                "seeded_countable_rows": used,
                "concurrency_successes": successes,
                "concurrency_refusals": refusals,
                "diagnostic_rows_counted_toward_budget": diag_counted,
                "synthetic_rows_cleaned_after": cleaned,
                "provider_calls": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
