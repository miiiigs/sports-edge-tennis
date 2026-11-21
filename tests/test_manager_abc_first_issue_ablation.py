"""Manager A/B/C same-first-issued read-only shadow tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES,_available_agents,_policy,_wilson,run,source_sql,summarize,
)

START=datetime(2026,10,15,tzinfo=UTC)
END=datetime(2026,11,21,tzinfo=UTC)
ASOF=datetime(2026,11,21,3,30,tzinfo=UTC)
MATCH=datetime(2026,11,5,12,tzinfo=UTC)


def fixture(mid:int,*,tour="ATP_MAIN",votes=((1,.75),(1,.70),(None,None)),
            winner=1,manager="NO_BET",reason="AGENT3_ABSTENTION",
            issued=None,result_kind="COMPLETED"):
    issued=issued or MATCH-timedelta(hours=2)
    result_time=MATCH+timedelta(hours=2)
    rows=[]
    for code,(side,prob) in zip(CODES,votes):
        status="PREDICTED" if side is not None else "ABSTAINED"
        rows.append((
            mid,mid+100,issued,MATCH,tour,10,
            code,status,side,prob,issued-timedelta(minutes=1),
            manager,reason,
            1 if manager=="CONSENSUS" else None,
            .75 if manager=="CONSENSUS" else None,
            result_kind,
            winner if result_kind=="COMPLETED" else None,
            result_time if result_kind=="COMPLETED" else None,
        ))
    return rows


def audit(rows):
    return summarize(rows,start=START,end=END,as_of=ASOF)


def test_a3_abstention_does_not_force_loss_or_block_shadow_two_agents():
    result=audit(fixture(1))
    assert result["first_issued_pre_match_cohort"]==1
    assert result["a3_abstained"]==1
    a=result["policies"]["A_unanimous3_floor60"]
    b=result["policies"]["B_guarded2_floor60_spread20"]
    c=result["policies"]["C_uncalibrated_mean_floor60"]
    assert a["issued"]==0
    assert a["wins"]==0 and a["losses"]==0
    assert b["issued"]==1 and b["wins"]==1
    assert c["issued"]==1 and c["wins"]==1
    assert result["full_eligible_fixture_coverage_verified"] is False
    assert result["manager_e_chronological_stacking"].startswith("NOT_ASSESSED")


def test_unanimity_succeeds_only_with_three_active_agreeing_votes():
    r=audit(fixture(1,votes=((1,.81),(1,.76),(1,.67)),
                    manager="CONSENSUS",reason="NONE"))
    assert r["policies"]["A_unanimous3_floor60"]["wins"]==1
    assert r["policies"]["original_manager"]["wins"]==1
    assert r["a3_predicted"]==1


def test_two_disagreeing_agents_plus_a3_abstention_does_not_publish():
    r=audit(fixture(1,votes=((1,.75),(2,.75),(None,None))))
    assert all(r["policies"][name]["issued"]==0 for name in (
        "A_unanimous3_floor60","B_guarded2_floor60_spread20",
        "C_uncalibrated_mean_floor60"
    ))


def test_low_a2_confidence_abstains_guard_but_raw_mean_can_publish():
    r=audit(fixture(1,votes=((1,.85),(1,.55),(None,None))))
    assert r["policies"]["B_guarded2_floor60_spread20"]["issued"]==0
    assert r["policies"]["C_uncalibrated_mean_floor60"]["issued"]==1
    assert r["policies"]["C_uncalibrated_mean_floor60"]["wins"]==1


def test_losses_counted_and_noncompleted_result_never_as_loss():
    cases=fixture(1,winner=2)+fixture(2,result_kind="CANCELLED")
    r=audit(cases)
    a=r["policies"]["C_uncalibrated_mean_floor60"]
    assert a["issued"]==2
    assert a["settled"]==1
    assert a["wins"]==0 and a["losses"]==1
    assert a["ungraded"]==1
    assert r["unsafe_or_ungraded_counts"]["UNSETTLED_OR_VOID_GRADING_NOT_LOSS"]==1


def test_no_retrospectively_late_first_issued_predictions():
    r=audit(fixture(1,issued=MATCH-timedelta(minutes=10)))
    assert r["first_run_candidates"]==1
    assert r["first_issued_pre_match_cohort"]==0
    assert r["unsafe_or_ungraded_counts"]["MISSING_OR_LATE_FIRST_ISSUED_PIT"]==1


def test_raw_probabilities_are_player_side_oriented_before_average():
    active=_available_agents({
        CODES[0]:("PREDICTED",2,.8),  # p1=.2
        CODES[1]:("PREDICTED",2,.6),  # p1=.4
        CODES[2]:("ABSTAINED",None,None),
    })
    c=_policy(active)["C_uncalibrated_mean_floor60"]
    assert c is not None
    assert c[0]==2
    assert c[1]==pytest.approx(.30)


def test_inconsistent_agent_results_are_rejected_and_missing_tracked():
    rows=fixture(1)
    rows[1]=(*rows[1][:6],"AGENT_1_SURFACE_ELO",*rows[1][7:])
    with pytest.raises(ValueError,match="unexpected/duplicate"):
        audit(rows)
    with pytest.raises(ValueError,match="invalid status/side"):
        _available_agents({CODES[0]:("PREDICTED",None,.8)})
    with pytest.raises(ValueError,match="abstaining"):
        _available_agents({CODES[0]:("ABSTAINED",1,.7)})


def test_first_run_pit_future_rejected():
    rows=fixture(1)
    rows[0]=(*rows[0][:10],MATCH+timedelta(minutes=2),*rows[0][11:])
    with pytest.raises(ValueError,match="future prediction feature timestamp"):
        audit(rows)


def test_sources_explicitly_filter_first_completed_experiment_free_runs():
    sql=source_sql()
    assert "DISTINCT ON(t.match_id)" in sql
    assert "pr.run_status='COMPLETED'" in sql
    assert "pr.experiment_id IS NULL" in sql
    assert "mv.ingested_at <= fr.issued_at" in sql
    assert "odds_snapshots" not in sql


def test_empty_migrated_postgres_returns_no_spurious_winners(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        r=run(conn,start=START,end=END,as_of=ASOF)
    assert r["first_run_candidates"]==0
    assert r["policies"]["A_unanimous3_floor60"]["winner_accuracy"] is None
    assert r["policies"]["C_uncalibrated_mean_floor60"]["brier"] is None
    assert not r["production_rules_changed"]


def test_wilson_interval_has_bounded_limits():
    assert _wilson(0,0) is None
    lo,hi=_wilson(2,3)
    assert 0<=lo<=2/3<=hi<=1
