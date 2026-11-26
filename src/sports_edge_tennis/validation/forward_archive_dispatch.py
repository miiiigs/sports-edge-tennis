"""Transport-independent archive dispatch, with explicit local-only qualification."""
from __future__ import annotations

import json

from sports_edge_tennis.validation.forward_evidence_outbox import (
    EvidenceConflict, _append, _lock_and_read, claim_delivery,
)
from sports_edge_tennis.validation.forward_receipt_store import _instant


def dispatch_archive(conn, version, match_id, submit):
    """submit(bytes, sha256) is an authenticated transport supplied by the caller.
    Any exception or invalid acknowledgement leaves the delivery uncertain.
    The local collector cannot independently qualify a receipt.
    """
    claimed = claim_delivery(conn, version, match_id, "ARCHIVE")
    if claimed is None:
        return None
    payload, sha = claimed
    original = json.loads(payload)
    receipt = submit(payload, sha)
    if (not isinstance(receipt, dict)
            or receipt.get("experiment_version") != version
            or receipt.get("canonical_match_id") != match_id
            or receipt.get("first_prediction_run_id") != original["first_prediction_run_id"]
            or receipt.get("payload_sha256") != sha
            or receipt.get("independent_clock_verified") is not False
            or receipt.get("eligible_for_activation_evidence") is not False):
        raise EvidenceConflict("unverified or mismatched collector acknowledgement")
    if _instant(receipt["collector_received_at_utc"]) < _instant(original["original_issued_at_utc"]):
        raise EvidenceConflict("receipt predates original issuance")
    evidence = dict(receipt)
    evidence.pop("duplicate_delivery",None)
    serialized = json.dumps(evidence,sort_keys=True,separators=(",",":"))
    with conn.transaction():
        _, _, state = _lock_and_read(conn,version,match_id,"ARCHIVE")
        if state == "DELIVERED":
            prior = conn.execute(
                """SELECT evidence FROM forward_delivery_events WHERE experiment_version=%s
                   AND match_id=%s AND channel='ARCHIVE' ORDER BY event_id DESC LIMIT 1""",
                (version,match_id),
            ).fetchone()
            if prior[0] != serialized:
                raise EvidenceConflict("different acknowledgement after delivery")
        elif state == "DELIVERY_UNCERTAIN":
            _append(conn,version,match_id,"ARCHIVE","DELIVERED",serialized)
        else:
            raise EvidenceConflict("unexpected archive state")
    return evidence
