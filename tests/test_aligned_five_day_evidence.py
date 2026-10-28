"""Whole-aligned acceptance must never be replaced with Manager-only denominators."""
from datetime import UTC, date, datetime, timedelta

from sports_edge_tennis.dashboard.model_pick_history import DailyModelPickSummary
from sports_edge_tennis.performance.aligned_five_day_evidence import (
    WINDOW_START, AlignedDailyEvidence, from_dashboard_rows,
    render_aligned_five_day_evidence, load_aligned_five_day_evidence,
)


def day(d, wins, losses, void=0, pending=0):
    n=wins+losses+void+pending
    return DailyModelPickSummary(d,n,wins+losses,pending,void,wins,losses,
                                 wins/(wins+losses) if wins+losses else None)


def test_all_five_days_require_full_settlement_and_actual_73():
    rows=tuple(day(WINDOW_START + timedelta(days=i),8,2) for i in range(5))
    report=from_dashboard_rows(rows,as_of=datetime(2026,11,2,2,tzinfo=UTC))
    assert report.status=="MET_FIVE_COMPLETE_DAYS"
    assert all(x.status=="MET_73" for x in report.days)
    assert "scope=ALL_ORIGINAL_ALIGNED_MODEL_PICKS" in render_aligned_five_day_evidence(report)[-1]
    partial=from_dashboard_rows(rows,as_of=datetime(2026,11,1,2,tzinfo=UTC))
    assert partial.status=="NOT_YET_MET"
    assert partial.days[-1].status=="IN_PROGRESS"


def test_pending_and_missing_days_fail_closed():
    rows=(day(WINDOW_START,3,0,pending=1),
          day(date(2026,10,29),8,3))
    report=from_dashboard_rows(rows,as_of=datetime(2026,11,2,2,tzinfo=UTC))
    assert report.days[0].status=="UNSETTLED"
    assert report.days[1].status=="BELOW_73"
    assert report.days[2].status=="NO_PICKS"
    assert report.status=="NOT_YET_MET"


def test_rounding_and_invalid_result_rows_cannot_fake_target():
    v=AlignedDailyEvidence(WINDOW_START,11,8,3,0,0,True)
    assert v.status=="BELOW_73"
    assert v.accuracy_pct is not None and 72<v.accuracy_pct<73
    broken=AlignedDailyEvidence(WINDOW_START,11,9,1,0,0,True)
    assert broken.status=="INCONSISTENT"


def test_pit_results_query_compiles_in_postgres(db):
    # No writes, schema-resolved canonical aligned CTE.
    evidence=load_aligned_five_day_evidence(
        db, as_of=datetime(2026,10,28,2,tzinfo=UTC)
    )
    assert len(evidence.days)==5
    assert evidence.days[0].status=="IN_PROGRESS"
    assert evidence.days[-1].status=="IN_PROGRESS"


def test_after_initial_five_dates_roll_without_skipping_bad_days():
    rows=tuple(day(date(2026,10,28)+timedelta(days=i),8,2) for i in range(6))
    report=from_dashboard_rows(rows,as_of=datetime(2026,11,3,2,tzinfo=UTC))
    assert report.start==date(2026,10,29)
    assert report.end==date(2026,11,2)
    assert report.status=="MET_FIVE_COMPLETE_DAYS"
    missing=tuple(x for x in rows if x.decision_date!=date(2026,10,30))
    blocked=from_dashboard_rows(missing,as_of=datetime(2026,11,3,2,tzinfo=UTC))
    assert blocked.status=="NOT_YET_MET"
    assert any(x.status=="NO_PICKS" for x in blocked.days)


def test_rolling_window_uses_manila_midnight_not_utc():
    rows=tuple(day(date(2026,10,28)+timedelta(days=i),8,2) for i in range(6))
    after=from_dashboard_rows(rows,as_of=datetime(2026,11,2,17,tzinfo=UTC))
    assert after.start==date(2026,10,29)
    before=from_dashboard_rows(rows,as_of=datetime(2026,11,2,15,59,tzinfo=UTC))
    assert before.start==date(2026,10,28)
