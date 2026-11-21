"""Counterfactual false-veto audit regression: admitted losses matter too."""
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.manager_false_veto_first_issue import (
    summarize, run,
)
from sports_edge_tennis.validation.manager_abc_first_issue_ablation import CODES

START=datetime(2026,10,15,tzinfo=UTC)
END=datetime(2026,11,21,tzinfo=UTC)
ASOF=datetime(2026,11,21,3,30,tzinfo=UTC)
MATCH=datetime(2026,11,5,12,tzinfo=UTC)


def fixture(mid:int, *, winner=1, tour="ATP_MAIN",
            votes=((1,.78),(1,.71),(None,None)),
            original=False,manager_side=1,
            reason="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
            result_kind="COMPLETED",issue_minutes=90):
    issued=MATCH-timedelta(minutes=issue_minutes)
    rows=[]
    for code,(side,prob) in zip(CODES,votes):
        rows.append((
            mid,mid+100,issued,MATCH,tour,10,
            code,"PREDICTED" if side is not None else "ABSTAINED",
            side,prob,issued-timedelta(minutes=1),
            "CONSENSUS" if original else "NO_BET",
            "NONE" if original else reason,
            manager_side if original else None,
            .78 if original else None,
            result_kind,
            winner if result_kind=="COMPLETED" else None,
            MATCH+timedelta(hours=2) if result_kind=="COMPLETED" else None,
        ))
    return rows


def audit(rows):
    return summarize(rows,start=START,end=END,as_of=ASOF)


def test_counterfactual_keeps_missed_winners_losses_and_pending():
    rows=(
        fixture(1,winner=1)+
        fixture(2,winner=2)+
        fixture(3,result_kind="WALKOVER")
    )
    r=audit(rows)
    b=r["counterfactuals"]["B"]
    assert r["original_unique_first_run_matches"]==3
    assert r["original_manager_rejected"]==3
    admitted=b["alternative_admits_original_rejections"]
    assert admitted["issued"]==3
    assert admitted["settled"]==2
    assert admitted["wins"]==1 and admitted["losses"]==1
    assert admitted["ungraded"]==1
    assert b["both_select_same_match"]==0
    assert b["admitted_rejected_by_A3_status"]["ABSTAINED"]["issued"]==3
    assert b["admitted_rejected_by_original_reason"][
        "AGENT3_ABSTENTION_SPREAD_EXCEEDED"]["wins"]==1
    assert r["market_value_gate_used"] is False
    assert r["db_writes"] is False


def test_removed_original_winners_and_losses_not_hidden():
    # Original Manager issued, but B rejects because A1 vs A2 disagree;
    # C may also abstain if the averaged forecast is 50:50.
    rows=(
        fixture(1,votes=((1,.8),(2,.8),(None,None)),original=True,winner=1)+
        fixture(2,votes=((1,.8),(2,.8),(None,None)),original=True,winner=2)
    )
    r=audit(rows)
    b=r["counterfactuals"]["B"]
    removed=b["original_manager_selections_removed_by_alternative"]
    assert removed["issued"]==2
    assert removed["wins"]==1 and removed["losses"]==1
    assert r["original_manager_consensus"]["settled"]==2
    assert b["alternative_total"]["issued"]==0


def test_same_match_true_discordance_has_no_double_counting():
    rows=(
        fixture(1,original=True,winner=1)+
        fixture(2,original=True,winner=2)+
        fixture(3,original=True,winner=2,
                manager_side=2,votes=((1,.78),(1,.71),(None,None)))
    )
    a=audit(rows)["counterfactuals"]["B"]
    assert a["both_select_same_match"]==3
    assert a["same_match_both_settled"]==3
    assert a["on_shared_settled"]=={
        "both_correct":1,"both_wrong":1,"only_experiment_correct":1
    }
    assert a["different_side_on_shared_settled"]==1
    assert a["alternative_admits_original_rejections"]["issued"]==0


def test_count_new_match_once_despite_three_agent_rows():
    a=audit(fixture(1))
    assert a["original_unique_first_run_matches"]==1
    assert a["counterfactuals"]["B"]["alternative_total"]["issued"]==1


def test_no_late_original_and_no_future_agent_issue():
    r=audit(fixture(1,issue_minutes=1))
    assert r["original_unique_first_run_matches"]==0
    assert r["exclusions"]["NOT_SAFE_FIRST_PREMATCH_ISSUE"]==1
    rows=fixture(3)
    rows[0]=(*rows[0][:10],rows[0][2]+timedelta(minutes=2),*rows[0][11:])
    with pytest.raises(ValueError,match="future agent prediction"):
        audit(rows)


def test_duplicate_agent_and_changed_manager_rejected():
    rows=fixture(1)
    rows[1]=(*rows[1][:6],CODES[0],*rows[1][7:])
    with pytest.raises(ValueError,match="duplicate/unexpected"):
        audit(rows)
    rows=fixture(1)
    rows[0]=(*rows[0][:11],"CONSENSUS",*rows[0][12:])
    with pytest.raises(ValueError,match="mixed first-run"):
        audit(rows)


def test_no_ungraded_cancelled_match_is_retroactive_win_or_loss():
    r=audit(fixture(1,winner=None,result_kind="CANCELLED"))
    out=r["counterfactuals"]["B"]["alternative_total"]
    assert out["issued"]==1
    assert out["settled"]==0
    assert out["wins"]==0 and out["losses"]==0
    assert out["ungraded"]==1
    assert out["accuracy"] is None


def test_tour_split_no_missing_agent3_vote_assumed():
    r=audit(
        fixture(1,tour="ATP_MAIN")+
        fixture(2,tour="CHALLENGER",result_kind="VOIDED")
    )
    assert r["by_tour"]["ATP_MAIN"]["original_first_run_matches"]==1
    assert r["by_tour"]["CHALLENGER"]["original_first_run_matches"]==1
    assert r["by_tour"]["CHALLENGER"]["safely_settled"]==0
    assert r["a3_status_distribution"]=={"ABSTAINED":2}


def test_empty_migrated_postgres_returns_no_fictitious_counterfactual(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        out=run(conn,start=START,end=END,as_of=ASOF)
    assert out["original_unique_first_run_matches"]==0
    assert out["counterfactuals"]["B"]["alternative_total"]["issued"]==0
    assert out["counterfactuals"]["C"]["alternative_total"]["wins"]==0
    assert out["official_eligible_fixture_denominator"] is False
