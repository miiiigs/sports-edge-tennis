"""Second live scheduled cycle with original A1/A2/A3 v2 probabilities."""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

F=Path(__file__).parents[1]/"docs/research/forward_captures/observer_live_v2_20261123_1205Z.json"


def test_second_scheduled_cycle_is_real_captured_and_v2():
    a=json.loads(F.read_text())
    assert a["cycle_verification_class"]=="TWO_CONSECUTIVE_SCHEDULED_EXECUTIONS"
    assert a["separate_persistent_archival_automation_proven"] is False
    assert len(a["records"])==1
    assert len(a["summaries"])==2
    assert len(a["heartbeats"])==1
    h=json.loads(a["heartbeats"][0]["raw"])
    assert (h["attempted"],h["successful"],h["failed"],h["records_logged"])==(2,2,0,1)
    assert h["status"]=="CAPTURED"
    assert h["published_model_picks_changed"] is False
    r=json.loads(a["records"][0]["raw"])
    assert r["version"]=="BRAZZY365_PROSPECTIVE_MANAGER_SHADOW_FROZEN_V2"
    assert r["not_a_published_model_pick"] is True
    assert r["outcomes_available_during_capture"] is False
    assert r["canonical_match_id"]==134291
    assert datetime.fromisoformat(r["captured_at_utc"]) <= (
       datetime.fromisoformat(r["scheduled_start_at_utc"])-timedelta(minutes=30))
    q=r["original_agent_forecasts"]
    assert q["AGENT_1_SURFACE_ELO"]["player_1_probability"]==.548511
    assert q["AGENT_2_HIERARCHICAL_MARKOV"]["player_1_probability"]==.758949
    assert q["AGENT_3_INTRANSITIVITY"]["status"]=="ABSTAINED"
    assert q["AGENT_3_INTRANSITIVITY"]["player_1_probability"] is None
    by_run={r["first_prediction_run_id"]:r}
    for row in a["summaries"]:
        s=json.loads(row["raw"])
        assert s["observer_record_schema_version"].endswith("_V2")
        assert s["railway_deployed_commit"]==a["production_commit"]
        assert not s["external_append_only_archive_verified"]
        assert s["official_complete_fixture_denominator"] is None
        rs=[r] if r["first_prediction_run_id"] in s["filtered_to_first_run_ids"] else []
        assert len(rs)==s["first_issued_future_targets"]
        content="".join(json.dumps(v,sort_keys=True,separators=(",",":"))+"\n" for v in rs)
        assert hashlib.sha256(content.encode()).hexdigest()==s["utc_capture_records_sha256"]
