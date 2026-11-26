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


def test_runtime_freeze_rejects_wrong_candidate_and_nested_commit(db):
    import json
    from dataclasses import replace
    from sports_edge_tennis.validation.forward_evidence_outbox import (
        build_rehearsal_freeze, run_with_originals,
    )
    from tests.test_full_slate_runner import _request, _seed_configs

    _seed_configs(db)
    request = replace(_request(db, match_ids=[]), experiment_id="A-v1",
                      persist_identity_resolutions=True)
    freeze = build_rehearsal_freeze(db, request)
    altered = json.loads(freeze)
    altered["candidate"] = "B"
    with pytest.raises(EvidenceConflict, match="freeze"):
        run_with_originals(db, request, freeze_bytes=json.dumps(
            altered, sort_keys=True, separators=(",", ":")).encode())
    assert db.execute("SELECT count(*) FROM prediction_runs").fetchone()[0] == 0
    with db.transaction():
        with pytest.raises(ValueError, match="idle"):
            run_with_originals(db, request, freeze_bytes=freeze)
    assert db.execute("SELECT count(*) FROM forward_experiment_freezes").fetchone()[0] == 0


def test_real_runner_atomic_original_and_rollback(db, monkeypatch):
    import json
    from dataclasses import replace
    from datetime import timedelta
    from sports_edge_tennis.validation import forward_evidence_outbox as outbox
    from tests.test_full_slate_runner import (
        _insert_scheduled_target, _request, _seed_configs,
    )

    _seed_configs(db)
    now = datetime.now(UTC)
    observed = now - timedelta(minutes=5)
    target = _insert_scheduled_target(db, as_of=observed, ingested_at=observed)
    request = replace(_request(db, match_ids=[target["match_id"]],
                              as_of=now-timedelta(minutes=1)),
                      experiment_id="A-v1", persist_identity_resolutions=True)
    freeze = outbox.build_rehearsal_freeze(db, request)
    original = outbox._original

    def write_then_fail(*args):
        original(*args)
        raise RuntimeError("simulated crash before outer commit")

    with monkeypatch.context() as patch:
        patch.setattr(outbox, "_original", write_then_fail)
        with pytest.raises(RuntimeError, match="simulated crash"):
            outbox.run_with_originals(db, request, freeze_bytes=freeze)
    for table in ("prediction_runs", "predictions", "prediction_feature_snapshots",
                  "identity_resolution_provenance", "manager_consensus_results",
                  "forward_experiment_freezes", "forward_originals", "forward_delivery_events"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    result = outbox.run_with_originals(db, request, freeze_bytes=freeze)
    assert result.runner_status == "COMPLETED"
    row = db.execute("SELECT payload,payload_sha256 FROM forward_originals").fetchone()
    record = json.loads(bytes(row[0]))
    assert record["canonical_match_id"] == target["match_id"]
    assert len(record["agents_json"]) == 3
    assert len(record["identity_provenance_json"]) == 2
    assert outbox.digest(bytes(row[0])) == row[1]
    assert outbox.claim_delivery(db, "A-v1", target["match_id"], "ARCHIVE") == (bytes(row[0]), row[1])
    with pytest.raises(EvidenceConflict):
        outbox.run_with_originals(db, request, freeze_bytes=freeze)
    assert db.execute("SELECT count(*) FROM prediction_runs").fetchone()[0] == 1
