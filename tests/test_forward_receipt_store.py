"""Isolated collector pilot; no production database or provider calls."""
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sports_edge_tennis.validation import forward_receipt_store as collector


def _payload():
    path = (
        Path(__file__).resolve().parents[1]
        / "docs/research/forward_captures/railway_forward_20261124_full_day.json"
    )
    export = json.loads(path.read_text(encoding="utf-8"))
    return next(
        row["message"].split(" ", 1)[1].encode()
        for row in export["logs"]
        if row["message"].startswith("TENNIS_FROZEN_PROSPECTIVE_CANDIDATE ")
    )


def test_restart_retry_preserves_original_receipt(tmp_path, monkeypatch):
    payload = _payload()
    record = json.loads(payload)
    capture = datetime.fromisoformat(record["captured_at_utc"])
    monkeypatch.setattr(collector, "_now", lambda: capture)
    path = tmp_path / "collector.sqlite"
    first = collector.ReceiptStore(path).receive(payload)
    monkeypatch.setattr(collector, "_now", lambda: datetime(2027, 1, 1, tzinfo=UTC))
    retry = collector.ReceiptStore(path).receive(payload)
    assert retry == {**first, "duplicate_delivery": True}
    assert first["timing_status"] == "TIMELY_RECEIPT_CLOCK_ONLY"
    assert first["eligible_for_activation_evidence"] is False
    assert first["independent_clock_verified"] is False


def test_late_historical_export_never_counts_as_timely(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "_now", lambda: datetime(2027, 1, 1, tzinfo=UTC))
    receipt = collector.ReceiptStore(tmp_path / "collector.sqlite").receive(_payload())
    assert receipt["timing_status"] == "LATE_RECEIPT"
    assert receipt["eligible_for_activation_evidence"] is False


def test_conflicting_retry_and_rescheduled_payload_rejected(tmp_path, monkeypatch):
    payload = _payload()
    monkeypatch.setattr(collector, "_now", lambda: datetime(2027, 1, 1, tzinfo=UTC))
    store = collector.ReceiptStore(tmp_path / "collector.sqlite")
    first = store.receive(payload)
    changed = json.loads(payload)
    changed["scheduled_start_at_utc"] = "2027-01-02T00:00:00+00:00"
    with pytest.raises(collector.ReceiptConflict):
        store.receive(json.dumps(changed).encode())
    assert store.receive(payload)["payload_sha256"] == first["payload_sha256"]


def test_append_only_guards(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "_now", lambda: datetime(2027, 1, 1, tzinfo=UTC))
    path = tmp_path / "collector.sqlite"
    collector.ReceiptStore(path).receive(_payload())
    with sqlite3.connect(path) as db:
        for query in ("UPDATE forward_receipts SET timing_status='fake'", "DELETE FROM forward_receipts"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(query)
        assert db.execute("SELECT count(*) FROM forward_receipts").fetchone()[0] == 1


def test_failed_insert_can_retry_without_partial_record(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "_now", lambda: datetime(2027, 1, 1, tzinfo=UTC))
    path = tmp_path / "collector.sqlite"
    store = collector.ReceiptStore(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON forward_receipts "
                   "BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        store.receive(_payload())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM forward_receipts").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_insert")
    assert store.receive(_payload())["duplicate_delivery"] is False


def test_future_producer_clock_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "_now", lambda: datetime(2020, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="clock precedes"):
        collector.ReceiptStore(tmp_path / "collector.sqlite").receive(_payload())
