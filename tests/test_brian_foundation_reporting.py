"""Protect three reporting windows and November 9 selection identity accounting."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from sports_edge_tennis.dashboard.qualified_history import ComparisonDay
from ops.brian_three_window_report import report_windows
from ops.brian_nov09_selection_audit import audit, classify


OBSERVED = {
    "2026-11-05": ((19, 12, 7, 0, 0), (6, 4, 2, 0, 0)),
    "2026-11-06": ((8, 6, 2, 0, 0), (2, 1, 1, 0, 0)),
    "2026-11-07": ((10, 5, 4, 1, 0), (5, 4, 1, 0, 0)),
    "2026-11-08": ((26, 14, 11, 0, 1), (15, 9, 5, 0, 1)),
    "2026-11-09": ((61, 32, 21, 4, 4), (28, 17, 7, 3, 1)),
    "2026-11-10": ((22, 17, 4, 1, 0), (8, 7, 1, 0, 0)),
}


def _days():
    res = []
    d = date(2026, 10, 27)
    end = date(2026, 11, 10)
    while d <= end:
        o, f = OBSERVED.get(d.isoformat(), ((0,)*5, (0,)*5))
        res.append(ComparisonDay(
            day=d, original=o[0], original_won=o[1],
            original_lost=o[2], original_void=o[3],
            original_pending=o[4], qualified=f[0],
            qualified_won=f[1], qualified_lost=f[2],
            qualified_void=f[3], qualified_pending=f[4],
            avoided_losses=o[2]-f[2],
            missed_winners=o[1]-f[1], complete=True,
        ))
        d += timedelta(days=1)
    return res


def _manager():
    known = {
        "2026-11-05": (10, 3, 7, 0, 0),
        "2026-11-06": (6, 4, 2, 0, 0),
        "2026-11-07": (7, 4, 2, 1, 0),
        "2026-11-08": (17, 11, 6, 0, 0),
        "2026-11-09": (29, 13, 12, 2, 2),
        "2026-11-10": (8, 6, 2, 0, 0),
    }
    out = []
    for d in _days():
        p,w,l,v,n = known.get(d.day.isoformat(), (0,0,0,0,0))
        out.append(SimpleNamespace(
            decision_date=d.day, picks=p, wins=w,
            losses=l, voids=v, pending=n,
        ))
    return out


def test_fixed_and_rolling_never_silently_change_reporting_windows():
    report = report_windows(
        _days(), _manager(), as_of_date=date(2026, 11, 11),
        source_refresh_at=datetime(2026,11,11,9,tzinfo=UTC),
    )
    f = report["periods"]["fixed_nov05_nov09"]
    r = report["periods"]["rolling_five_complete_dates"]
    c = report["periods"]["cumulative_since_2026_10_27"]
    assert (f["start"], f["end"]) == ("2026-11-05","2026-11-09")
    assert (r["start"], r["end"]) == ("2026-11-06","2026-11-10")
    assert c["start"] == "2026-10-27"
    assert f["original"] == {
        "picks": 124, "won":69, "lost":45,"void":5,"pending":5,
        "graded":114,"settled_accuracy":pytest.approx(69/114),
    }
    assert f["existing_qualified_ge70"]["picks"] == 56
    assert f["existing_qualified_ge70"]["won"] == 35
    assert f["existing_qualified_ge70"]["lost"] == 16
    assert f["existing_qualified_ge70"]["void"] == 3
    assert f["existing_qualified_ge70"]["pending"] == 2
    assert f["existing_qualified_ge70"]["settled_accuracy"] == pytest.approx(35/51)
    assert f["status"] == "PROVISIONAL"
    assert r["original"]["picks"] == 127
    assert r["original"]["won"] == 74
    assert r["original"]["lost"] == 42
    assert r["existing_qualified_ge70"]["picks"] == 58
    assert r["existing_qualified_ge70"]["won"] == 38
    assert r["existing_qualified_ge70"]["lost"] == 15
    assert r["existing_qualified_ge70"]["coverage"] == pytest.approx(58/127)
    # Synthetic October zero rows must not be presented as an actual
    # cumulative production report. This only checks period geometry.
    assert c["start"] == "2026-10-27"
    assert report["a3_a4_forward_evidence"] == "NOT_AVAILABLE"


def test_missing_days_or_rewritten_subsets_block_reporting():
    data = _days()
    with pytest.raises(ValueError, match="missing daily"):
        report_windows(
            data[:-1], _manager(), as_of_date=date(2026,11,11),
            source_refresh_at=datetime(2026,11,11,tzinfo=UTC),
        )
    altered = list(data)
    last = altered[-1]
    altered[-1] = ComparisonDay(**{
        **vars(last), "qualified_won": 55,
    })
    with pytest.raises(ValueError):
        report_windows(
            altered, _manager(), as_of_date=date(2026,11,11),
            source_refresh_at=datetime(2026,11,11,tzinfo=UTC),
        )


def test_nov9_28_never_26_or_25_and_all_status_transitions_accounted():
    t1=datetime(2026,11,10,14,tzinfo=UTC)
    t2=datetime(2026,11,11,9,tzinfo=UTC)
    before = {}
    after = {}
    for mid in range(1,29):
        outcome1 = ("WON" if mid <= 16 else
                    "LOST" if mid <=21 else
                    "VOID" if mid <=23 else "PENDING")
        outcome2 = ("WON" if mid <= 17 else
                    "LOST" if mid <=24 else
                    "VOID" if mid <=27 else "PENDING")
        base={
            "match_id":mid, "players":["A","B"],
            "qualified_ge70": True,"selected_side":1,
            "original_issue_at":"2026-11-08T09:00:00+00:00",
            "issued_before_kickoff": True,
        }
        before[mid]={**base,"status":outcome1}
        after[mid]={**base,"status":outcome2}
    data=audit(before,after,before_time=t1,after_time=t2)
    assert data["qualified_before"]["selections"] == 28
    assert data["qualified_after"]["selections"] == 28
    assert data["qualified_before"]["void"] == 2
    assert data["qualified_after"]["void"] == 3
    assert data["qualified_after"]["won"] == 17
    assert data["qualified_after"]["lost"] == 7
    assert data["qualified_after"]["pending"] == 1
    with pytest.raises(ValueError, match="population changed"):
        audit(before,dict(list(after.items())[1:]),before_time=t1,after_time=t2)


def test_production_equivalent_noncompletions_remain_void_or_pending():
    assert classify("COMPLETED",1,1) == "WON"
    assert classify("COMPLETED",2,1) == "LOST"
    assert classify("WALKOVER",None,1) == "VOID"
    assert classify("RETIRED",2,1) == "VOID"
    assert classify("CANCELLED",None,1) == "VOID"
    assert classify(None,None,1) == "PENDING"
