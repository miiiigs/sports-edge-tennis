"""Prospective shadow publication: frozen A/B/C/blend, PIT safety, no outcome leakage."""
from datetime import UTC,datetime,timedelta
import json
import psycopg
import pytest

from sports_edge_tennis.validation.prospective_manager_shadow_capture import (
    FROZEN,VERSION,_blend,_candidate,collect,run,
)

NOW=datetime(2026,11,22,14,25,tzinfo=UTC)
ISSUE=NOW-timedelta(hours=2)
MATCH=NOW+timedelta(hours=12)
START=NOW+timedelta(minutes=30)
END=NOW+timedelta(days=7)
SHA="test-locked-commit-1234"


def agent(mid, code, status,side,confidence, *,
          issued=ISSUE,match=MATCH,
          manager_status="NO_BET",manager_reason="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
          mgr_side=None,mgr_conf=None,
          res_status=None,res_winner=None):
    return (mid,mid+1000,issued,match,"CHALLENGER",200,
            code,status,side,confidence,issued,
            manager_status,manager_reason,mgr_side,mgr_conf,
            res_status,res_winner,None)


def group(mid=1,*,a1p=.78,a2p=.76):
    return [
        agent(mid,"AGENT_1_SURFACE_ELO","PREDICTED",1,a1p),
        agent(mid,"AGENT_2_HIERARCHICAL_MARKOV","PREDICTED",1,a2p),
        agent(mid,"AGENT_3_INTRANSITIVITY","ABSTAINED",None,None),
    ]


def capture(rows):
    return collect(rows,cutoff=NOW,captured_at=NOW,window_start=START,
                   window_end=END,source_sha=SHA)


def test_high_consensus_prospectively_selected_without_retro_results():
    r=capture(group())
    assert r["first_issued_future_targets"]==1
    x=r["genuine_future_shadow_records"][0]
    assert x["frozen_research_policies"]["B_guarded2_floor60_spread20"]["status"]=="SELECTED"
    assert x["frozen_research_policies"]["C_uncalibrated_mean_floor60"]["status"]=="SELECTED"
    assert x["frozen_research_policies"]["A1A2_equal_log_odds_blend"]["status"]=="SELECTED"
    assert x["frozen_research_policies"]["original_manager"]["status"]=="ABSTAINED"
    assert x["frozen_research_policies"]["production_original_as_issued"]["status"]=="ABSTAINED"
    assert x["scheduled_start_at_utc"]>x["captured_at_utc"]
    assert r["settled_match_results_at_capture"]==0
    assert r["no_market_api_calls"] is True


def test_original_manager_selected_separately_from_research_b_c():
    rows=group()
    new=[]
    for r in rows:
        x=list(r)
        x[11]="CONSENSUS";x[13]=1;x[14]=.8
        new.append(tuple(x))
    out=capture(new)
    assert out["selection_counts"]["original_manager"]==1
    assert out["selection_counts"]["B_guarded2_floor60_spread20"]==1


def test_p1_p2_swap_blend_orientation_and_confidence():
    a=_blend(.8,.7)
    b=_blend(.2,.3)
    assert a[0]==1 and b[0]==2
    assert a[1]==pytest.approx(1-b[1])
    assert _candidate("A1A2_equal_log_odds_blend",a)["status"]=="SELECTED"


def test_low_threshold_is_nopick_but_p1_is_preserved():
    out=_candidate("C_uncalibrated_mean_floor60",(1,.62))
    assert out["status"]=="BELOW_FROZEN_THRESHOLD"
    assert out["side"] is None
    assert out["p1"]==.62


def test_reject_outcomes_even_on_future_scheduled_fixture():
    rows=[list(r) for r in group()]
    rows[0][15]="COMPLETED";rows[0][16]=1
    for r in rows[1:]:
        r[15]="COMPLETED";r[16]=1
    with pytest.raises(ValueError,match="final winner"):
        capture([tuple(r) for r in rows])


def test_reject_old_fixture_if_capture_after_30min_cutoff():
    rows=group()
    modified=[]
    for r in rows:
        x=list(r);x[3]=NOW+timedelta(minutes=29)
        modified.append(tuple(x))
    with pytest.raises(ValueError,match="fixture not in frozen future window"):
        capture(modified)


def test_fail_on_inconsistent_repeated_original_match_run():
    rows=group()
    a=list(rows[1]);a[1]=23454
    with pytest.raises(ValueError,match="mixed first run"):
        capture([rows[0],tuple(a),rows[2]])


def test_fail_if_unissued_future_agent_timestamps():
    rows=group()
    a=list(rows[0]);a[10]=ISSUE+timedelta(minutes=1)
    with pytest.raises(ValueError,match="future agent"):
        capture([tuple(a),rows[1],rows[2]])


def test_result_hash_deterministic_despite_join_row_order():
    rows=group(1)+group(2)
    a=capture(rows)
    b=capture(list(reversed(rows)))
    assert a["utc_capture_records_sha256"]==b["utc_capture_records_sha256"]
    assert a["genuine_future_shadow_records"]==b["genuine_future_shadow_records"]


def test_capture_cannot_be_backdated_by_more_than_five_minutes():
    with pytest.raises(ValueError,match="within five minutes"):
        collect([],cutoff=NOW-timedelta(hours=2),captured_at=NOW,
                window_start=START,window_end=END,source_sha=SHA)


def test_shadow_counts_empty_on_migrated_db(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        out=run(conn,captured_at=NOW,start=START,end=END,source_sha=SHA)
    assert out["first_issued_future_targets"]==0
    assert out["selection_counts"]=={k:0 for k in FROZEN}
    assert out["no_database_writes"] is True


def test_duplicate_agent_status_fails_closed():
    with pytest.raises(ValueError,match="duplicate"):
        capture(group()+[group()[0]])


def test_forward_v2_preserves_original_agent_p1_and_original_asof_without_inventing_features():
    rows=[
        agent(33,"AGENT_1_SURFACE_ELO","PREDICTED",2,.78),
        agent(33,"AGENT_2_HIERARCHICAL_MARKOV","PREDICTED",1,.70),
        agent(33,"AGENT_3_INTRANSITIVITY","ABSTAINED",None,None),
    ]
    rep=capture(rows)
    a=rep["genuine_future_shadow_records"][0]
    assert a["version"].endswith("_V2")
    assert a["original_agent_forecasts"]["AGENT_1_SURFACE_ELO"]=={
        "status":"PREDICTED",
        "selection_side":2,
        "player_1_probability":pytest.approx(.22),
        "prediction_as_of_utc":ISSUE.isoformat(),
    }
    assert a["original_agent_forecasts"]["AGENT_2_HIERARCHICAL_MARKOV"]["player_1_probability"]==pytest.approx(.7)
    assert a["original_agent_forecasts"]["AGENT_3_INTRANSITIVITY"]["status"]=="ABSTAINED"
    assert a["original_agent_forecasts"]["AGENT_3_INTRANSITIVITY"]["player_1_probability"] is None
    assert a["underlying_feature_cutoff_provenance_complete"] is False
    assert a["underlying_feature_snapshot_sha256"] is None
    assert a["original_production_model_version_unavailable"] is True
    assert a["frozen_research_definition_commit"]==SHA
    assert rep["settled_match_results_at_capture"]==0


def test_forward_v2_raw_original_input_changes_record_hash_without_changing_frozen_selection_cutoffs():
    first=capture(group(48,a1p=.78,a2p=.76))
    second=capture(group(48,a1p=.79,a2p=.76))
    assert first["utc_capture_records_sha256"]!=second["utc_capture_records_sha256"]
    assert first["frozen_rules"]==second["frozen_rules"]
    assert first["genuine_future_shadow_records"][0]["original_agent_forecasts"]["AGENT_1_SURFACE_ELO"]["player_1_probability"]==pytest.approx(.78)
