"""Tests for safe pre-issue OddsPapi acquisition ordering."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sports_edge_tennis.live.oddspapi_budget import OddspapiBudgetPolicy
from sports_edge_tennis.live.oddspapi_ledger import OddspapiAttemptCap
from sports_edge_tennis.live.preissue_acquisition import (
    AcquisitionStatus,
    PreIssueFixture,
    executable_items,
    execute_preissue_acquisition,
    plan_preissue_acquisition,
)

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def _fixture(match_id: int, *, start_in=timedelta(hours=6),
             issue_in=timedelta(hours=5), cached=False,
             tournament=10) -> PreIssueFixture:
    return PreIssueFixture(
        match_id=match_id, tournament_id=tournament,
        scheduled_start=NOW + start_in, first_issue_at=NOW + issue_in,
        has_cached_identity=cached,
    )


def _by_id(plan):
    return {item.fixture.match_id: item for item in plan.items}


def test_orders_by_earliest_deadline_not_cached_identity():
    fixtures = [
        _fixture(1, start_in=timedelta(hours=8), issue_in=timedelta(hours=7), cached=True),
        _fixture(2, start_in=timedelta(hours=4), issue_in=timedelta(hours=3), cached=False),
    ]
    plan = plan_preissue_acquisition(
        fixtures, now=NOW, policy=OddspapiBudgetPolicy(per_cycle_limit=20))
    ready = [i.fixture.match_id for i in executable_items(plan)]
    assert ready == [2, 1]


def test_forecast_already_issued_is_not_scheduled():
    fixture = _fixture(1, issue_in=timedelta(minutes=-1))
    plan = plan_preissue_acquisition([fixture], now=NOW)
    item = _by_id(plan)[1]
    assert item.status == AcquisitionStatus.FORECAST_ALREADY_ISSUED


def test_safe_deadline_passed_is_too_late():
    fixture = _fixture(1, start_in=timedelta(minutes=20), issue_in=timedelta(minutes=10))
    plan = plan_preissue_acquisition([fixture], now=NOW)
    assert _by_id(plan)[1].status == AcquisitionStatus.TOO_LATE


def test_outside_horizon_is_not_scheduled():
    fixture = _fixture(1, start_in=timedelta(days=9), issue_in=timedelta(days=8))
    plan = plan_preissue_acquisition([fixture], now=NOW)
    assert _by_id(plan)[1].status == AcquisitionStatus.OUTSIDE_HORIZON


def test_missing_entitlement_blocks_all_provider_work():
    plan = plan_preissue_acquisition(
        [_fixture(1)], now=NOW, entitled=False)
    assert _by_id(plan)[1].status == AcquisitionStatus.NOT_ENTITLED
    assert executable_items(plan) == ()


def test_exhausted_monthly_allowance_blocks_all_provider_work():
    plan = plan_preissue_acquisition(
        [_fixture(1)], now=NOW, monthly_remaining=0)
    assert _by_id(plan)[1].status == AcquisitionStatus.MONTHLY_ALLOWANCE_EXHAUSTED
    assert executable_items(plan) == ()


def test_per_cycle_budget_defers_later_items():
    fixtures = [_fixture(i, start_in=timedelta(hours=2 + i),
                         issue_in=timedelta(hours=1 + i), tournament=i)
                for i in range(1, 8)]
    plan = plan_preissue_acquisition(
        fixtures, now=NOW, books=("pinnacle+30",),
        policy=OddspapiBudgetPolicy(per_cycle_limit=3, tournaments_per_call=1))
    ready = executable_items(plan)
    deferred = [i for i in plan.items if i.status == AcquisitionStatus.BUDGET_DEFERRED]
    assert ready
    assert deferred
    assert len(ready) + len(deferred) == len(fixtures)
    assert plan.estimated_requests <= 3


def test_executor_captures_prices_and_never_raises():
    plan = plan_preissue_acquisition([_fixture(1)], now=NOW)
    results = execute_preissue_acquisition(
        plan,
        resolve_identity=lambda mid: {"event": mid},
        fetch_prices=lambda mid, identity: [("PLAYER_1", 2.0), ("PLAYER_2", 1.8)],
        clock=lambda: NOW,
    )
    assert results[0].status == AcquisitionStatus.CAPTURED
    assert len(results[0].prices) == 2


def test_executor_downgrades_identity_and_price_failures():
    plan = plan_preissue_acquisition(
        [_fixture(1, start_in=timedelta(hours=5), issue_in=timedelta(hours=4)),
         _fixture(2, start_in=timedelta(hours=6), issue_in=timedelta(hours=5))],
        now=NOW, policy=OddspapiBudgetPolicy(per_cycle_limit=20))
    results = execute_preissue_acquisition(
        plan,
        resolve_identity=lambda mid: None if mid == 1 else {"event": mid},
        fetch_prices=lambda mid, identity: [],
        clock=lambda: NOW,
    )
    assert [r.status for r in results] == [
        AcquisitionStatus.IDENTITY_UNAVAILABLE,
        AcquisitionStatus.PRICE_UNAVAILABLE,
    ]


def test_executor_converts_provider_error_to_terminal_result():
    plan = plan_preissue_acquisition([_fixture(1)], now=NOW)

    def boom(_mid):
        raise RuntimeError("provider down")

    results = execute_preissue_acquisition(
        plan, resolve_identity=boom,
        fetch_prices=lambda mid, identity: [], clock=lambda: NOW)
    assert results[0].status == AcquisitionStatus.PROVIDER_ERROR
    assert results[0].reason == "RuntimeError"


def test_executor_stops_at_attempt_cap():
    plan = plan_preissue_acquisition(
        [_fixture(1, start_in=timedelta(hours=5), issue_in=timedelta(hours=4)),
         _fixture(2, start_in=timedelta(hours=6), issue_in=timedelta(hours=5))],
        now=NOW, policy=OddspapiBudgetPolicy(per_cycle_limit=20))
    cap = OddspapiAttemptCap(1)
    results = execute_preissue_acquisition(
        plan,
        resolve_identity=lambda mid: {"event": mid},
        fetch_prices=lambda mid, identity: [("PLAYER_1", 2.0)],
        clock=lambda: NOW, attempt_cap=cap)
    assert results[0].status == AcquisitionStatus.CAPTURED
    assert results[1].status == AcquisitionStatus.ATTEMPT_CAP_EXHAUSTED


def test_executor_wait_budget_prevents_indefinite_delay():
    plan = plan_preissue_acquisition(
        [_fixture(1, start_in=timedelta(hours=5), issue_in=timedelta(hours=4)),
         _fixture(2, start_in=timedelta(hours=6), issue_in=timedelta(hours=5))],
        now=NOW, policy=OddspapiBudgetPolicy(per_cycle_limit=20))
    times = iter([NOW, NOW, NOW, NOW, NOW + timedelta(minutes=3)])
    results = execute_preissue_acquisition(
        plan,
        resolve_identity=lambda mid: {"event": mid},
        fetch_prices=lambda mid, identity: [("PLAYER_1", 2.0)],
        clock=lambda: next(times), max_wait=timedelta(minutes=2))
    assert results[0].status == AcquisitionStatus.CAPTURED
    assert results[1].status == AcquisitionStatus.TIMEOUT


def test_identity_completion_at_deadline_does_not_fetch_prices():
    plan = plan_preissue_acquisition(
        [_fixture(1, issue_in=timedelta(minutes=1))], now=NOW)
    times = iter([NOW, NOW, NOW + timedelta(minutes=1)])
    calls = []
    results = execute_preissue_acquisition(
        plan, resolve_identity=lambda mid: {"event": mid},
        fetch_prices=lambda mid, identity: calls.append(mid),
        clock=lambda: next(times), max_wait=timedelta(minutes=5))
    assert results[0].status == AcquisitionStatus.TOO_LATE
    assert results[0].prices == ()
    assert calls == []


def test_price_completion_at_deadline_is_never_preissue():
    plan = plan_preissue_acquisition(
        [_fixture(1, issue_in=timedelta(minutes=1))], now=NOW)
    times = iter([NOW, NOW, NOW, NOW + timedelta(minutes=1)])
    results = execute_preissue_acquisition(
        plan, resolve_identity=lambda mid: {"event": mid},
        fetch_prices=lambda mid, identity: [("PLAYER_1", 2.0), ("PLAYER_2", 1.8)],
        clock=lambda: next(times), max_wait=timedelta(minutes=5))
    assert results[0].status == AcquisitionStatus.TOO_LATE
    assert results[0].prices == ()
    assert results[0].completed_at == NOW + timedelta(minutes=1)


def test_price_completion_over_wait_budget_is_not_captured():
    plan = plan_preissue_acquisition([_fixture(1)], now=NOW)
    times = iter([NOW, NOW, NOW, NOW + timedelta(minutes=2)])
    results = execute_preissue_acquisition(
        plan, resolve_identity=lambda mid: {"event": mid},
        fetch_prices=lambda mid, identity: [("PLAYER_1", 2.0), ("PLAYER_2", 1.8)],
        clock=lambda: next(times), max_wait=timedelta(minutes=2))
    assert results[0].status == AcquisitionStatus.TIMEOUT
    assert results[0].prices == ()
