import json
from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.validation.forward_evidence_outbox import EvidenceConflict, _freeze, _original
from sports_edge_tennis.validation.forward_evidence_settlement import (
    append_settlement, reconcile_candidate, record_expected_fixture, sporting_grade,
)
from sports_edge_tennis.validation.forward_original_collector import OriginalReceiptStore
from sports_edge_tennis.validation.forward_archive_dispatch import dispatch_archive
from tests.abstention_helpers import insert_match, insert_run, insert_target, utc


def _payload():
    return json.dumps({"fixture_json":json.dumps({"player_1_id":11,"player_2_id":22}),
        "manager_json":[json.dumps({"selected_side":1}),"{}"]}).encode()


def _seed(db, payload=None):
    match = insert_match(db)
    run = insert_run(db,as_of=utc())
    insert_target(db,run_id=run,match_id=match)
    issued = datetime.now(UTC)
    freeze=b'{"candidate":"A","model_versions":["m"],"configuration_versions":["c"],"eligibility_policy":"e","selection_policy":"s"}'
    if payload is None:
        from sports_edge_tennis.validation.forward_evidence_outbox import digest
        record=json.loads(_payload())
        record.update(schema="forward_original_v1",experiment_version="A-v1",
            canonical_match_id=match,first_prediction_run_id=run,tour="ATP_MAIN",
            not_a_published_model_pick=True,original_issued_at_utc=issued.isoformat(),
            scheduled_start_at_utc=(issued+timedelta(hours=1)).isoformat(),
            feature_snapshot_json="{}",feature_sha256=digest(b"{}"),
            freeze_json=freeze.decode(),freeze_sha256=digest(freeze))
        payload=json.dumps(record).encode()
    with db.transaction():
        _freeze(db,"A-v1",freeze)
        _original(db,"A-v1",match,run,payload,issued)
    return match,run,issued


@pytest.mark.parametrize("kind,winner,expected",[
    ("COMPLETED",11,"WON"),("COMPLETED",22,"LOST"),("RETIRED",22,"VOID"),
    ("WALKOVER",11,"VOID"),("DEFAULTED",22,"VOID"),("CANCELLED",None,"CANCELLED"),
    ("UNRESOLVED",None,"UNRESOLVED")])
def test_sporting_orientation_and_outcome(kind,winner,expected):
    assert sporting_grade(_payload(),outcome_kind=kind,source_participants=[22,11],
                          winner_player_id=winner)==expected


def test_append_only_correction_and_separate_market(db):
    match,_,issued=_seed(db)
    def source(kind,winner):
        return json.dumps(dict(outcome_kind=kind,source_reference="synthetic",
            source_participants=[22,11],winner_player_id=winner)).encode()
    def official(key,body,previous=None):
        return append_settlement(db,"A-v1",match,pathway="SPORTING",
            market_key="official_match_winner",evidence_key=key,
            evidence_bytes=body,supersedes_id=previous)
    first=official("1",source("RETIRED",22))
    assert official("1",source("RETIRED",22))==first
    with pytest.raises(EvidenceConflict):
        official("2",source("COMPLETED",11))
    corrected=official("2",source("COMPLETED",11),first)
    assert corrected!=first
    market=json.dumps(dict(outcome_kind="RETIRED",source_reference="synthetic book",
        source_participants=[22,11],selection_player_id=11,bookmaker="test",
        rule_version="test-v1",rule_reference="synthetic-rule-document",
        market_reference="test:market",bookmaker_grade="LOST")).encode()
    with pytest.raises(EvidenceConflict, match="rule not explicitly approved"):
        append_settlement(db,"A-v1",match,pathway="BOOKMAKER",market_key="test:market",
            evidence_key="market1",evidence_bytes=market)
    with pytest.raises(EvidenceConflict, match="rule not explicitly approved"):
        append_settlement(db,"A-v1",match,pathway="BOOKMAKER",market_key="test:market",
            evidence_key="market1",evidence_bytes=market,
            approved_bookmaker_rules={("test","test-v1"): "different-document"})
    append_settlement(db,"A-v1",match,pathway="BOOKMAKER",market_key="test:market",
        evidence_key="market1",evidence_bytes=market,
        approved_bookmaker_rules={("test","test-v1"): "synthetic-rule-document"})
    assert db.execute("SELECT status FROM forward_settlement_events ORDER BY settlement_id").fetchall()==[
        ("VOID",),("WON",),("LOST",)]
    record_expected_fixture(db,"A-v1",match,expected_at=issued,source_bytes=b"synthetic")
    missing=insert_match(db)
    record_expected_fixture(db,"A-v1",missing,expected_at=issued,source_bytes=b"synthetic")
    report=reconcile_candidate(db,"A-v1")
    assert (report["expected"],report["missing"],report["sporting_wins"])==(2,1,1)
    assert report["checkpoint_qualified"] is False
    assert report["settled_selections_descriptive"] == 1
    assert [c["target"] for c in report["checkpoint_progress"]] == [25,50,100,300,600,1000,1500,2000]
    assert all(not c["qualified"] and not c["descriptive_count_reached"] for c in report["checkpoint_progress"])
    assert report["minimum_calendar_days"] == 30
    assert report["independent_qualified_settled"] == 0


def test_archive_commit_then_lost_ack_recovery(db,tmp_path):
    match,_,_=_seed(db)
    store=OriginalReceiptStore(tmp_path/"receipts.sqlite")
    def lost(payload,sha):
        store.receive(payload,sha)
        raise TimeoutError("lost acknowledgement after commit")
    with pytest.raises(TimeoutError):
        dispatch_archive(db,"A-v1",match,lost)
    receipt=dispatch_archive(db,"A-v1",match,store.receive)
    assert receipt["eligible_for_activation_evidence"] is False
    assert store.verify_inventory()["count"]==1
    assert dispatch_archive(db,"A-v1",match,store.receive) is None
