"""Exact frozen research selection: pending, retirements, void, future fixture."""
from datetime import UTC, datetime, timedelta
from sports_edge_tennis.validation.a1a2_frozen_194_ungraded_audit import (
    classify, audit, VERSION,
)
import psycopg
import pytest

ASOF=datetime(2026,11,21,3,30,tzinfo=UTC)
VAL=datetime(2026,10,15,0,tzinfo=UTC)
HOLD=datetime(2026,11,4,0,tzinfo=UTC)
START=datetime(2026,11,15,12,tzinfo=UTC)


def source_row(mid,*,start=START,status=None,winner=None,known=None):
    issue=start-timedelta(hours=8)
    return (
        mid,issue-timedelta(minutes=2),issue-timedelta(minutes=2),
        issue,start,"CHALLENGER","HARD",15,
        1,"0.8",1,"0.8",status,winner,known,
    )


def test_future_and_past_without_result_separate():
    assert classify(status=None,winner_side=None,known_at=None,
        start_at=ASOF+timedelta(hours=1),as_of=ASOF)=="FUTURE_SCHEDULED_AT_REPORT_CUTOFF"
    assert classify(status=None,winner_side=None,known_at=None,
        start_at=ASOF-timedelta(hours=4),as_of=ASOF)=="PAST_START_NO_TERMINAL_RESULT_OBSERVED"


def test_terminal_noncompleted_are_never_silently_losses():
    expected={
        ("CANCELLED",None):"CANCELLED_NO_WINNER",
        ("WALKOVER",None):"WINNERLESS_WALKOVER_EXCLUDED",
        ("WALKOVER",1):"WALKOVER_WINNER_PRESENT_EXCLUDED_BY_COMPLETED_ONLY_RULE",
        ("RETIRED",2):"RETIRED_WITH_WINNER_EXCLUDED_BY_COMPLETED_ONLY_RULE",
    }
    for (status,winner),category in expected.items():
        assert classify(status=status,winner_side=winner,known_at=ASOF,
            start_at=START,as_of=ASOF)==category


def test_completed_outcome_checked_for_known_at():
    assert classify(status="COMPLETED",winner_side=1,known_at=START+timedelta(hours=2),
        start_at=START,as_of=ASOF)=="ALREADY_SETTLED_NOT_UNGRADED"
    assert classify(status="COMPLETED",winner_side=1,known_at=ASOF+timedelta(hours=1),
        start_at=START,as_of=ASOF)=="COMPLETED_BUT_RESULT_TIMESTAMP_UNSAFE"


def test_immutable_original_issued_statuses_reconcile_without_posthoc_edits():
    val=[source_row(i,start=datetime(2026,10,20,tzinfo=UTC)+timedelta(minutes=i),
        status="COMPLETED",winner=1,
        known=datetime(2026,10,21,tzinfo=UTC)+timedelta(minutes=i))
        for i in range(1,25)]
    hold=[
        source_row(101,start=START,status=None),
        source_row(102,start=START,status="RETIRED",winner=2),
        source_row(103,start=START,status="WALKOVER",winner=None),
        source_row(104,start=START,status="COMPLETED",winner=1,
                   known=START+timedelta(hours=4)),
    ]
    report=audit(val+hold,as_of=ASOF,validation_start=VAL,
        holdout_start=HOLD,require_original_counts=False)
    assert report["selected_issued"]==4
    assert report["selected_settled"]==1
    assert report["selected_wins"]==1
    assert report["selected_ungraded"]==3
    assert sum(report["status_reason_counts"].values())==3
    assert len(report["ungraded_original_match_records"])==3
    assert all(row["original_issued_at"]<row["match_start_at"]
            for row in report["ungraded_original_match_records"])
    assert report["all_eligible_fixture_coverage"] is None
    assert report["not_forward_validated"] is True


def test_duplicate_first_paired_rows_fail_closed():
    with pytest.raises(ValueError,match="duplicate"):
        audit([source_row(1),source_row(1)],
             as_of=ASOF,validation_start=VAL,holdout_start=HOLD,
             require_original_counts=False)


def test_missing_validation_is_not_passed_as_frozen_threshold():
    with pytest.raises(ValueError,match="no prior validation"):
        audit([source_row(101)],as_of=ASOF,validation_start=VAL,
              holdout_start=HOLD,require_original_counts=False)


def test_original_counts_required_by_default():
    validation=[source_row(i,start=datetime(2026,10,20,tzinfo=UTC)+timedelta(minutes=i),
        status="COMPLETED",winner=1,
        known=datetime(2026,10,21,tzinfo=UTC)+timedelta(minutes=i))
        for i in range(1,10)]
    with pytest.raises(ValueError,match="not reproduced"):
        audit(validation+[source_row(101)],as_of=ASOF,validation_start=VAL,
            holdout_start=HOLD)


def test_read_only_empty_migrated_db(migrated_postgres):
    from sports_edge_tennis.validation.a1a2_frozen_194_ungraded_audit import run
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        with pytest.raises(ValueError,match="no prior validation"):
            run(conn,as_of=ASOF,validation_start=VAL,holdout_start=HOLD)
