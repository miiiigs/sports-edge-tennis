"""Manager C learned reliability challenger experiment must never train on future."""
from datetime import UTC, datetime, timedelta
import pytest

from sports_edge_tennis.validation.manager_c_reliability_chooser import (
    POLICY, CODES, analyze, features, fit, score, _frozen_cutoff, _accuracy,
)

SPLIT=datetime(2026,11,4,tzinfo=UTC)


def population():
    rows=[];agents={};known={}
    for i in range(190):
        train=i<110
        start=(datetime(2026,10,16,tzinfo=UTC)+timedelta(hours=i*3)
               if train else datetime(2026,11,8,tzinfo=UTC)+timedelta(hours=(i-110)*3))
        p=.605+(i%9)*.027
        side=1 if p>.5 else 2
        # Deterministic mixture of correct and incorrect settled labels.
        winner=side if i%5 in (0,1,2) else 2
        rows.append({"match_id":i+1,"tour":"CHALLENGER" if i%4 else "ATP_MAIN",
                     "issued_at":start-timedelta(hours=2),"start_at":start,
                     "winner":winner,"candidates":{POLICY:(side,p)}})
        known[i+1]=start+timedelta(hours=1)
        agents[i+1]={
            CODES[0]:(1,p),
            CODES[1]:(2,.5-.03*(i%2)) if i%3==0 else (1,p-.03),
        }
    # one prior match settled after split, must not leak into fit
    known[1]=SPLIT+timedelta(minutes=2)
    return rows,agents,known


def test_training_outcome_cutoff_and_unseen_label_invariance():
    rows,agents,known=population()
    a=analyze(rows,split=SPLIT,result_known_at=known,original_agents=agents,
              dataset_sha256="abc")
    assert a["training_original_c_settled_known_at_cutoff"]==109
    assert a["evaluation_targets"]==80
    assert a["validation_targets"]==110
    assert a["independent_prospective_confirmation"] is False
    assert a["no_live_selection_changes"] is True
    assert a["not_full_official_fixture_denominator"] is True
    assert len(a["fitted_weights"])==6
    assert a["full_candidate_correctness_calibration"]["calibrated_selection_model"]["n"]==80
    assert set(a["threshold_experiments"])=={"0.2","0.4"}
    flipped=[dict(r,winner=(2 if r["winner"]==1 else 1))
             if r["start_at"]>=SPLIT else r for r in rows]
    b=analyze(flipped,split=SPLIT,result_known_at=known,original_agents=agents,
              dataset_sha256="abc")
    assert a["fitted_weights"]==b["fitted_weights"]
    assert a["candidate_train_sha256"]==b["candidate_train_sha256"]
    for key in a["threshold_experiments"]:
        aa=a["threshold_experiments"][key]
        bb=b["threshold_experiments"][key]
        assert aa["original_confidence_cutoff"]==bb["original_confidence_cutoff"]
        assert aa["reliability_cutoff"]==bb["reliability_cutoff"]
        assert aa["selected_match_ids_sha256"]==bb["selected_match_ids_sha256"]


def test_player_orientation_uses_probability_gap_not_side_number():
    a={"match_id":42,"tour":"CHALLENGER","candidates":{POLICY:(1,.7)}}
    agents={42:{CODES[0]:(1,.8),CODES[1]:(2,.25)}}
    x=features(a,agents)
    assert x[0]==1
    assert x[2]==1
    assert x[4]==1
    assert x[5]==0
    assert x[3]==pytest.approx(2.2)
    missing=features(a,{})
    assert missing[5]==1


def test_fit_rejects_too_small_or_single_outcome_and_is_deterministic():
    x=(1.,.5,1.,.3,0.,0.)
    with pytest.raises(ValueError,match="at least 30"):
        fit([(x,1)]*29)
    with pytest.raises(ValueError,match="both correct"):
        fit([(x,1)]*40)
    w=fit([(x,i%2) for i in range(40)])
    assert w==fit([(x,i%2) for i in range(40)])
    assert 0<score(w,x)<1


def test_unreachable_coverage_must_not_create_picks():
    a=_frozen_cutoff([.8,.9],10,.4)
    assert a["status"]=="INSUFFICIENT_PREMATCH_C_CANDIDATES"
    assert a["cutoff"] is None


def test_void_and_pending_are_not_losses():
    rs=[{"tour":"CHALLENGER","match_id":i,"winner":w,
         "candidates":{POLICY:(1,.7)}}
        for i,w in enumerate([1,2,None])]
    x=_accuracy(rs)
    assert (x["issued"],x["settled"],x["ungraded"],x["wins"],x["losses"])==(3,2,1,1,1)


def test_duplicate_match_id_rejected():
    rows,agents,known=population()
    with pytest.raises(ValueError,match="duplicate"):
        analyze(rows+[dict(rows[0])],split=SPLIT,result_known_at=known,
                original_agents=agents)


def test_no_future_training_labels_even_if_normalizer_supplied_final_outcome():
    rows,agents,known=population()
    for r in rows:
        if r["start_at"]<SPLIT:
            known[r["match_id"]]=SPLIT+timedelta(days=1)
    with pytest.raises(ValueError,match="at least 30"):
        analyze(rows,split=SPLIT,result_known_at=known,original_agents=agents)
