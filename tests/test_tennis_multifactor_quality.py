"""PIT research-only quality experiment must not tune on test outcomes."""
from datetime import UTC, date, datetime, timedelta

import pytest

from research.tennis_multifactor_quality_20261028.quality import (
    Match, agent_agrees, allowed, as_of_cutoff, evaluate, parse, report,
)


def match(mid, day, win, prob=0.78, tier="CHALLENGER", surface="HARD",
          a1=0.78, a2=0.80, side=1, a2side=1, observed="2026-10-10T00:00:00Z"):
    t=datetime.fromisoformat(observed.replace("Z","+00:00")) if observed else None
    return Match(
        mid, date(2026,10,day), datetime(2026,10,day,2,tzinfo=UTC),
        "A1_A2_WIDE_SPREAD",prob,side,surface,tier,side,a2side,a1,a2,win,t,t
    )


def test_only_original_features_can_affect_selection():
    p=match(1,24,"LOST")
    w=match(1,24,"WON")
    assert allowed(p,"quality_balanced")==allowed(w,"quality_balanced")
    assert allowed(p,"quality_strict")==allowed(w,"quality_strict")
    assert not allowed(match(2,24,"WON",a2side=2),"quality_balanced")
    assert not allowed(match(3,24,"WON",surface="UNKNOWN"),"quality_balanced")
    assert not allowed(match(4,24,"WON",a2=None),"quality_balanced")


def test_training_uses_result_observed_before_day_not_hindsight():
    before=[match(i,1+i//10,"WON" if i%3 else "LOST",prob=0.83) for i in range(100)]
    later=[match(200+j,24+j,"WON") for j in range(4)]
    alternative=[match(200+j,24+j,"LOST") for j in range(4)]
    a=evaluate(tuple(before+later),validation_start=date(2026,10,24),validation_end=date(2026,10,27))
    b=evaluate(tuple(before+alternative),validation_start=date(2026,10,24),validation_end=date(2026,10,27))
    assert a["chosen_without_holdout_outcomes"]==b["chosen_without_holdout_outcomes"]
    assert a["training_candidates"]==b["training_candidates"]
    assert a["daily"]!=b["daily"]
    unseen=[match(150,10,"WON",observed="2026-10-25T00:00:00Z")]
    after=evaluate(tuple(before+unseen+later),validation_start=date(2026,10,24),validation_end=date(2026,10,27))
    assert after["train_scored"]==a["train_scored"]


def test_coverage_and_critical_overclaim_guards():
    samples=tuple([match(i,24,"WON" if i<4 else "LOST",prob=0.79) for i in range(6)])
    stats=report(samples,"quality_balanced")
    assert stats["retained"]==6 and stats["won"]==4
    x=evaluate(samples,validation_start=date(2026,10,24),validation_end=date(2026,10,27))
    assert x["deployed"] is False
    assert x["chosen_without_holdout_outcomes"] is None
    assert x["daily"][0]["policies"]["original"]["day_73_status"]=="REPLAY_BELOW_73"
    assert x["daily"][1]["policies"]["original"]["day_73_status"]=="NO_ORIGINAL_PICKS"


def test_parse_rejects_duplicate_original_match_and_bad_probability():
    r={
        "match_id":1,"manila_date":"2026-10-24",
        "picked_at":"2026-10-23T08:00:00Z",
        "aligned_pick_type":"MANAGER_CONSENSUS","aligned_probability":0.83,
        "selected_side":1,"surface":"CLAY","tier":"CHALLENGER",
        "a1_side":1,"a2_side":1,"a1_prob":0.83,"a2_prob":0.84,
        "result_status":"WON","result_observed_at":"2026-10-24T22:00:00Z",
        "result_source_observed_at":"2026-10-24T22:00:00Z",
    }
    assert len(parse({"data":[r]}))==1
    with pytest.raises(ValueError,match="duplicate"):
        parse({"data":[r,r]})
    with pytest.raises(ValueError,match="invalid original confidence"):
        parse({"data":[dict(r,aligned_probability=float("nan"))]})


def test_locked_cutoff_is_manila_midnight():
    assert as_of_cutoff(date(2026,10,24))==datetime(2026,10,23,16,tzinfo=UTC)
