"""Actual DB runner + corrected history loader, still synthetic fixtures."""
import json
from dataclasses import replace
from datetime import UTC,datetime,timedelta

import pytest

from sports_edge_tennis.validation.corrected_database_rehearsal import (
    run_isolated_corrected_from_database,
)
from sports_edge_tennis.validation.corrected_rehearsal_persistence import REHEARSAL_RUN
from sports_edge_tennis.validation.forward_evidence_outbox import EvidenceConflict
from sports_edge_tennis.runner.full_slate import run_full_slate
from tests.test_full_slate_runner import _insert_scheduled_target,_seed_configs,_request


def seed_runtime(db, experiment=REHEARSAL_RUN):
    now = datetime.now(UTC)
    observed = now-timedelta(minutes=10)
    cutoff = now-timedelta(minutes=1)
    target = _insert_scheduled_target(db,as_of=observed,ingested_at=observed)
    mid = target["match_id"]
    edition = db.execute("SELECT tournament_edition_id FROM matches WHERE match_id=%s",(mid,)).fetchone()[0]
    history_id = db.execute("INSERT INTO matches(tournament_edition_id) VALUES(%s) RETURNING match_id",(edition,)).fetchone()[0]
    start = observed-timedelta(days=2)
    end = start+timedelta(hours=2)
    db.execute("""INSERT INTO match_versions(match_id,player_1_id,player_2_id,match_start_at,
        tour_scope,competition_tier,surface,round_code,best_of_sets,qualifying_flag,
        match_status,source_observed_at,as_of,ingested_at)
        VALUES(%s,%s,%s,%s,'ATP_MAIN','ATP_250','HARD','Q1',3,TRUE,'COMPLETED',%s,%s,%s)""",
        (history_id,target["player_1_id"],target["player_2_id"],start,end,end,end))
    db.execute("""INSERT INTO match_result_observations(match_id,outcome_status,winner_side,
        source_observed_at,as_of,ingested_at) VALUES(%s,'COMPLETED',1,%s,%s,%s)""",
        (history_id,end,end,end))
    _seed_configs(db)
    run = run_full_slate(db,replace(_request(db,match_ids=[mid],as_of=cutoff),
                                  experiment_id=experiment))
    assert run.runner_status == "COMPLETED"
    return run.prediction_run_id,mid


def test_actual_runner_and_corrected_loader_feed_three_local_candidates(db):
    run,mid = seed_runtime(db)
    report = run_isolated_corrected_from_database(db,run_id=run,match_id=mid)
    assert report["candidate_count"] == 3
    assert report == run_isolated_corrected_from_database(db,run_id=run,match_id=mid)
    originals = [json.loads(bytes(row[0])) for row in db.execute(
        "SELECT payload FROM forward_originals WHERE match_id=%s",(mid,)).fetchall()]
    assert len(originals) == 3
    for original in originals:
        features = json.loads(original["feature_snapshot_json"])
        evidence = features["isolated_runtime_evidence"]
        assert evidence["history_rows"] == 1
        assert len(evidence["agent_prediction_ids"]) == 3
        assert evidence["corrected_history_loader_parity"]
        assert evidence["actual_persisted_specialist_inputs"]
        assert original["eligible_for_prospective_evidence"] is False
    assert originals[0]["can_promote"] is False


def test_real_or_unscoped_run_cannot_enter_synthetic_path(db):
    run,mid = seed_runtime(db,experiment=None)
    with pytest.raises(EvidenceConflict,match="synthetic anchor"):
        run_isolated_corrected_from_database(db,run_id=run,match_id=mid)
    assert db.execute("SELECT count(*) FROM forward_originals").fetchone()[0] == 0
