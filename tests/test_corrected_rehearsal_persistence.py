"""PostgreSQL synthetic rehearsal only: atomic persistence and recovery."""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.validation import corrected_rehearsal_persistence as persistence
from sports_edge_tennis.validation.forward_evidence_outbox import EvidenceConflict
from sports_edge_tennis.validation.forward_archive_dispatch import dispatch_archive
from sports_edge_tennis.validation.forward_original_collector import OriginalReceiptStore
from sports_edge_tennis.runner.full_slate import run_full_slate
from tests.test_corrected_candidate_rehearsal import inputs
from tests.test_full_slate_runner import _insert_scheduled_target, _request, _seed_configs


def seed(db):
    now = datetime.now(UTC)
    observed = now - timedelta(minutes=5)
    target = _insert_scheduled_target(db, as_of=observed, ingested_at=observed)
    _seed_configs(db)
    cutoff = now - timedelta(minutes=1)
    anchor = run_full_slate(db, replace(
        _request(db, match_ids=[target["match_id"]], as_of=cutoff),
        experiment_id=persistence.REHEARSAL_RUN))
    data = inputs()
    data["request"] = replace(data["request"], player_1_id=target["player_1_id"],
                              player_2_id=target["player_2_id"], as_of=cutoff,
                              tour_scope="ATP_MAIN", competition_tier="ATP_250")
    # No real history: eight synthetic observations with separate IDs.
    data["history"] = [replace(
        row, match_id=10000+i, player_1_id=9001,
        player_2_id=9100+i, as_of=cutoff-timedelta(days=10-i),
        source_observed_at=cutoff-timedelta(days=10-i),
        match_start_at=cutoff-timedelta(days=11-i))
        for i,row in enumerate(data["history"])]
    data["identity_map"] = {9001: target["player_1_id"],
                            target["player_1_id"]: target["player_1_id"],
                            target["player_2_id"]: target["player_2_id"],
                            **{9100+i:9100+i for i in range(8)}}
    data.update(run_id=anchor.prediction_run_id, match_id=target["match_id"],
                scheduled_start=observed+timedelta(days=1))
    return data


def test_atomic_three_candidate_persistence_and_exact_retry(db, monkeypatch, tmp_path):
    data = seed(db)
    original = persistence._original
    calls = []
    def fail_second(*args):
        original(*args)
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("simulated crash")
    with monkeypatch.context() as patch:
        patch.setattr(persistence, "_original", fail_second)
        with pytest.raises(RuntimeError, match="simulated crash"):
            persistence.persist_corrected_rehearsal(db, **data)
    for table in ("forward_originals","forward_experiment_freezes","forward_delivery_events"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0

    result = persistence.persist_corrected_rehearsal(db, **data)
    assert result == persistence.persist_corrected_rehearsal(db, **data)
    rows = db.execute("SELECT payload FROM forward_originals").fetchall()
    assert len(rows) == 3
    records = {r["candidate"]:r for r in (json.loads(bytes(row[0])) for row in rows)}
    assert records["B"]["candidate_decision"]["p1"] == records["D"]["candidate_decision"]["p1"]
    assert all(not r["eligible_for_prospective_evidence"] for r in records.values())
    assert db.execute("SELECT count(*) FROM forward_delivery_events").fetchone()[0] == 3
    store = OriginalReceiptStore(tmp_path/"synthetic.sqlite")
    for version in result["versions"]:
        receipt = dispatch_archive(db, version, data["match_id"], store.receive)
        assert receipt["eligible_for_activation_evidence"] is False
    assert store.verify_inventory()["count"] == 3
    changed = {**data, "history": data["history"][:-1]}
    with pytest.raises(EvidenceConflict, match="conflicting original"):
        persistence.persist_corrected_rehearsal(db, **changed)


def test_wrong_anchor_and_orientation_fail_closed(db):
    data = seed(db)
    with pytest.raises(EvidenceConflict, match="anchor"):
        persistence.persist_corrected_rehearsal(db, **{**data, "run_id": 0})
    request = data["request"]
    reversed_request = replace(request, player_1_id=request.player_2_id,
                               player_2_id=request.player_1_id)
    with pytest.raises(EvidenceConflict, match="orientation"):
        persistence.persist_corrected_rehearsal(db, **{**data, "request": reversed_request})
    assert db.execute("SELECT count(*) FROM forward_originals").fetchone()[0] == 0


def test_each_candidate_uses_its_own_selection(db):
    from sports_edge_tennis.validation.forward_evidence_settlement import (
        append_settlement, reconcile_candidate, sporting_grade,
    )
    data = seed(db)
    report = persistence.persist_corrected_rehearsal(db, **data)
    p1 = data["request"].player_1_id
    p2 = data["request"].player_2_id
    for version in report["versions"]:
        payload = bytes(db.execute(
            "SELECT payload FROM forward_originals WHERE experiment_version=%s",
            (version,)).fetchone()[0])
        original = json.loads(payload)
        decision = original["candidate_decision"]
        expected = ("NO_SELECTION" if decision["side"] is None else
                    "WON" if decision["side"] == 1 else "LOST")
        evidence = json.dumps({
            "source_reference": "synthetic result",
            "outcome_kind": "COMPLETED",
            "source_participants": [p2, p1],
            "winner_player_id": p1,
        }).encode()
        assert sporting_grade(payload, outcome_kind="COMPLETED",
                              source_participants=[p2,p1], winner_player_id=p1) == expected
        append_settlement(db, version, data["match_id"], pathway="SPORTING",
                          market_key="official_match_winner",
                          evidence_key="synthetic:1", evidence_bytes=evidence)
        status = db.execute(
            "SELECT status FROM forward_settlement_events WHERE experiment_version=%s",
            (version,)).fetchone()[0]
        assert status == expected
        assert reconcile_candidate(db, version)["independent_qualified_settled"] == 0
        original["candidate_decision"]["threshold"] = "0.1"
        with pytest.raises(EvidenceConflict, match="invalid synthetic candidate"):
            sporting_grade(json.dumps(original).encode(), outcome_kind="COMPLETED",
                           source_participants=[p1,p2], winner_player_id=p1)
