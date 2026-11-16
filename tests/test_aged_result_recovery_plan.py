"""Proof that the >=3-day backlog cannot quietly fall off the tracker."""
from datetime import UTC, date, datetime, timedelta

import pytest

from sports_edge_tennis.live.aged_result_recovery_plan import (
    MAX_REQUESTS_PER_DAY, MAX_TARGETS_PER_REQUEST, plan_aged_recovery,
)
from sports_edge_tennis.live.pending_result_diagnostics import (
    PendingPickDiagnosis, PendingPickEvidence, PendingResultDiagnosticsReport,
)
from sports_edge_tennis.live.pending_result_refresh import refresh_pending_results


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)


def _case(mid, days_ago, reason="PROVIDER_FIXTURE_NOT_REOBSERVED"):
    start = NOW - timedelta(days=days_ago)
    return PendingPickDiagnosis(
        evidence=PendingPickEvidence(
            match_id=mid, pick_source="MANAGER_CONSENSUS",
            picked_at=NOW-timedelta(days=100), selected_side=1,
            has_type_a_bet=False, pit_match_start_at=start,
            latest_match_start_at=start,
            latest_match_status="SCHEDULED",
            last_fixture_observed_at=NOW-timedelta(days=2),
            fixture_versions=2, source_systems=("API_TENNIS",),
            api_tennis_event_key=str(mid), tournament="Test", competition_tier="CHALLENGER",
            player_1="A", player_2="B",
        ),
        reason=reason, flags=(),
        age_bucket="GT_72H" if days_ago>=3 else "LT_6H",
        hours_since_start=24*days_ago,
        hours_since_last_observed=48,
    )


def _report(*items):
    return PendingResultDiagnosticsReport(
        generated_at=NOW, recovery_window_start=date(2026, 11, 10),
        recovery_window_end=date(2026, 11, 12), items=items,
    )


def test_postponed_old_fixture_stays_eligible_for_bounded_probe():
    report=_report(
        _case(101, 4), _case(102, 15, "TERMINAL_FIXTURE_WITHOUT_RESULT"),
        _case(103, -1, "NOT_STARTED"),
        _case(104, 91),
    )
    planned=plan_aged_recovery(report, as_of=NOW)
    assert planned.provider_request_budget == 1
    assert len(planned.selected_match_ids) == 1
    assert planned.selected_match_ids[0] in (101,102)
    assert 102 in planned.manual_review_match_ids
    assert 104 in planned.manual_review_match_ids
    assert planned.skipped_future == 1
    assert planned.skipped_outside_90_days == 1
    assert planned.log_line().startswith("PENDING_AGED_RECOVERY_PLAN")


def test_stale_cases_rotate_between_distinct_observed_utc_days():
    report=_report(_case(1001, 4), _case(1002, 11))
    chosen={plan_aged_recovery(report,as_of=NOW+timedelta(days=d)).selected_day
            for d in range(2)}
    assert len(chosen)==2


def test_bounded_batch_and_duplicate_identity_rejected():
    report=_report(*[_case(i,4) for i in range(100,121)])
    planned=plan_aged_recovery(report,as_of=NOW)
    assert len(planned.selected_match_ids)==MAX_TARGETS_PER_REQUEST
    assert planned.provider_request_budget==MAX_REQUESTS_PER_DAY
    with pytest.raises(ValueError,match="duplicate"):
        plan_aged_recovery(_report(_case(1,4),_case(1,4)),as_of=NOW)


def test_older_result_probe_requires_explicit_dry_run_and_strict_limits(monkeypatch):
    # Validate guard before any provider fetch, even with a fake DB.
    called=[]
    def fake_load(conn, **kwargs):
        called.append(kwargs)
        return ()
    monkeypatch.setattr(
        "sports_edge_tennis.live.pending_result_refresh.load_pending_model_pick_targets",
        fake_load,
    )
    start=(NOW-timedelta(days=6)).date()
    with pytest.raises(ValueError,match="dry-run"):
        refresh_pending_results(
            object(), api_key="secret", as_of=NOW, dry_run=False,
            explicit_window=(start,start), max_targets=10,
        )
    with pytest.raises(ValueError,match="10 targets"):
        refresh_pending_results(
            object(), api_key="secret", as_of=NOW, dry_run=True,
            explicit_window=(start,start), max_targets=11,
        )
    with pytest.raises(ValueError,match="90-day"):
        old=(NOW-timedelta(days=100)).date()
        refresh_pending_results(
            object(), api_key="secret", as_of=NOW, dry_run=True,
            explicit_window=(old,old), max_targets=10,
        )
    summary=refresh_pending_results(
        object(), api_key=None, as_of=NOW, dry_run=True,
        explicit_window=(start,start), max_targets=10,
    )
    assert summary.checked==0
    assert called and called[-1]["window_start"]==start
    assert called[-1]["window_end"]==start


def test_busy_historical_day_rotates_unresolved_ids_instead_of_starving():
    report=_report(*[_case(i,4) for i in range(100,121)])
    first=set(plan_aged_recovery(report,as_of=NOW).selected_match_ids)
    second=set(plan_aged_recovery(
        report,as_of=NOW+timedelta(days=1)
    ).selected_match_ids)
    third=set(plan_aged_recovery(
        report,as_of=NOW+timedelta(days=2)
    ).selected_match_ids)
    assert len(first)==len(second)==len(third)==10
    assert len(first | second | third)==21
