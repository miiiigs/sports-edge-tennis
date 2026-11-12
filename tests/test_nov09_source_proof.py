"""Match-ID source reconciliation must block missing winners/voids."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from sports_edge_tennis.dashboard.nov09_source_proof import (
    DAY, _state, verify_nov9_rows,
)
from sports_edge_tennis.dashboard.qualified_history import ComparisonDay


NOW=datetime(2026,11,12,7,tzinfo=UTC)
ISSUED=datetime(2026,11,8,7,tzinfo=UTC)
KICKOFF=datetime(2026,11,9,8,tzinfo=UTC)


def _pick(mid,prob="0.73"):
    return SimpleNamespace(
        match_id=mid,selected_side=1,
        picked_at=ISSUED, match_start_at=KICKOFF,
        aligned_probability=Decimal(prob),
        aligned_pick_type="MANAGER_CONSENSUS",
    )


def _result(mid,status=None,winner=None,*,observed=None):
    seen=observed if observed is not None else NOW-timedelta(hours=1)
    return (mid,"Player A","Player B",status,winner,
            seen if status else None,seen if status else None,
            seen if status else None,"SCHEDULED",KICKOFF,seen)


def _day():
    return ComparisonDay(
        day=DAY, original=4, original_won=1, original_lost=1,
        original_void=1,original_pending=1,
        qualified=2,qualified_won=1,qualified_lost=0,
        qualified_void=1,qualified_pending=0,
        avoided_losses=1, missed_winners=0,complete=True,
    )


def test_all_first_issued_match_ids_reconcile_to_daily_and_void():
    picks=[_pick(1),_pick(2,"0.61"),_pick(3),_pick(4,"0.60")]
    results={1:_result(1,"COMPLETED",1),
             2:_result(2,"COMPLETED",2),
             3:_result(3,"WALKOVER",None),
             4:_result(4)}
    report=verify_nov9_rows(picks,results,_day(),as_of=NOW)
    assert report["original_total"]==4
    assert report["qualified_ge70_total"]==2
    assert report["original_counts"]["VOID"]==1
    assert report["qualified_counts"]["VOID"]==1
    assert [r["match_id"] for r in report["records"]]==[1,2,3,4]
    assert report["pit_unverified_match_ids"]==[]


def test_duplicate_missing_void_and_post_cutoff_result_fail_closed():
    picks=[_pick(1),_pick(2,"0.61"),_pick(3),_pick(4,"0.60")]
    results={1:_result(1,"COMPLETED",1),
             2:_result(2,"COMPLETED",2),
             3:_result(3,"WALKOVER",None),
             4:_result(4)}
    with pytest.raises(ValueError,match="duplicate"):
        verify_nov9_rows([*picks,picks[0]],results,_day(),as_of=NOW)
    with pytest.raises(ValueError,match="missing"):
        verify_nov9_rows(picks,{k:v for k,v in results.items() if k!=3},
                         _day(),as_of=NOW)
    altered=dict(results)
    altered[3]=_result(3,None,None)
    with pytest.raises(ValueError,match="Nov9 original"):
        verify_nov9_rows(picks,altered,_day(),as_of=NOW)
    late=dict(results)
    late[1]=_result(1,"COMPLETED",1,observed=NOW+timedelta(minutes=1))
    with pytest.raises(ValueError,match="future observation"):
        verify_nov9_rows(picks,late,_day(),as_of=NOW)


def test_missing_pit_match_start_explicitly_unverified():
    picks=[_pick(1),_pick(2,"0.61"),_pick(3),_pick(4,"0.60")]
    picks[0].match_start_at=None
    result={1:_result(1,"COMPLETED",1),
            2:_result(2,"COMPLETED",2),
            3:_result(3,"CANCELLED",None),
            4:_result(4)}
    report=verify_nov9_rows(picks,result,_day(),as_of=NOW)
    assert report["status"]=="PIT_REVIEW_REQUIRED"
    assert report["pit_unverified_match_ids"]==[1]


def test_non_completed_terminal_never_inferred_winner():
    assert _state("RETIRED",1,1)=="VOID"
    assert _state("WALKOVER",2,1)=="VOID"
    assert _state("CANCELLED",None,1)=="VOID"
    assert _state("COMPLETED",None,1)=="PENDING"
