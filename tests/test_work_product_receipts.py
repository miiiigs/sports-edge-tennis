"""Work-product receipt publisher: fail-closed logic and PostgreSQL SQL."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.ops.work_product_receipts import (
    STATUS_EMPTY_ACKNOWLEDGED,
    STATUS_PARTIAL,
    STATUS_UNVERIFIED,
    ReceiptScope,
    WorkProductReceiptPublisher,
)

SLOT = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
WINDOW_START = datetime(2026, 8, 23, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConnection:
    autocommit = True

    def __init__(self, *, discovery, forecast, bookmaker, archive,
                 settlement, bookmaker_settlement=(0, 0), candidate=(1, 1)):
        self._responses = {
            "ingest_runs ir": discovery,
            "WITH run AS": forecast,
            "decision_odds_snapshots": bookmaker,
            "forward_delivery_events": archive,
            "WITH bet AS": settlement,
            "forward_settlement_events": bookmaker_settlement,
            "forward_fixture_expectations": candidate,
        }
        self.seen: list[str] = []

    def execute(self, sql, params=None):
        self.seen.append(sql)
        if "to_regclass" in sql:
            return _Cursor(("public.table",))
        for marker, row in self._responses.items():
            if marker in sql:
                return _Cursor(row)
        raise AssertionError(f"unexpected SQL: {sql[:120]}")


def _scope(**overrides):
    base = dict(
        slot=SLOT,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        prediction_run_id=7,
        experiment_id="EXP-1",
        cutoff=WINDOW_END,
    )
    base.update(overrides)
    return ReceiptScope(**base)


def _publisher(conn):
    return WorkProductReceiptPublisher(conn, clock=lambda: WINDOW_END)


def _healthy():
    finalized = WINDOW_END - timedelta(hours=2)
    return _FakeConnection(
        discovery=(1, 1, 0, 1, 0),
        forecast=(1, 1, "COMPLETED", finalized, None),
        bookmaker=(1, 1),
        archive=(1, 1),
        settlement=(1, 1, 0),
        candidate=(1, 1),
    )


def test_all_seven_stages_verified_produces_a_successful_receipt():
    collected = _publisher(_healthy()).collect(_scope())
    assert collected["success"] is True
    assert collected["missing_stages"] == ()
    checks = {check["stage"]: check for check in collected["checks"]}
    assert len(checks) == 7
    assert all(check["durable"] for check in checks.values())
    assert collected["receipt"].record.eligible_fixture_count == 1
    assert collected["receipt"].record.reconciliation_completed is True


def test_partial_forecast_never_produces_success():
    healthy = _healthy()
    healthy._responses["WITH run AS"] = (
        2,
        1,
        "COMPLETED",
        WINDOW_END - timedelta(hours=2),
        None,
    )
    collected = _publisher(healthy).collect(_scope())
    assert collected["success"] is False
    assert "FORECAST_GENERATION" in collected["missing_stages"]
    checks = {check["stage"]: check for check in collected["checks"]}
    assert checks["FORECAST_GENERATION"]["status"] == STATUS_PARTIAL


def test_partial_discovery_never_produces_success():
    healthy = _healthy()
    healthy._responses["ingest_runs ir"] = (5, 3, 1, 1, 2)
    collected = _publisher(healthy).collect(_scope())
    assert collected["success"] is False
    assert "FIXTURE_DISCOVERY" in collected["missing_stages"]


def test_zero_discovery_requires_an_explicit_acknowledgement():
    zero = _FakeConnection(
        discovery=(0, 0, 0, 1, 0),
        forecast=(0, 0, "COMPLETED", WINDOW_END - timedelta(hours=1), None),
        bookmaker=(0, 0),
        archive=(0, 0),
        settlement=(0, 0, 0),
        candidate=(0, 0),
    )
    refused = _publisher(zero).collect(_scope())
    assert refused["success"] is False
    assert "FIXTURE_DISCOVERY" in refused["missing_stages"]

    acknowledged = _publisher(zero).collect(
        _scope(acknowledge_empty_discovery=True)
    )
    assert acknowledged["success"] is True
    checks = {check["stage"]: check for check in acknowledged["checks"]}
    assert checks["FIXTURE_DISCOVERY"]["status"] == STATUS_EMPTY_ACKNOWLEDGED
    assert checks["FORECAST_GENERATION"]["status"] == STATUS_EMPTY_ACKNOWLEDGED
    assert acknowledged["receipt"].record.eligible_fixture_count == 0


def test_archive_acknowledgement_mismatch_is_partial():
    healthy = _healthy()
    healthy._responses["forward_delivery_events"] = (2, 1)
    collected = _publisher(healthy).collect(_scope())
    assert "INDEPENDENT_ARCHIVAL" in collected["missing_stages"]
    checks = {check["stage"]: check for check in collected["checks"]}
    assert checks["INDEPENDENT_ARCHIVAL"]["status"] == STATUS_PARTIAL


def test_stale_official_result_is_partial():
    healthy = _healthy()
    healthy._responses["WITH bet AS"] = (1, 1, 1)
    collected = _publisher(healthy).collect(_scope())
    assert "OFFICIAL_SETTLEMENT" in collected["missing_stages"]
    checks = {check["stage"]: check for check in collected["checks"]}
    assert checks["OFFICIAL_SETTLEMENT"]["detail"] == (
        "SETTLEMENT_BOUND_TO_STALE_RESULT_OBSERVATION"
    )


def test_no_approved_bookmaker_rules_is_acknowledged_empty_not_inferred():
    healthy = _healthy()
    collected = _publisher(healthy).collect(_scope())
    checks = {check["stage"]: check for check in collected["checks"]}
    check = checks["BOOKMAKER_SETTLEMENT"]
    assert check["status"] == STATUS_EMPTY_ACKNOWLEDGED
    assert check["acknowledges_empty"] is True
    assert "NO_APPROVED_BOOKMAKER_RULES" in check["verification"]


def test_approved_rules_without_a_graded_ledger_is_partial():
    healthy = _healthy()
    healthy._responses["forward_settlement_events"] = (1, 0)
    collected = _publisher(healthy).collect(
        _scope(approved_bookmaker_rules={("book-a", "rule-v1"): "ref-1"})
    )
    assert "BOOKMAKER_SETTLEMENT" in collected["missing_stages"]


def test_missing_forward_tables_are_unverified_not_zero():
    class NoTables(_FakeConnection):
        def execute(self, sql, params=None):
            if "to_regclass" in sql:
                return _Cursor((None,))
            return super().execute(sql, params)

    conn = NoTables(
        discovery=(1, 1, 0, 1, 0),
        forecast=(1, 1, "COMPLETED", WINDOW_END - timedelta(hours=2), None),
        bookmaker=(1, 1),
        archive=(0, 0),
        settlement=(1, 1, 0),
    )
    collected = _publisher(conn).collect(_scope())
    checks = {check["stage"]: check for check in collected["checks"]}
    assert checks["INDEPENDENT_ARCHIVAL"]["status"] == STATUS_UNVERIFIED
    assert checks["PROSPECTIVE_CANDIDATE_EXECUTION"]["status"] == STATUS_UNVERIFIED
    assert collected["success"] is False


def test_scope_rejects_naive_time_and_out_of_window_slot():
    with pytest.raises(ValueError):
        ReceiptScope(
            slot=datetime(2026, 8, 26),
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )
    with pytest.raises(ValueError):
        ReceiptScope(
            slot=datetime(2026, 8, 30, tzinfo=UTC),
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )


def test_publish_writes_the_monitoring_receipt(tmp_path):
    path = tmp_path / "receipt.json"
    result = _publisher(_healthy()).publish(_scope(), receipt_path=str(path))
    assert result["success"] is True
    assert path.exists()
    payload = path.read_text(encoding="utf-8")
    assert "TENNIS_MONITORING_RECEIPT_V1" in payload
    assert "ALL_REQUIRED" not in payload  # the receipt is evidence, not an assessment
    assert result["receipt_sha256"]
