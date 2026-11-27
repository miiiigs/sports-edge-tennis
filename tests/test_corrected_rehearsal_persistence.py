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


def seed(db, *, capture_a=False):
    now = datetime.now(UTC)
    observed = now - timedelta(minutes=5)
    target = _insert_scheduled_target(db, as_of=observed, ingested_at=observed)
    _seed_configs(db)
    cutoff = now - timedelta(minutes=1)
    anchor_request = replace(
        _request(db, match_ids=[target["match_id"]], as_of=cutoff),
        experiment_id=persistence.REHEARSAL_RUN,
        persist_identity_resolutions=capture_a)
    if capture_a:
        from sports_edge_tennis.validation.forward_evidence_outbox import (
            build_rehearsal_freeze, run_with_originals,
        )
        anchor = run_with_originals(
            db, anchor_request, freeze_bytes=build_rehearsal_freeze(db, anchor_request))
    else:
        anchor = run_full_slate(db, anchor_request)
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


def test_four_candidate_end_to_end_rehearsal(db, tmp_path):
    """One synthetic slate, four original journals, lost receipt and correction.

    A runs the existing runner; B/C/D execute A1 replay with injected specialist
    inputs. A and B/C/D are separate transactions, not one atomic four-arm run.
    No live data, independent receipt, external transport or publication.
    """
    from sports_edge_tennis.validation.forward_evidence_outbox import digest
    from sports_edge_tennis.validation.forward_evidence_settlement import (
        append_settlement, reconcile_candidate, record_expected_fixture,
        sporting_grade,
    )
    data = seed(db, capture_a=True)
    report = persistence.persist_corrected_rehearsal(db, **data)
    versions = [persistence.REHEARSAL_RUN, *report["versions"]]
    assert len(set(versions)) == 4
    mid = data["match_id"]
    p1, p2 = data["request"].player_1_id, data["request"].player_2_id
    store = OriginalReceiptStore(tmp_path / "four-arm.sqlite")
    original_bytes = {}
    for version in versions:
        row = db.execute(
            "SELECT payload,payload_sha256 FROM forward_originals "
            "WHERE experiment_version=%s AND match_id=%s", (version, mid)
        ).fetchone()
        original_bytes[version] = bytes(row[0])
        assert digest(original_bytes[version]) == row[1]

        def lost_ack(payload, sha):
            store.receive(payload, sha)
            raise TimeoutError("synthetic lost acknowledgement")

        with pytest.raises(TimeoutError):
            dispatch_archive(db, version, mid, lost_ack)
        assert reconcile_candidate(db, version)["uncertain"] == 1
        receipt = dispatch_archive(db, version, mid, store.receive)
        assert receipt["eligible_for_activation_evidence"] is False
        assert dispatch_archive(db, version, mid, store.receive) is None

        # Synthetic census includes a deliberately missing expected fixture.
        record_expected_fixture(db, version, mid,
            expected_at=data["request"].as_of, source_bytes=b"synthetic census")

    from tests.abstention_helpers import insert_match
    missing = insert_match(db)
    for version in versions:
        record_expected_fixture(db, version, missing,
            expected_at=data["request"].as_of, source_bytes=b"synthetic missing fixture")
        def body(winner):
            return json.dumps({
                "outcome_kind": "COMPLETED",
                "source_participants": [p2, p1],
                "winner_player_id": winner,
                "source_reference": "synthetic result",
            }, sort_keys=True).encode()
        first = append_settlement(db, version, mid, pathway="SPORTING",
            market_key="official_match_winner", evidence_key="synthetic:first",
            evidence_bytes=body(p1))
        corrected = append_settlement(db, version, mid, pathway="SPORTING",
            market_key="official_match_winner", evidence_key="synthetic:correction",
            evidence_bytes=body(p2), supersedes_id=first)
        grade = sporting_grade(original_bytes[version], outcome_kind="COMPLETED",
                               source_participants=[p2, p1], winner_player_id=p2)
        assert db.execute(
            "SELECT status,supersedes_id FROM forward_settlement_events WHERE settlement_id=%s",
            (corrected,)).fetchone() == (grade, first)
        summary = reconcile_candidate(db, version)
        assert (summary["expected"], summary["missing"], summary["archived"]) == (2, 1, 1)
        assert summary["published"] == 0
        assert summary["independent_qualified_settled"] == 0
        assert summary["activation_eligible"] is False
        assert bytes(db.execute(
            "SELECT payload FROM forward_originals WHERE experiment_version=%s AND match_id=%s",
            (version, mid)).fetchone()[0]) == original_bytes[version]
    assert store.verify_inventory() == {"count": 4, "digest_mismatches": 0}
    assert db.execute("SELECT count(*) FROM forward_settlement_events").fetchone()[0] == 8
