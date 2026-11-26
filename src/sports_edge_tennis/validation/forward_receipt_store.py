"""Durable collector-side receipts for the existing frozen V2 shadow records.

This is a separate SQLite pilot, not a production outbox, trusted timestamp
authority, WORM store, verified first-issue ledger or publication system.
Deploy under a separate operator with a durable volume and authenticated
transport before treating its clock as independent evidence.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sports_edge_tennis.validation.prospective_manager_shadow_capture import VERSION


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_receipts (
    version TEXT NOT NULL,
    match_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    payload BLOB NOT NULL,
    sha256 TEXT NOT NULL,
    received_at TEXT NOT NULL,
    timing_status TEXT NOT NULL,
    PRIMARY KEY (version, match_id)
);
CREATE TRIGGER IF NOT EXISTS forward_receipts_no_update
BEFORE UPDATE ON forward_receipts
BEGIN SELECT RAISE(ABORT, 'append-only receipt'); END;
CREATE TRIGGER IF NOT EXISTS forward_receipts_no_delete
BEFORE DELETE ON forward_receipts
BEGIN SELECT RAISE(ABORT, 'append-only receipt'); END;
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("naive evidence timestamp")
    return dt.astimezone(UTC)


def _positive_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("invalid original identifier")
    return value


def _decode(payload: bytes) -> dict:
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("nonfinite JSON number: " + value)

    if not isinstance(payload, bytes) or not 0 < len(payload) <= 1024 * 1024:
        raise ValueError("invalid receipt payload size")
    record = json.loads(payload, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    if not isinstance(record, dict) or record.get("version") != VERSION:
        raise ValueError("unsupported research record")
    _positive_id(record["canonical_match_id"])
    _positive_id(record["first_prediction_run_id"])
    if (
        record.get("tour") not in ("ATP_MAIN", "CHALLENGER")
        or record.get("outcomes_available_during_capture") is not False
        or record.get("not_a_published_model_pick") is not True
    ):
        raise ValueError("invalid research scope or capture status")
    issued, captured, start = (
        _instant(record[k])
        for k in ("original_issued_at_utc", "captured_at_utc", "scheduled_start_at_utc")
    )
    if not issued <= captured <= start - timedelta(minutes=30):
        raise ValueError("invalid producer chronology")
    return record


class ReceiptConflict(ValueError):
    """The existing authoritative payload differs; do not overwrite it."""


class ReceiptStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("collector requires explicit absolute storage path")
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self):
        from contextlib import closing, contextmanager

        @contextmanager
        def connect():
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                with conn:
                    yield conn

        return connect()

    def receive(self, payload: bytes) -> dict:
        # Only the collector assigns receipt time. Never use a caller's clock
        # or an original producer/log timestamp as a receipt time.
        received = _now()
        record = _decode(payload)
        version = record["version"]
        match = record["canonical_match_id"]
        run = record["first_prediction_run_id"]
        digest = hashlib.sha256(payload).hexdigest()
        captured = _instant(record["captured_at_utc"])
        deadline = _instant(record["scheduled_start_at_utc"]) - timedelta(minutes=30)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT run_id, payload, sha256, received_at, timing_status "
                "FROM forward_receipts WHERE version=? AND match_id=?",
                (version, match),
            ).fetchone()
            if row:
                if row[0] != run or row[1] != payload or row[2] != digest:
                    raise ReceiptConflict("conflicting first-issue payload")
                receipt_at, timing = row[3], row[4]
                duplicate = True
            else:
                if received < captured:
                    raise ValueError("collector clock precedes producer capture")
                timing = "TIMELY_RECEIPT_CLOCK_ONLY" if received <= deadline else "LATE_RECEIPT"
                receipt_at = received.isoformat()
                conn.execute(
                    "INSERT INTO forward_receipts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (version, match, run, payload, digest, receipt_at, timing),
                )
                duplicate = False
        # Acknowledge only after the database context commits successfully.
        return {
            "version": version,
            "canonical_match_id": match,
            "first_prediction_run_id": run,
            "payload_sha256": digest,
            "collector_received_at_utc": receipt_at,
            "timing_status": timing,
            "duplicate_delivery": duplicate,
            "independent_clock_verified": False,
            "first_issue_verified": False,
            "feature_lineage_verified": False,
            "official_scope_verified": False,
            "eligible_for_activation_evidence": False,
        }
