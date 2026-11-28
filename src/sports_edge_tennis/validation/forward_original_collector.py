"""Authenticated, separately operable local receipt service; not a TSA or WORM.

Run behind authenticated TLS termination on loopback. A separate operator,
verified clock, retention and recovery controls are external prerequisites.
No receipt from this candidate is automatically independent evidence.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sports_edge_tennis.validation.forward_receipt_store import (
    ReceiptConflict, _instant, _positive_id,
)

LIMIT = 1048576
SCHEMA = """
CREATE TABLE IF NOT EXISTS original_receipts (
    experiment_version TEXT NOT NULL, match_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL, payload BLOB NOT NULL, sha256 TEXT NOT NULL,
    received_at TEXT NOT NULL, timing TEXT NOT NULL,
    PRIMARY KEY(experiment_version,match_id)
);
CREATE TRIGGER IF NOT EXISTS original_receipts_no_update
BEFORE UPDATE ON original_receipts BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS original_receipts_no_delete
BEFORE DELETE ON original_receipts BEGIN SELECT RAISE(ABORT,'append-only'); END;
"""


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite JSON value: " + value)


def _decode(payload):
    if not isinstance(payload, bytes) or not 0 < len(payload) <= LIMIT:
        raise ValueError("invalid payload length")
    row = json.loads(payload, object_pairs_hook=_unique, parse_constant=_reject_constant)
    if not isinstance(row, dict) or row.get("schema") != "forward_original_v1":
        raise ValueError("unknown envelope")
    version = row["experiment_version"]
    if not isinstance(version, str) or not version.strip() or len(version) > 256:
        raise ValueError("invalid experiment")
    match = _positive_id(row["canonical_match_id"])
    run = _positive_id(row["first_prediction_run_id"])
    if row["tour"] not in ("ATP_MAIN", "CHALLENGER") or row["not_a_published_model_pick"] is not True:
        raise ValueError("outside research scope")
    for data, checksum in (("feature_snapshot_json", "feature_sha256"), ("freeze_json", "freeze_sha256")):
        if _sha(row[data].encode()) != row[checksum]:
            raise ValueError("embedded digest mismatch")
    issued = _instant(row["original_issued_at_utc"])
    deadline = _instant(row["scheduled_start_at_utc"]) - timedelta(minutes=30)
    if issued > deadline:
        raise ValueError("late producer issuance")
    return version, match, run, issued, deadline


class OriginalReceiptStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("absolute durable path required")
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA recursive_triggers=ON")
        except Exception:
            db.close()
            raise
        return db

    def receive(self, payload: bytes, supplied_sha: str):
        received = datetime.now(UTC)
        sha = _sha(payload)
        if not hmac.compare_digest(sha, supplied_sha):
            raise ValueError("transport digest mismatch")
        version, match, run, issued, deadline = _decode(payload)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                """SELECT run_id,payload,sha256,received_at,timing FROM original_receipts
                   WHERE experiment_version=? AND match_id=?""", (version, match),
            ).fetchone()
            if prior is not None:
                if prior[:3] != (run, payload, sha):
                    raise ReceiptConflict("conflicting original payload")
                timestamp, timing = prior[3:]
            else:
                if received < issued:
                    raise ValueError("collector clock before issuance")
                timestamp = received.isoformat()
                timing = "TIMELY_LOCAL_CLOCK_ONLY" if received <= deadline else "LATE_RECEIPT"
                db.execute("INSERT INTO original_receipts VALUES (?,?,?,?,?,?,?)",
                           (version, match, run, payload, sha, timestamp, timing))
        return {"experiment_version": version, "canonical_match_id": match,
                "first_prediction_run_id": run, "payload_sha256": sha,
                "collector_received_at_utc": timestamp, "timing_status": timing,
                "duplicate_delivery": prior is not None,
                "independent_clock_verified": False, "eligible_for_activation_evidence": False}

    def verify_inventory(self):
        """Read-only integrity inventory; not an external timestamp proof."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT payload,sha256 FROM original_receipts").fetchall()
        return {"count": len(rows), "digest_mismatches": sum(_sha(p) != s for p, s in rows)}


def handler_for(store, token: str):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii():
        raise ValueError("collector requires an independent high-entropy secret")
    expected = ("Bearer " + token).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # Avoid logging authorization or request data.
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def _reply(self, status, body):
            data = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True

        def do_POST(self):
            if self.path != "/v1/original-receipts":
                return self._reply(404, {"error": "not_found"})
            actual = self.headers.get("Authorization", "").encode("utf-8")
            if not hmac.compare_digest(actual, expected):
                return self._reply(401, {"error": "unauthorized"})
            lengths = self.headers.get_all("Content-Length") or []
            try:
                if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("ambiguous length")
                size = int(lengths[0])
                if not 0 < size <= LIMIT:
                    raise ValueError("size")
                payload = self.rfile.read(size)
                if len(payload) != size:
                    raise ValueError("short body")
                receipt = store.receive(payload, self.headers.get("X-Payload-SHA256", ""))
            except ReceiptConflict:
                return self._reply(409, {"error": "conflicting_original"})
            except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
                return self._reply(400, {"error": "invalid_payload"})
            except (sqlite3.Error, OSError):
                return self._reply(503, {"error": "storage_or_transport_unavailable"})
            return self._reply(200, receipt)

    return Handler


def main():
    path = Path(os.environ["FORWARD_COLLECTOR_DB"])
    token = os.environ["FORWARD_COLLECTOR_TOKEN"]
    port = int(os.environ.get("FORWARD_COLLECTOR_PORT", "8765"))
    with ThreadingHTTPServer(("127.0.0.1", port), handler_for(OriginalReceiptStore(path), token)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
