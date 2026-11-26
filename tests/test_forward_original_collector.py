"""No external receipts or production network calls."""
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from sports_edge_tennis.validation.forward_original_collector import OriginalReceiptStore, handler_for
from sports_edge_tennis.validation.forward_receipt_store import ReceiptConflict


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _payload():
    now = datetime.now(UTC)
    return json.dumps({"schema":"forward_original_v1","experiment_version":"A-v1",
        "canonical_match_id":42,"first_prediction_run_id":11,
        "tour":"ATP_MAIN","not_a_published_model_pick":True,
        "original_issued_at_utc":now.isoformat(),
        "scheduled_start_at_utc":(now+timedelta(hours=1)).isoformat(),
        "feature_snapshot_json":"{}","feature_sha256":_sha(b"{}"),
        "freeze_json":"{}","freeze_sha256":_sha(b"{}")}).encode()


def test_exact_byte_receipt_restart_conflict_and_integrity(tmp_path):
    path = tmp_path / "receipt.sqlite"
    payload = _payload()
    store = OriginalReceiptStore(path)
    with pytest.raises(ValueError, match="digest"):
        store.receive(payload, "bad")
    first = store.receive(payload, _sha(payload))
    retry = OriginalReceiptStore(path).receive(payload, _sha(payload))
    assert retry == {**first, "duplicate_delivery":True}
    changed = payload+b" "
    with pytest.raises(ReceiptConflict):
        store.receive(changed, _sha(changed))
    assert store.verify_inventory() == {"count":1,"digest_mismatches":0}
    assert not first["eligible_for_activation_evidence"]


def test_http_auth_digest_and_retry(tmp_path):
    store = OriginalReceiptStore(tmp_path / "receipt.sqlite")
    token = "test-only-secret-"+"x"*32
    with ThreadingHTTPServer(("127.0.0.1",0), handler_for(store,token)) as server:
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        payload = _payload()
        def post(secret, digest, data=payload):
            conn = HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            try:
                conn.request("POST","/v1/original-receipts",body=data,headers={
                    "Authorization":"Bearer "+secret,"X-Payload-SHA256":digest})
                reply = conn.getresponse()
                return reply.status,json.loads(reply.read())
            finally:
                conn.close()
        try:
            assert post("bad",_sha(payload))[0] == 401
            assert post(token,"bad")[0] == 400
            status, first = post(token,_sha(payload))
            assert status == 200
            assert post(token,_sha(payload))[1] == {**first,"duplicate_delivery":True}
            assert post(token,_sha(payload+b" "),payload+b" ")[0] == 409
        finally:
            server.shutdown()
            thread.join(timeout=5)
