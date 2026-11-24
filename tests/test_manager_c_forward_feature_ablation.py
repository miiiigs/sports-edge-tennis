from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import CODES
from sports_edge_tennis.validation.manager_c_marginal_cohort import POLICY
from sports_edge_tennis.validation.manager_c_forward_feature_ablation import (
    evaluate, freeze, vector,
)

T = datetime(2026, 11, 4, tzinfo=UTC)
F = datetime(2026, 11, 25, tzinfo=UTC)


def case(mid, start, winner=1, p=.7, tour="CHALLENGER"):
    return dict(match_id=mid, run_id=mid+1000, start_at=start,
                issued_at=start-timedelta(hours=1), tour=tour, winner=winner,
                candidates={POLICY:(1,p) if p is not None else None})


def fixture():
    train = [case(i,T-timedelta(days=2),1 if i%3 else 2,
                  .61+.005*(i%30)) for i in range(1,61)]
    val = [case(i,T+timedelta(days=2),None,.61+.01*(i%20))
           for i in range(101,121)]
    later = [case(i,F+timedelta(days=2),None if i%7==0 else 1 if i%3 else 2,
                  .61+.01*(i%20)) for i in range(201,221)]
    later.append(case(221,F+timedelta(days=2),None,None))
    known = {r["match_id"]:r["start_at"]+timedelta(hours=2) for r in train}
    agents = {r["match_id"]:{CODES[0]:(1,.75),CODES[1]:(1,.65)}
              for r in train+val+later}
    return train,val,later,known,agents


def build():
    train,val,later,known,agents=fixture()
    m=freeze(train,val,train_cutoff=T,freeze_at=F,known=known,agents=agents)
    return m,train,val,later,known,agents


def test_same_candidate_scores_and_equal_issued_comparison():
    m,_,_,later,_,agents=build()
    result=evaluate(m,later,agents=agents)
    assert result["first_run_targets"]==21
    group=result["groups"]["disagreement"]
    assert group["candidate_n"]==group["feature_available_n"]==20
    report=group["raw_vs_feature"]
    full=report["all_common_candidates"]
    assert full["raw"]["settled"]==full["alternative"]["settled"]
    assert full["raw"]["brier"] is not None
    assert full["alternative"]["log_loss"] is not None
    pair=report["targets"]["0.4"]["exact_k_descriptive_only"]
    assert pair["raw"]["issued"]==pair["alternative"]["issued"]==9
    assert pair["removed"]["issued"]==pair["added"]["issued"]
    assert pair["overlap"]+pair["removed"]["issued"]==9
    assert pair["raw"]["ungraded"]+pair["raw"]["settled"]==9
    assert result["groups"]["history"]["feature_missing_n"]==20
    assert result["groups"]["uncertainty"]["status"]=="UNAVAILABLE_FEATURES_OR_INSUFFICIENT_TRAINING"


def test_validation_outcomes_do_not_influence_freeze_and_late_labels_excluded():
    m,train,val,_,known,agents=build()
    changed=[dict(r,winner=2) for r in val]
    again=freeze(train,changed,train_cutoff=T,freeze_at=F,known=known,agents=agents)
    assert m==again
    known[1]=T+timedelta(seconds=1)
    late=freeze(train,val,train_cutoff=T,freeze_at=F,known=known,agents=agents)
    assert late["models"]["calibration"]["training_n"]==59


def test_reject_duplicate_pre_freeze_and_tampered_manifest():
    m,_,_,later,_,agents=build()
    with pytest.raises(ValueError,match="duplicate"):
        evaluate(m,later+[later[0]],agents=agents)
    too_early=dict(later[0],issued_at=F-timedelta(seconds=1))
    with pytest.raises(ValueError,match="predates freeze"):
        evaluate(m,[too_early],agents=agents)
    with pytest.raises(ValueError,match="manifest"):
        evaluate(dict(m,freeze_at=T.isoformat()),later,agents=agents)


def test_enrichment_requires_original_run_and_all_three_clocks():
    r=case(1,F+timedelta(days=2))
    source=dict(run_id=r["run_id"],source_ref="immutable snapshot:1",
                as_of=r["issued_at"],source_observed_at=r["issued_at"],
                ingested_at=r["issued_at"],min_player_prior_match_count=5,
                max_player_rating_uncertainty=3)
    assert vector(r,"history",{}, {1:source})[-1]>0
    assert vector(r,"uncertainty",{}, {1:source})[-1]>0
    assert vector(r,"history",{}, {}) is None
    with pytest.raises(ValueError,match="provenance"):
        vector(r,"history",{}, {1:dict(source,run_id=999)})
    with pytest.raises(ValueError,match="late"):
        vector(r,"history",{}, {1:dict(source,ingested_at=r["issued_at"]+timedelta(seconds=1))})
    with pytest.raises(ValueError,match="missing"):
        vector(r,"history",{}, {1:dict(source,source_observed_at=None)})


def test_history_and_uncertainty_run_only_on_complete_case_and_no_candidate_fabrication():
    train,val,later,known,agents=fixture()
    snapshots={}
    for r in train+val+later[:-2]:
        snapshots[r["match_id"]]=dict(
            run_id=r["run_id"], source_ref="synthetic original fixture",
            as_of=r["issued_at"], source_observed_at=r["issued_at"],
            ingested_at=r["issued_at"], min_player_prior_match_count=r["match_id"]%10,
            max_player_rating_uncertainty=r["match_id"]%5)
    m=freeze(train,val,train_cutoff=T,freeze_at=F,known=known,agents=agents,snapshots=snapshots)
    out=evaluate(m,later,agents=agents,snapshots=snapshots)
    for name in ("history","uncertainty"):
        group=out["groups"][name]
        assert group["feature_available_n"]==19
        assert group["feature_missing_n"]==1
        stats=group["raw_vs_feature"]["all_common_candidates"]
        assert stats["raw"]["issued"]==stats["alternative"]["issued"]==19
