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
