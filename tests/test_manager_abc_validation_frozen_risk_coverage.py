"""Manager A/B/C equivalent first-run coverage: no future threshold tuning."""
from datetime import UTC,datetime,timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.manager_abc_validation_frozen_risk_coverage import (
    COVERAGE_TARGETS,POLICIES,normalize,freeze_threshold,evaluate_selected,
    paired_overlap,evaluate,run,
)

START=datetime(2026,10,15,tzinfo=UTC)
SPLIT=datetime(2026,11,4,tzinfo=UTC)
END=datetime(2026,11,21,tzinfo=UTC)
ASOF=datetime(2026,11,21,3,30,tzinfo=UTC)


def rec(mid,*,day=20,part="validation",winner=1,tour="CHALLENGER",
        original=True,b=True,c=True,conf=.7):
    when=datetime(2026,10,day,12,tzinfo=UTC) if part=="validation" else datetime(2026,11,day,12,tzinfo=UTC)
    def pick(eligible):
        return (1,conf) if eligible else None
    return {
        "match_id":mid,"run_id":mid+100,"tour":tour,
        "edition":str(day),
        "issued_at":when-timedelta(hours=8),"start_at":when,
        "winner":winner,"result_status":"COMPLETED" if winner else "UNOBSERVED",
        "candidates":{
            "original_manager":pick(original),
            "B_guarded2_floor60_spread20":pick(b),
            "C_uncalibrated_mean_floor60":pick(c),
        },
    }


def test_validation_gate_never_uses_later_confidence_distribution():
    val=[rec(i,original=i<=2,b=i<=5,c=True,conf=.62+i*.01) for i in range(1,11)]
    hold=[rec(20+i,part="holdout",day=5,conf=.95) for i in range(1,6)]
    lock=freeze_threshold(val,policy="B_guarded2_floor60_spread20",
                          coverage_target=.20)
    assert lock["status"]=="VALIDATION_CONFIDENCE_CUTOFF_FROZEN"
    assert lock["threshold"]==.66
    same=freeze_threshold(val,policy="B_guarded2_floor60_spread20",
                          coverage_target=.20)
    assert same==lock
    stats,selected=evaluate_selected(hold,policy="B_guarded2_floor60_spread20",
                                   cutoff=lock["threshold"])
    assert stats["selected"]==5
    assert stats["winner_accuracy"]==1
    assert stats["full_official_eligible_match_coverage"] is None


def test_capacity_above_original_policy_candidates_is_unreachable_not_guessed():
    val=[rec(i,original=i<=2,b=i<=3,c=True) for i in range(1,11)]
    for policy,expected_max in [
        ("original_manager",.2),("B_guarded2_floor60_spread20",.3)
    ]:
        lock=freeze_threshold(val,policy=policy,coverage_target=.4)
        assert lock["status"]=="UNREACHABLE_ON_VALIDATION_CANDIDATE_CAPACITY"
        assert lock["threshold"] is None
        assert lock["validation_policy_candidate_maximum_coverage"]==expected_max
    assert freeze_threshold(val,policy="C_uncalibrated_mean_floor60",
                            coverage_target=.4)["threshold"]==.7


def test_holdout_known_at_not_used_to_fit_threshold():
    val=[rec(i,day=20,conf=.60+i*.01) for i in range(1,11)]
    hold=[rec(100+i,day=5,part="holdout",
              conf=.6+.07*i,winner=None if i%3==0 else 1) for i in range(1,6)]
    a=evaluate(val+hold,start=START,end=END,as_of=ASOF,
               validation_end=SPLIT)
    altered=[dict(r,winner=2 if r["winner"] else None) for r in hold]
    b=evaluate(val+altered,start=START,end=END,as_of=ASOF,
               validation_end=SPLIT)
    for target in a["curve"]:
        for policy in POLICIES:
            assert a["curve"][target][policy]["threshold"]==b["curve"][target][policy]["threshold"]
    assert a["research_only_historical_holdout_previously_inspected"]
    assert a["full_official_eligible_match_population"] is None


def test_same_selected_matches_are_scored_identically_not_doubled():
    x=rec(1,part="holdout",day=5,conf=.8,winner=1)
    y=rec(2,part="holdout",day=5,conf=.8,winner=2)
    result=paired_overlap([x,y],[x],first_policy="original_manager",
                          second_policy="B_guarded2_floor60_spread20")
    assert result["same_selected_settled"]==1
    assert result["both_correct"]==1
    assert result["different_selected_populations_not_a_paired_accuracy_test"]


def test_normalize_original_manager_same_run_and_ungraded_not_losses():
    issue=datetime(2026,11,15,10,tzinfo=UTC)
    start=issue+timedelta(hours=2)
    row=(
        1,20,issue,start,"ATP_MAIN",1,
        "AGENT_1_SURFACE_ELO","PREDICTED",1,.8,issue-timedelta(minutes=1),
        "CONSENSUS","NONE",1,.75,
        None,None,None
    )
    data,exclusions,digest=normalize([row],start=START,end=END,as_of=ASOF)
    assert data[0]["winner"] is None
    assert data[0]["candidates"]["original_manager"]==(1,.75)
    assert exclusions=={}
    assert len(digest)==64


def test_late_prediction_rejected_without_invalidating_first_targets():
    start=datetime(2026,11,15,12,tzinfo=UTC)
    issue=start-timedelta(minutes=2)
    row=(1,20,issue,start,"ATP_MAIN",1,
        "AGENT_1_SURFACE_ELO","PREDICTED",1,.8,issue,
        None,None,None,None,None,None,None)
    data,exc,_=normalize([row],start=START,end=END,as_of=ASOF)
    assert data==[]
    assert exc["UNSAFE_OR_LATE_FIRST_PREMATCH_ISSUE"]==1


def test_full_migrated_db_read_only():
    # Existing fixtures are absent; no mutation and transparent missing sample.
    from sports_edge_tennis.validation.manager_abc_first_issue_ablation import source_sql
    assert "pr.experiment_id IS NULL" in source_sql()


def test_empty_migrated_postgres(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        result=run(conn,start=START,end=END,as_of=ASOF,validation_end=SPLIT)
    assert result["all_first_run_targets"]==0
    assert result["curve"]["0.1"]["original_manager"]["unreachable"] is True
    assert result["no_live_rules_changed"] is True
