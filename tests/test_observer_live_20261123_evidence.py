"""Real Railway originals: one live forward observer cycle, not a replay."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SOURCE=Path(__file__).parents[1]/"docs/research/forward_captures/observer_live_20261123_1104Z.json"


def test_railway_first_scheduled_cycle_preserved_original_raw_records():
    doc=json.loads(SOURCE.read_text())
    assert doc["provenance"]=="ORIGINAL_RAILWAY_LOG_TIMESTAMPS"
    assert doc["not_sufficient_for_two_cycle_acceptance"]
    assert len(doc["records"])==7
    assert len(doc["summaries"])==3
    assert len(doc["heartbeats"])==1
    h=json.loads(doc["heartbeats"][0]["raw_json"])
    assert h["enabled"] is True
    assert h["status"]=="CAPTURED"
    assert (h["attempted"],h["successful"],h["failed"],h["records_logged"])==(3,3,0,7)
    assert h["persistent_storage_verified"] is False  # original log had no external archive proof
    assert h["published_model_picks_changed"] is False
    records_by_run=defaultdict(list)
    all_ids=[]
    for entry in doc["records"]:
        r=json.loads(entry["raw_json"])
        assert r["not_a_published_model_pick"] is True
        assert r["outcomes_available_during_capture"] is False
        assert r["original_issued_at_utc"]<r["scheduled_start_at_utc"]
        assert r["captured_at_utc"]<r["scheduled_start_at_utc"]
        assert datetime.fromisoformat(r["original_issued_at_utc"])<datetime.fromisoformat(entry["railway_utc"])
        all_ids.append(r["canonical_match_id"])
        records_by_run[r["first_prediction_run_id"]].append(r)
    assert len(set(all_ids))==7
    counters=defaultdict(int)
    for entry in doc["summaries"]:
        r=json.loads(entry["raw_json"])
        mids=r["filtered_to_first_run_ids"]
        assert len(mids)==1
        n=mids[0]
        records=records_by_run[n]
        assert len(records)==r["first_issued_future_targets"]
        assert r["settled_match_results_at_capture"]==0
        assert r["no_market_api_calls"] and r["no_database_writes"]
        digest=hashlib.sha256("".join(
            json.dumps(v,sort_keys=True,separators=(",",":"))+"\n"
            for v in sorted(records,key=lambda x:x["canonical_match_id"])
        ).encode()).hexdigest()
        assert digest==r["utc_capture_records_sha256"]
        counters[n]=len(records)
    assert sorted(counters.values())==[0,1,6]
    assert sum(counters.values())==h["records_logged"]
