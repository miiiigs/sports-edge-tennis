from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sports_edge_tennis.live.oddspapi_ledger import (
    OddspapiBudgetExceeded,
    OddspapiLedgerBinding,
    OddspapiLedgerConfig,
    billing_month,
    month_window,
    projected_monthly,
    record_outcome,
    remaining_allowance,
    reserve_attempt,
    would_exceed_monthly,
)


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _LedgerConn:
    def __init__(self, existing: int = 0):
        self.statements: list[tuple[str, tuple]] = []
        self.existing = existing
        self.inserts = 0
        self.updates = 0

    def transaction(self):
        import contextlib

        return contextlib.nullcontext()

    def execute(self, sql, params=None):
        self.statements.append((str(sql), tuple(params or ())))
        normalized = " ".join(str(sql).split()).lower()
        if normalized.startswith("select pg_advisory_xact_lock"):
            return _Result((None,))
        if normalized.startswith("select count(*) from oddspapi_request_ledger"):
            return _Result((self.existing,))
        if normalized.startswith("insert into oddspapi_request_ledger"):
            self.inserts += 1
            self.existing += 1
            return _Result((1000 + self.inserts,))
        if normalized.startswith("update oddspapi_request_ledger"):
            self.updates += 1
            return _Result((None,))
        return _Result((None,))


def test_billing_month_rolls_over() -> None:
    assert billing_month(datetime(2026, 9, 30, 23, 59, tzinfo=UTC)) == "2026-09"
    assert billing_month(datetime(2026, 10, 1, 0, 1, tzinfo=UTC)) == "2026-10"
    start, end = month_window(datetime(2026, 9, 15, tzinfo=UTC))
    assert start == datetime(2026, 9, 1, tzinfo=UTC)
    assert end == datetime(2026, 10, 1, tzinfo=UTC)


def test_reserve_attempt_records_provider_call() -> None:
    conn = _LedgerConn(existing=0)
    config = OddspapiLedgerConfig()
    ledger_id = reserve_attempt(
        conn,
        config=config,
        endpoint="odds-by-tournaments",
        request_purpose="QUALIFICATION",
        classification="PRODUCTION",
        now=datetime(2026, 9, 23, 12, tzinfo=UTC),
        bookmaker="pinnacle+30",
        tournament_chunk_size=5,
        correlation_id="corr-1",
    )
    assert ledger_id == 1001
    assert conn.inserts == 1
    joined = " ".join(sql for sql, _ in conn.statements).lower()
    assert "pg_advisory_xact_lock" in joined
    assert "insert into oddspapi_request_ledger" in joined


def test_final_available_request_allowed_then_one_more_blocked() -> None:
    config = OddspapiLedgerConfig(monthly_limit=5000, reserve=500)
    assert config.usable_allowance == 4500
    assert would_exceed_monthly(used=4499, config=config) is False
    assert would_exceed_monthly(used=4500, config=config) is True
    assert remaining_allowance(used=4499, config=config) == 1
    conn = _LedgerConn(existing=4500)
    with pytest.raises(OddspapiBudgetExceeded, match="monthly_budget_exceeded"):
        reserve_attempt(
            conn,
            config=config,
            endpoint="fixtures",
            request_purpose="DISCOVERY",
            classification="PRODUCTION",
            now=datetime(2026, 9, 23, 12, tzinfo=UTC),
        )
    assert conn.inserts == 0


def test_restart_does_not_reset_usage() -> None:
    # A fresh process reads the durable month-to-date count from the ledger.
    config = OddspapiLedgerConfig()
    conn = _LedgerConn(existing=1200)
    ledger_id = reserve_attempt(
        conn,
        config=config,
        endpoint="fixtures",
        request_purpose="DISCOVERY",
        classification="PRODUCTION",
        now=datetime(2026, 9, 23, 12, tzinfo=UTC),
    )
    assert isinstance(ledger_id, int)
    assert conn.existing == 1201


def test_each_retry_reserves_another_row() -> None:
    conn = _LedgerConn(existing=0)
    binding = OddspapiLedgerBinding(
        conn=conn,
        config=OddspapiLedgerConfig(),
        request_purpose="QUALIFICATION",
        classification="PRODUCTION",
        correlation_id="corr",
    )
    first = binding.reserve(endpoint="odds-by-tournaments", retry_sequence=0)
    second = binding.reserve(endpoint="odds-by-tournaments", retry_sequence=1)
    assert (first, second) == (1001, 1002)
    assert conn.inserts == 2


def test_record_outcome_marks_success_and_failure() -> None:
    conn = _LedgerConn(existing=0)
    record_outcome(conn, ledger_id=1, success=True, http_status=200)
    record_outcome(conn, ledger_id=2, success=False, http_status=429, error_kind="HTTP_429", retry_sequence=1)
    assert conn.updates == 2


def test_projection() -> None:
    assert projected_monthly(requests_per_cycle=5, cycles_per_month=744) == 3720
    assert projected_monthly(requests_per_cycle=5, cycles_per_month=720) == 3600
