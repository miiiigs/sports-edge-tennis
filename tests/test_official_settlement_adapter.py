import json
from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.validation.forward_evidence_outbox import (
    EvidenceConflict, _freeze, _original,
)
from sports_edge_tennis.validation.official_settlement_adapter import (
    settle_stored_official_result,
)
from tests.abstention_helpers import insert_match, insert_run, insert_target

START = datetime(2026, 9, 1, 12, tzinfo=UTC)
ISSUED = START-timedelta(hours=2)
SOURCE = {("TEST_OFFICIAL", "results"): "synthetic canonical orientation contract"}


def seed(db):
    match = insert_match(db, match_start_at=START, ingested_at=START)
    p1,p2 = db.execute(
        "SELECT player_1_id,player_2_id FROM match_versions WHERE match_id=%s",
        (match,)).fetchone()
    run = insert_run(db, as_of=ISSUED)
    insert_target(db,run_id=run,match_id=match)
    freeze = b'{"candidate":"A","model_versions":["m"],"configuration_versions":["c"],"eligibility_policy":"e","selection_policy":"s"}'
    payload = json.dumps({
        "experiment_version": "A-v1", "canonical_match_id":match,
        "fixture_json":json.dumps({"player_1_id":p1,"player_2_id":p2}),
        "manager_json":[json.dumps({"selected_side":1}),"{}"],
        "original_issued_at_utc":ISSUED.isoformat(),
        "scheduled_start_at_utc":START.isoformat(),
    }).encode()
    with db.transaction():
        _freeze(db,"A-v1",freeze)
        _original(db,"A-v1",match,run,payload,ISSUED)
    ingest = db.execute(
        """INSERT INTO ingest_runs(source_system,source_dataset,started_at,status)
           VALUES ('TEST_OFFICIAL','results',%s,'RUNNING') RETURNING ingest_run_id""",
        (START,)).fetchone()[0]
    return match,ingest


def add_result(db,match,ingest,at,side):
    return db.execute(
        """INSERT INTO match_result_observations
           (match_id,outcome_status,winner_side,source_observed_at,as_of,ingested_at,ingest_run_id)
           VALUES (%s,'COMPLETED',%s,%s,%s,%s,%s)
           RETURNING match_result_observation_id""",
        (match,side,at,at,at,ingest)).fetchone()[0]


def settle(db, match, cutoff, sources=SOURCE):
    return settle_stored_official_result(
        db,"A-v1",match,cutoff=cutoff,approved_sources=sources)


def test_automatic_sporting_settlement_retry_and_correction(db):
    match,ingest = seed(db)
    assert settle(db,match,START)["status"] == "NO_VISIBLE_RESULT"
    first = START+timedelta(hours=2)
    second = first+timedelta(minutes=1)
    add_result(db,match,ingest,first,1)
    won = settle(db,match,first)
    assert won["status"] == "WON"
    assert settle(db,match,first) == won
    add_result(db,match,ingest,second,2)
    lost = settle(db,match,second)
    assert lost["status"] == "LOST"
    assert db.execute(
        "SELECT supersedes_id FROM forward_settlement_events WHERE settlement_id=%s",
        (lost["settlement_id"],)).fetchone()[0] == won["settlement_id"]
    with pytest.raises(EvidenceConflict,match="stale"):
        settle(db,match,first)
    assert db.execute("SELECT count(*) FROM forward_settlement_events").fetchone()[0] == 2


def test_unapproved_or_prestart_result_does_not_settle(db):
    match,ingest = seed(db)
    at = START-timedelta(minutes=1)
    add_result(db,match,ingest,at,1)
    with pytest.raises(EvidenceConflict,match="not approved"):
        settle(db,match,START,{("OTHER","results"):"contract"})
    with pytest.raises(EvidenceConflict,match="timing|unoriented"):
        settle(db,match,START)
    assert db.execute("SELECT count(*) FROM forward_settlement_events").fetchone()[0] == 0
