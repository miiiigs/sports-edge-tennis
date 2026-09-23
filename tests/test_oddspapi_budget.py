from __future__ import annotations

import pytest

from sports_edge_tennis.live.oddspapi_budget import (
    OddspapiBudgetError,
    OddspapiBudgetPolicy,
    enforce_oddspapi_budget,
    plan_oddspapi_qualification,
    qualification_odds_required,
)

BOOKS = ("pinnacle+30", "1xbet", "unibet", "bet365")


def test_zero_consensus_plans_zero_requests() -> None:
    assert qualification_odds_required(manager_consensus_count=0) is False
    assert qualification_odds_required(manager_consensus_count=3) is True
    plan = plan_oddspapi_qualification(
        tournament_ids=(),
        books=BOOKS,
        discovery_book="pinnacle+30",
        policy=OddspapiBudgetPolicy(),
    )
    assert plan.is_empty
    assert plan.total_requests == 0


def test_one_tournament_request_plan() -> None:
    plan = plan_oddspapi_qualification(
        tournament_ids=(44,),
        books=BOOKS,
        discovery_book="pinnacle+30",
        policy=OddspapiBudgetPolicy(),
    )
    # 1 discovery + 1 chunk * 4 books
    assert plan.discovery_requests == 1
    assert plan.odds_requests == 4
    assert plan.total_requests == 5


def test_five_tournaments_stay_in_one_chunk_per_book() -> None:
    plan = plan_oddspapi_qualification(
        tournament_ids=(1, 2, 3, 4, 5, 5),
        books=BOOKS,
        discovery_book="pinnacle+30",
        policy=OddspapiBudgetPolicy(),
    )
    assert plan.tournament_ids == (1, 2, 3, 4, 5)
    assert plan.odds_requests == 4
    assert plan.total_requests == 5


def test_six_tournaments_need_two_chunks_per_book() -> None:
    plan = plan_oddspapi_qualification(
        tournament_ids=(1, 2, 3, 4, 5, 6),
        books=BOOKS,
        discovery_book="pinnacle+30",
        policy=OddspapiBudgetPolicy(),
    )
    assert plan.odds_requests == 8
    assert plan.total_requests == 9


def test_budget_refuses_plan_over_cycle_ceiling() -> None:
    plan = plan_oddspapi_qualification(
        tournament_ids=(1, 2, 3, 4, 5, 6),
        books=BOOKS,
        discovery_book="pinnacle+30",
        policy=OddspapiBudgetPolicy(per_cycle_limit=6),
    )
    with pytest.raises(OddspapiBudgetError, match="cycle_budget_exceeded"):
        enforce_oddspapi_budget(plan, policy=OddspapiBudgetPolicy(per_cycle_limit=6))


def test_budget_counts_retries() -> None:
    plan = plan_oddspapi_qualification(
        tournament_ids=(1,),
        books=("pinnacle+30",),
        discovery_book="pinnacle+30",
        policy=OddspapiBudgetPolicy(),
    )
    # 2 requests with 2 retries each = 6 worst case; limit 6 passes, limit 5 fails.
    enforce_oddspapi_budget(plan, policy=OddspapiBudgetPolicy(per_cycle_limit=6), max_retries_per_request=2)
    with pytest.raises(OddspapiBudgetError, match="cycle_budget_exceeded"):
        enforce_oddspapi_budget(plan, policy=OddspapiBudgetPolicy(per_cycle_limit=5), max_retries_per_request=2)


def test_budget_refuses_when_projected_monthly_exceeds_limit() -> None:
    policy = OddspapiBudgetPolicy(monthly_limit=5000, reserve=0, per_cycle_limit=6, cycles_per_month=720)
    plan = plan_oddspapi_qualification(
        tournament_ids=(1,),
        books=("pinnacle+30",),
        discovery_book="pinnacle+30",
        policy=policy,
    )
    # 2/cycle * 720 = 1440 fine; pretend 4000 already used -> over.
    enforce_oddspapi_budget(plan, policy=policy, monthly_used_estimate=3000)
    with pytest.raises(OddspapiBudgetError, match="monthly_budget_exceeded"):
        enforce_oddspapi_budget(plan, policy=policy, monthly_used_estimate=4000)


from datetime import UTC, datetime

from sports_edge_tennis.live.oddspapi_budget import SchedulingItem, schedule_oddspapi_work


def _scheduling_items(n: int, *, cached: bool = False):
    return [
        SchedulingItem(
            match_id=i,
            tournament_id=100 + i,
            scheduled_start=datetime(2026, 9, 10 + i, tzinfo=UTC),
            has_cached_identity=cached,
        )
        for i in range(1, n + 1)
    ]


def test_scheduler_prefers_cached_identity_then_earliest_start() -> None:
    items = [
        SchedulingItem(2, 200, datetime(2026, 9, 1, tzinfo=UTC), False),
        SchedulingItem(1, 100, datetime(2026, 9, 9, tzinfo=UTC), True),
    ]
    out = schedule_oddspapi_work(items, books=("pinnacle+30",), policy=OddspapiBudgetPolicy())
    assert out.scheduled_match_ids == (1, 2)


def test_scheduler_defers_beyond_cycle_ceiling() -> None:
    out = schedule_oddspapi_work(
        _scheduling_items(6),
        books=("pinnacle+30", "1xbet", "unibet", "bet365"),
        policy=OddspapiBudgetPolicy(per_cycle_limit=5),
    )
    assert len(out.scheduled_tournament_ids) == 5
    assert out.deferred_match_ids == (6,)
    assert out.planned_requests <= 5


def test_scheduler_cold_cache_uses_discovery() -> None:
    out = schedule_oddspapi_work(
        _scheduling_items(1),
        books=("pinnacle+30", "1xbet", "unibet", "bet365"),
        policy=OddspapiBudgetPolicy(),
    )
    assert out.needs_discovery is True
    assert out.planned_requests == 5


def test_scheduler_warm_cache_skips_discovery() -> None:
    out = schedule_oddspapi_work(
        _scheduling_items(1, cached=True),
        books=("pinnacle+30", "1xbet", "unibet", "bet365"),
        policy=OddspapiBudgetPolicy(),
    )
    assert out.needs_discovery is False
    assert out.planned_requests == 4
