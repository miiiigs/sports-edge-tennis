"""Synthetic failure recovery; does not call any external service."""
import hashlib
from datetime import UTC, datetime

import pytest

from sports_edge_tennis.validation.forward_evidence_outbox import (
    EvidenceConflict, _append, _freeze, _original, claim_delivery, confirm_delivery,
)
from tests.abstention_helpers import insert_match, insert_run, insert_target, utc


def _seed(db):
    match = insert_match(db)
    run = insert_run(db, as_of=utc())
    insert_target(db, run_id=run, match_id=match)
    return match, run


def _freeze_bytes():
    return b'{"candidate":"A","model_versions":["m"],"configuration_versions":["c"],"eligibility_policy":"e","selection_policy":"s"}'


def test_original_outbox_rollback_and_conflicting_retry(db):
    match, run = _seed(db)
    issued = datetime.now(UTC)
    with pytest.raises(RuntimeError):
        with db.transaction():
            _freeze(db, "A-v1", _freeze_bytes())
            _original(db, "A-v1", match, run, b"exact bytes", issued)
            raise RuntimeError("crash before commit")
    assert db.execute("SELECT count(*) FROM forward_originals").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM forward_delivery_events").fetchone()[0] == 0
    with db.transaction():
        _freeze(db, "A-v1", _freeze_bytes())
        _original(db, "A-v1", match, run, b"exact bytes", issued)
        _original(db, "A-v1", match, run, b"exact bytes", issued)
    with pytest.raises(EvidenceConflict), db.transaction():
        _original(db, "A-v1", match, run, b"changed", issued)
    assert claim_delivery(db, "A-v1", match, "ARCHIVE") == (
        b"exact bytes", hashlib.sha256(b"exact bytes").hexdigest()
    )
    # Lost response or archive outage: exact-byte idempotent retry.
    assert claim_delivery(db, "A-v1", match, "ARCHIVE")[0] == b"exact bytes"
    confirm_delivery(db, "A-v1", match, "ARCHIVE", state="DELIVERED", evidence="synthetic receipt")
    assert claim_delivery(db, "A-v1", match, "ARCHIVE") is None


def test_telegram_published_then_response_lost(db):
    match, run = _seed(db)
    with db.transaction():
        _freeze(db, "A-v1", _freeze_bytes())
        _original(db, "A-v1", match, run, b"exact bytes", datetime.now(UTC))
        # Synthetic only: never enqueued by the actual shadow wrapper.
        _append(db, "A-v1", match, "TELEGRAM", "PENDING")
    published = []
    def publish_then_lose_response():
        published.append("channel:42")
        raise TimeoutError("response lost after publish")
    with pytest.raises(TimeoutError):
        assert claim_delivery(db, "A-v1", match, "TELEGRAM") is not None
        publish_then_lose_response()
    assert claim_delivery(db, "A-v1", match, "TELEGRAM") is None
    assert published == ["channel:42"]
    confirm_delivery(db, "A-v1", match, "TELEGRAM",
                     state="RECONCILED", evidence=published[0])
    assert claim_delivery(db, "A-v1", match, "TELEGRAM") is None
