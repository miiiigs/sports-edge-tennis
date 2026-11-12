"""Read-only pending cohort movement cannot be inferred from net totals."""
from datetime import UTC, datetime

import pytest

from ops.brian_pending_transition import _outcome, _snapshot, query_as_of, reconcile


def _record(mid, status="PENDING", day="2026-11-09",
            cohort="A1_A2_WIDE_SPREAD"):
    return {
        "match_id": mid, "status": status, "decision_day": day,
        "pick_source": cohort, "original_aligned": True,
        "aligned_pick_type": cohort,
    }


def test_report_mathematically_reconciles_new_cases_and_exits():
    # 52 -> 43 is a NET CHANGE, not necessarily 9 settled.
    before = {i: _record(i) for i in range(1, 53)}
    after = {i: _record(i) for i in range(1, 40)}
    for i in range(40, 53):
        after[i] = _record(i, status="WON" if i % 2 else "LOST")
    for i in range(53, 57):
        after[i] = _record(i, day="2026-11-11")
    report = reconcile(before, after, expected_before=52, expected_after=43)
    assert report["before_pending"] == 52
    assert report["new_pending"] == 4
    assert report["left_pending"] == 13
    assert report["after_pending"] == 43
    assert report["left_pending_by_reporting_period"] == {"NOV05_NOV09": 13}
    assert {r["match_id"] for r in report["settled_or_void"]} == set(range(40, 53))
    assert {r["match_id"] for r in report["newly_pending"]} == set(range(53, 57))
    with pytest.raises(ValueError, match="after count"):
        reconcile(before, after, expected_before=52, expected_after=42)


def test_original_cohort_period_is_not_assumed_from_net_recovery():
    before = {1: _record(1, day="2026-10-21"),
              2: _record(2, day="2026-11-09"),
              3: _record(3, day="2026-11-08")}
    after = {1: _record(1, "VOID", "2026-10-21"),
             2: _record(2, "WON", "2026-11-09"),
             3: _record(3, "LOST", "2026-11-08"),
             4: _record(4, day="2026-11-10")}
    report = reconcile(before, after)
    assert report["left_pending_by_reporting_period"] == {
        "NOV05_NOV09": 2, "OTHER_DAY": 1,
    }
    assert report["left_pending_by_status"] == {
        "LOST": 1, "VOID": 1, "WON": 1,
    }
    assert len(report["newly_pending"]) == 1


def test_no_pending_removed_without_a_subsequent_terminal_result():
    with pytest.raises(ValueError, match="disappeared"):
        reconcile({1: _record(1)}, {})
    assert _outcome("COMPLETED", 1, 1) == "WON"
    assert _outcome("COMPLETED", 2, 1) == "LOST"
    assert _outcome("WALKOVER", 1, 1) == "VOID"
    assert _outcome(None, None, 1) == "PENDING"


def test_sql_excludes_future_picks_results_and_fixture_ingestions():
    sql = query_as_of()
    for q in (
        "p.picked_at <= %(cutoff)s",
        "mro.source_observed_at <= %(cutoff)s",
        "mro.ingested_at <= %(cutoff)s",
        "mv.ingested_at <= %(cutoff)s",
        "a.picked_at <= %(cutoff)s",
    ):
        assert q in sql
    assert "INSERT INTO" not in sql
    assert "UPDATE " not in sql


def test_snapshot_rejects_post_cutoff_observation_leakage():
    cutoff = datetime(2026, 11, 10, 12, tzinfo=UTC)
    row = (
        1, cutoff, "MANAGER_CONSENSUS", 1, None, None, "SCHEDULED",
        None, None, "COMPLETED", 1, cutoff, cutoff,
        True, "MANAGER_CONSENSUS",
    )
    item = _snapshot([row], cutoff)[1]
    assert item["status"] == "WON"
    assert item["original_aligned"]
    late = list(row)
    late[12] = datetime(2026, 11, 10, 14, tzinfo=UTC)
    with pytest.raises(ValueError, match="post-cutoff"):
        _snapshot([late], cutoff)
