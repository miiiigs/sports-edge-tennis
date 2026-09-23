from __future__ import annotations

"""OddsPapi request-budget planning and enforcement.

OddsPapi has no usage endpoint, so the live pipeline must bound its own
consumption. This module computes the *exact* request plan for a cycle before
any request is sent and fails closed when the plan would exceed the configured
per-cycle ceiling or the projected monthly allowance.

Design targets (hourly cadence, ~720 cycles/month, ~5,000/month allowance):

* qualification odds are only fetched for Manager-eligible matches, so a
  zero-consensus cycle plans **zero** OddsPapi requests;
* tournament ids are batched at most ``tournaments_per_call`` (5) per request;
* each request carries exactly one bookmaker;
* normal cycles stay well under the per-cycle ceiling, leaving reserve for
  retries and busy slates.
"""

from dataclasses import dataclass
from math import ceil
from typing import Iterable, Sequence


class OddspapiBudgetError(RuntimeError):
    """Raised when a planned OddsPapi request set would exceed the budget."""


@dataclass(frozen=True)
class OddspapiBudgetPolicy:
    monthly_limit: int = 5000
    reserve: int = 500
    per_cycle_limit: int = 6
    tournaments_per_call: int = 5
    cycles_per_month: int = 720

    @property
    def usable_monthly(self) -> int:
        return max(0, self.monthly_limit - self.reserve)

    def projected_monthly(self, requests_per_cycle: int) -> int:
        return int(requests_per_cycle) * self.cycles_per_month


@dataclass(frozen=True)
class OddspapiRequestPlan:
    tournament_ids: tuple[int, ...]
    books: tuple[str, ...]
    discovery_requests: int
    odds_requests: int
    total_requests: int

    @property
    def is_empty(self) -> bool:
        return self.total_requests == 0


def plan_oddspapi_qualification(
    *,
    tournament_ids: Iterable[int],
    books: Sequence[str],
    discovery_book: str,
    policy: OddspapiBudgetPolicy,
    include_discovery: bool = True,
) -> OddspapiRequestPlan:
    """Compute the exact request plan for consensus-eligible tournaments.

    ``discovery_book`` is the single bookmaker used for the cheap fixture
    discovery call; ``books`` are the per-book odds calls. Tournament ids are
    de-duplicated and sorted so the plan is deterministic.
    """
    if policy.tournaments_per_call < 1:
        raise ValueError("tournaments_per_call must be >= 1")
    unique_tournaments = tuple(sorted({int(value) for value in tournament_ids}))
    if not unique_tournaments:
        return OddspapiRequestPlan((), (), 0, 0, 0)
    clean_books = tuple(dict.fromkeys(str(b).strip() for b in books if str(b).strip()))
    if not clean_books:
        raise ValueError("books must be non-empty when tournaments are planned")
    discovery_requests = 1 if include_discovery else 0
    chunks = ceil(len(unique_tournaments) / policy.tournaments_per_call)
    odds_requests = chunks * len(clean_books)
    return OddspapiRequestPlan(
        tournament_ids=unique_tournaments,
        books=clean_books,
        discovery_requests=discovery_requests,
        odds_requests=odds_requests,
        total_requests=discovery_requests + odds_requests,
    )


def enforce_oddspapi_budget(
    plan: OddspapiRequestPlan,
    *,
    policy: OddspapiBudgetPolicy,
    monthly_used_estimate: int = 0,
    max_retries_per_request: int = 0,
) -> None:
    """Fail closed if the plan exceeds the per-cycle or monthly ceiling.

    Retries are counted against the budget: the worst-case request count is
    ``total_requests * (1 + max_retries_per_request)``.
    """
    if policy.per_cycle_limit < 1:
        raise ValueError("per_cycle_limit must be >= 1")
    worst_case_cycle = plan.total_requests * (1 + max(0, int(max_retries_per_request)))
    if worst_case_cycle > policy.per_cycle_limit:
        raise OddspapiBudgetError(
            "oddspapi_cycle_budget_exceeded "
            f"planned={plan.total_requests} worst_case={worst_case_cycle} "
            f"limit={policy.per_cycle_limit}"
        )
    projected = int(monthly_used_estimate) + policy.projected_monthly(worst_case_cycle)
    if projected > policy.monthly_limit:
        raise OddspapiBudgetError(
            "oddspapi_monthly_budget_exceeded "
            f"used={monthly_used_estimate} projected={projected} limit={policy.monthly_limit}"
        )


def qualification_odds_required(*, manager_consensus_count: int) -> bool:
    """Rule A: zero Manager consensus means zero qualification odds requests."""
    return int(manager_consensus_count) > 0


__all__ = [
    "OddspapiBudgetError",
    "OddspapiBudgetPolicy",
    "OddspapiRequestPlan",
    "enforce_oddspapi_budget",
    "plan_oddspapi_qualification",
    "qualification_odds_required",
]
