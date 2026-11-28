"""Actual local HTTP transport; synthetic fixtures, no independent receipts."""
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer

import pytest

from sports_edge_tennis.validation.forward_archive_dispatch import dispatch_archive
from sports_edge_tennis.validation.forward_archive_http import (
    ArchiveHTTPTransport, ArchiveTransportError,
)
from sports_edge_tennis.validation.forward_evidence_outbox import _freeze, _original, digest
from sports_edge_tennis.validation.forward_original_collector import OriginalReceiptStore, handler_for
from tests.test_forward_evidence_outbox import _seed, _freeze_bytes

TOKEN = "synthetic-only-" + "x" * 32


@pytest.fixture
def collector(tmp_path):
    store = OriginalReceiptStore(tmp_path / "receipts.sqlite")
    with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(store, TOKEN)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/original-receipts"
        try:
            yield store, endpoint
        finally:
            server.shutdown()
            thread.join(timeout=5)


def envelope(match=42, run=11):
    issued = datetime.now(UTC)
    body = json.dumps({
        "schema": "forward_original_v1", "experiment_version": "A-v1",
        "canonical_match_id": match, "first_prediction_run_id": run,
        "tour": "ATP_MAIN", "not_a_published_model_pick": True,
        "original_issued_at_utc": issued.isoformat(),
        "scheduled_start_at_utc": (issued + timedelta(hours=2)).isoformat(),
        "feature_snapshot_json": "{}", "feature_sha256": digest(b"{}"),
        "freeze_json": "{}", "freeze_sha256": digest(b"{}"),
    }).encode()
    return issued, body


def test_transport_auth_exact_bytes_and_conflict(collector):
    store, endpoint = collector
    send = ArchiveHTTPTransport(endpoint, TOKEN)
    _, body = envelope()
    with pytest.raises(ArchiveTransportError, match="401"):
        ArchiveHTTPTransport(endpoint, "y" * 32)(body, digest(body))
    with pytest.raises(ValueError, match="digest"):
        send(body, "0" * 64)
    first = send(body, digest(body))
    assert not first["eligible_for_activation_evidence"]
    assert send(body, digest(body)) == {**first, "duplicate_delivery": True}
    with pytest.raises(ArchiveTransportError, match="409"):
        send(body + b" ", digest(body + b" "))
    assert store.verify_inventory() == {"count": 1, "digest_mismatches": 0}


@pytest.mark.parametrize("endpoint", [
    "http://example.com/v1/original-receipts",
    "http://localhost/v1/original-receipts",
    "https://user:pass@example.com/v1/original-receipts",
    "https://example.com/v1/original-receipts?token=bad",
    "https://example.com/other",
])
def test_transport_rejects_unsafe_endpoints(endpoint):
    with pytest.raises(ValueError):
        ArchiveHTTPTransport(endpoint, TOKEN)


def test_postgres_outbox_real_transport_lost_ack_and_recovery(db, collector):
    store, endpoint = collector
    match, run = _seed(db)
    issued, body = envelope(match, run)
    with db.transaction():
        _freeze(db, "A-v1", _freeze_bytes())
        _original(db, "A-v1", match, run, body, issued)
    send = ArchiveHTTPTransport(endpoint, TOKEN)
    with pytest.raises(ArchiveTransportError, match="401"):
        dispatch_archive(db, "A-v1", match, ArchiveHTTPTransport(endpoint, "z" * 32))
    assert store.verify_inventory()["count"] == 0
    saved = []
    def lose_ack(payload, sha):
        saved.append(send(payload, sha))
        raise TimeoutError("synthetic lost acknowledgement after real durable write")
    with pytest.raises(TimeoutError):
        dispatch_archive(db, "A-v1", match, lose_ack)
    assert store.verify_inventory() == {"count": 1, "digest_mismatches": 0}
    recovered = dispatch_archive(db, "A-v1", match, send)
    assert recovered == {k: v for k, v in saved[0].items() if k != "duplicate_delivery"}
    assert dispatch_archive(db, "A-v1", match, send) is None
    assert store.verify_inventory()["count"] == 1
    assert not recovered["independent_clock_verified"]
