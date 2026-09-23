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
from datetime import datetime
from math import ceil
from typing import Iterable, Sequence


class OddspapiBudgetError(RuntimeError):
    """Raised when a planned OddsPapi request set would exceed the budget."""


@dataclass(frozen=True)
class OddspapiBudgetPolicy:
    monthly_limit: int = 5000
    reserve: int = 500
    per_cycle_limit: int = 5
    tournaments_per_call: int = 5
    cycles_per_month: int = 744

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


@dataclass(frozen=True)
class SchedulingItem:
    match_id: int
    tournament_id: int
    scheduled_start: datetime
    has_cached_identity: bool


@dataclass(frozen=True)
class SchedulingOutcome:
    scheduled_match_ids: tuple[int, ...]
    deferred_match_ids: tuple[int, ...]
    scheduled_tournament_ids: tuple[int, ...]
    planned_requests: int
    needs_discovery: bool


def schedule_oddspapi_work(
    items: Iterable[SchedulingItem],
    *,
    books: Sequence[str],
    policy: OddspapiBudgetPolicy,
) -> SchedulingOutcome:
    """Deterministically schedule which matches get priced within the cycle ceiling.

    Scheduling rule (market-fetch ordering only; never model scores):

    1. cached OddsPapi identity first, then earliest scheduled start, then match_id;
    2. include a match for free when its tournament is already scheduled;
    3. otherwise tentatively add the tournament and recompute
       ``books * ceil(tournaments / tournaments_per_call) + discovery``; commit
       only if it stays within ``per_cycle_limit``, else defer the match.

    Deferred matches stay fail-closed (unpriced) and are re-considered next cycle.
    """
    ordered = sorted(
        items,
        key=lambda item: (
            0 if item.has_cached_identity else 1,
            item.scheduled_start,
            item.match_id,
        ),
    )
    clean_books = tuple(dict.fromkeys(str(b).strip() for b in books if str(b).strip()))
    if not clean_books:
        raise ValueError("books must be non-empty")
    scheduled_tournaments: list[int] = []
    scheduled_matches: list[int] = []
    deferred_matches: list[int] = []
    scheduled_need_discovery = False

    for item in ordered:
        if item.tournament_id in scheduled_tournaments:
            scheduled_matches.append(item.match_id)
            if not item.has_cached_identity:
                scheduled_need_discovery = True
            continue
        candidate = scheduled_tournaments + [item.tournament_id]
        chunks = ceil(len(candidate) / policy.tournaments_per_call)
        needs_discovery = scheduled_need_discovery or not item.has_cached_identity
        requests = len(clean_books) * chunks + (1 if needs_discovery else 0)
        if requests > policy.per_cycle_limit:
            deferred_matches.append(item.match_id)
            continue
        scheduled_tournaments.append(item.tournament_id)
        scheduled_matches.append(item.match_id)
        scheduled_need_discovery = needs_discovery

    return SchedulingOutcome(
        scheduled_match_ids=tuple(scheduled_matches),
        deferred_match_ids=tuple(deferred_matches),
        scheduled_tournament_ids=tuple(scheduled_tournaments),
        planned_requests=len(clean_books) * ceil(len(scheduled_tournaments) / policy.tournaments_per_call)
        + (1 if scheduled_need_discovery and scheduled_tournaments else 0),
        needs_discovery=bool(scheduled_tournaments) and scheduled_need_discovery,
    )


__all__ = [
    "OddspapiBudgetError",
    "OddspapiBudgetPolicy",
    "OddspapiRequestPlan",
    "SchedulingItem",
    "SchedulingOutcome",
    "enforce_oddspapi_budget",
    "plan_oddspapi_qualification",
    "qualification_odds_required",
    "schedule_oddspapi_work",
]
