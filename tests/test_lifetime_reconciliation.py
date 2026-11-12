"""All-history reconciliation: no disappearing picks or inferred voids."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from sports_edge_tennis.dashboard.lifetime_reconciliation import (
    _state, parse, summarize, pending_dashboard_audit,
    compare_all_history_with_existing_daily, query,
)

ASOF=datetime(2026,11,12,6,tzinfo=UTC)
ISSUED=datetime(2026,9,13,5,tzinfo=UTC)
ORIGINAL=datetime(2026,9,14,5,tzinfo=UTC)
OLD=datetime(2026,9,14,8,tzinfo=UTC)
FUTURE=ASOF+timedelta(days=3)


def _row(mid, *, issued=ISSUED, original=ORIGINAL, latest=OLD,
         fixture="SCHEDULED", terminal=None, winner=None,
         prob="0.73", fixture_seen=None, result_seen=None,
         pit_ingested=None, mapped=True):
    seen=fixture_seen if fixture_seen is not None else (
        ASOF-timedelta(days=60)
    )
    result_time=result_seen if result_seen is not None else (
        ASOF-timedelta(hours=3)
    )
    return (
        mid,issued,1,prob,"MANAGER_CONSENSUS",
        original,31,32,
        pit_ingested or issued-timedelta(minutes=8),
        issued-timedelta(minutes=10),
        latest,fixture,seen,seen,
        terminal,winner,
        result_time if terminal else None,
        result_time if terminal else None,
        80000+mid if terminal else None,
        "Player A","Player B",mapped,
    )


def test_all_history_never_reinterprets_expired_pending_as_void():
    rows=[
        _row(11,fixture="CANCELLED",terminal=None),
        _row(12,fixture="CANCELLED",terminal="CANCELLED"),
        _row(13,fixture="WALKOVER",terminal=None),
        _row(14,fixture="WALKOVER",terminal="WALKOVER"),
        _row(15,terminal="COMPLETED",winner=1),
        _row(16,terminal="COMPLETED",winner=2,prob="0.65"),
        _row(17,latest=FUTURE,fixture_seen=ASOF-timedelta(days=30)),
    ]
    data=parse(rows,as_of=ASOF)
    assert [x["status"] for x in data]==[
        "PENDING","VOID","PENDING","VOID","WON","LOST","PENDING",
    ]
    assert data[0]["pending_reason"]=="TERMINAL_NO_USABLE_RESULT"
    assert data[2]["pending_reason"]=="TERMINAL_NO_USABLE_RESULT"
    assert data[6]["pending_reason"]=="STALE_FUTURE_SCHEDULE_RECHECK"
    report=summarize(data,as_of=ASOF)
    assert report["original"]["selections"]==7
    assert report["original"]["won"]==1
    assert report["original"]["lost"]==1
    assert report["original"]["void"]==2
    assert report["original"]["pending"]==3
    assert report["total_historical_pending"]==3
    assert report["pending_needing_operator_attention"]==3
    assert report["starts_at"]=="2026-09-14"
    assert report["all_days"][0]["original"]["selections"]==7
    assert report["all_days"][1]["original"]["selections"]==0
    assert report["original"]["settled_accuracy"]==pytest.approx(.5)
    pending=pending_dashboard_audit(report)
    assert pending["total"]==3
    assert pending["by_age"]["GT_72H"]==2
    assert pending["by_age"]["NOT_STARTED"]==1
    assert pending["by_flag"]["OUTSIDE_RECOVERY_WINDOW"]==2


def test_match_older_than_90_days_remains_accounted_for():
    old=ASOF-timedelta(days=180)
    issue=old-timedelta(hours=12)
    records=parse([_row(21,issued=issue,original=old,
                       latest=old,fixture="SCHEDULED",
                       fixture_seen=issue)],as_of=ASOF)
    report=summarize(records,as_of=ASOF)
    assert report["total_historical_pending"]==1
    assert report["unresolved_matches"][0]["match_id"]==21
    assert "LONG_STANDING_UNRESOLVED_OVER_30D" in (
        report["unresolved_matches"][0]["data_quality_flags"]
    )


def test_late_provider_data_or_duplicate_pick_fails():
    base=_row(22)
    with pytest.raises(ValueError,match="duplicate"):
        parse([base,base],as_of=ASOF)
    with pytest.raises(ValueError,match="ahead of audit cutoff"):
        parse([_row(23,terminal="COMPLETED",winner=1,
                    result_seen=ASOF+timedelta(minutes=5))],as_of=ASOF)


def test_every_prior_day_compared_against_legacy_counts():
    row=parse([_row(31)],as_of=ASOF)
    report=summarize(row,as_of=ASOF)
    old=SimpleNamespace(
        day=date(2026,9,14),original=1,original_won=0,
        original_lost=0,original_void=0,original_pending=1,
        qualified=1,qualified_won=0,qualified_lost=0,
        qualified_void=0,qualified_pending=1,
    )
    good=compare_all_history_with_existing_daily(report,[old],as_of=ASOF)
    assert good["legacy_daily_reconciliation"]["status"]=="PASS"
    wrong=SimpleNamespace(**{**vars(old),"original_void":1,
                             "original_pending":0})
    bad=compare_all_history_with_existing_daily(report,[wrong],as_of=ASOF)
    assert bad["status"]=="BLOCKED_LEGACY_DAILY_MISMATCH"
    assert bad["legacy_daily_reconciliation"]["discrepancies"][0]["day"]=="2026-09-14"


def test_historical_query_is_not_rolling_60_or_90_day_cutoff():
    sql=query()
    assert "FROM aligned_model_picks p" in sql
    assert "p.picked_at <= %(as_of)s" in sql
    assert "p.picked_at >= " not in sql
    assert "mro.ingested_at <= %(as_of)s" in sql
    assert "mv.ingested_at <= p.picked_at" in sql
    assert "mv.ingested_at <= %(as_of)s" in sql
    assert "INSERT INTO" not in sql and "UPDATE " not in sql
    assert _state("CANCELLED",None,1)=="VOID"
    assert _state(None,None,1)=="PENDING"
