"""Safe pre-issue OddsPapi acquisition ordering.

The live cycle currently resolves identity and captures prices *after* the first
Manager issue, so those observations cannot qualify as original-time evidence.
This module provides the missing ordering primitive without changing the live
cycle by default:

1. order eligible fixtures by the earliest safe deadline
   (``min(first_issue, scheduled_start - lead)``);
2. include only fixtures whose entitlement and monthly allowance permit work;
3. batch at most ``tournaments_per_call`` tournament ids per request and stop at
   the per-cycle ceiling;
4. execute with an injected identity resolver and price fetcher, bounded by a
   wall-clock wait budget, and downgrade every failure to ``UNAVAILABLE``.

Forecasting must never wait indefinitely for odds. The executor therefore returns
a terminal status for every fixture and never raises a provider, budget or
timeout error to the caller. A fixture that cannot be priced simply proceeds to
forecast without a market quote, exactly as it does today.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sports_edge_tennis.live.oddspapi_budget import (
    OddspapiBudgetError,
    OddspapiBudgetPolicy,
    OddspapiRequestPlan,
    enforce_oddspapi_budget,
    plan_oddspapi_qualification,
)
from sports_edge_tennis.live.oddspapi_ledger import (
    OddspapiAttemptCap,
    OddspapiBudgetExceeded,
)

DEFAULT_LEAD = timedelta(minutes=30)
DEFAULT_HORIZON = timedelta(days=3)
DEFAULT_MAX_WAIT = timedelta(minutes=2)
DEFAULT_BUDGET_POLICY = OddspapiBudgetPolicy()


class AcquisitionStatus(StrEnum):
    READY = "READY"
    CAPTURED = "CAPTURED"
    TOO_LATE = "TOO_LATE"
    FORECAST_ALREADY_ISSUED = "FORECAST_ALREADY_ISSUED"
    NOT_ENTITLED = "NOT_ENTITLED"
    MONTHLY_ALLOWANCE_EXHAUSTED = "MONTHLY_ALLOWANCE_EXHAUSTED"
    OUTSIDE_HORIZON = "OUTSIDE_HORIZON"
    BUDGET_DEFERRED = "BUDGET_DEFERRED"
    IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"
    PRICE_UNAVAILABLE = "PRICE_UNAVAILABLE"
    ATTEMPT_CAP_EXHAUSTED = "ATTEMPT_CAP_EXHAUSTED"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"


@dataclass(frozen=True)
class PreIssueFixture:
    match_id: int
    tournament_id: int | None
    scheduled_start: datetime
    first_issue_at: datetime
    has_cached_identity: bool = False


@dataclass(frozen=True)
class AcquisitionItem:
    fixture: PreIssueFixture
    deadline: datetime
    status: AcquisitionStatus
    reason: str


@dataclass(frozen=True)
class AcquisitionPlan:
    generated_at: datetime
    lead: timedelta
    items: tuple[AcquisitionItem, ...]
    request_plan: OddspapiRequestPlan
    estimated_requests: int
    discovery_required: bool

    @property
    def ready(self) -> tuple[AcquisitionItem, ...]:
        return tuple(i for i in self.items if i.status == AcquisitionStatus.READY)


@dataclass(frozen=True)
class AcquisitionResult:
    match_id: int
    status: AcquisitionStatus
    reason: str
    identity: object | None = None
    prices: tuple[object, ...] = field(default_factory=tuple)
    completed_at: datetime | None = None


def _utc(value: datetime, name: str) -> datetime:
    if value is None or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _deadline(fixture: PreIssueFixture, lead: timedelta) -> datetime:
    return min(
        _utc(fixture.first_issue_at, "first_issue_at"),
        _utc(fixture.scheduled_start, "scheduled_start") - lead,
    )


def plan_preissue_acquisition(
    fixtures: Iterable[PreIssueFixture],
    *,
    now: datetime,
    policy: OddspapiBudgetPolicy = DEFAULT_BUDGET_POLICY,
    books: Sequence[str] = ("pinnacle+30", "1xbet", "bet365"),
    lead: timedelta = DEFAULT_LEAD,
    horizon: timedelta = DEFAULT_HORIZON,
    entitled: bool = True,
    monthly_remaining: int | None = None,
    max_retries_per_request: int = 0,
) -> AcquisitionPlan:
    """Order pre-issue work and apply the existing budget policy.

    ``monthly_remaining`` is the caller-supplied remaining allowance from the
    existing ledger. When it is ``None`` the plan relies on the per-cycle budget
    check only (the live binding still enforces the monthly limit atomically).
    """
    now = _utc(now, "now")
    if lead < timedelta(0):
        raise ValueError("lead must be non-negative")
    items: list[AcquisitionItem] = []
    eligible: list[PreIssueFixture] = []
    for fixture in fixtures:
        deadline = _deadline(fixture, lead)
        base = {"fixture": fixture, "deadline": deadline}
        start = _utc(fixture.scheduled_start, "scheduled_start")
        issue = _utc(fixture.first_issue_at, "first_issue_at")
        if not entitled:
            items.append(AcquisitionItem(
                **base, status=AcquisitionStatus.NOT_ENTITLED,
                reason="PROVIDER_ENTITLEMENT_MISSING"))
        elif monthly_remaining is not None and monthly_remaining <= 0:
            items.append(AcquisitionItem(
                **base, status=AcquisitionStatus.MONTHLY_ALLOWANCE_EXHAUSTED,
                reason="ODDSPAPI_MONTHLY_RESERVE_EXHAUSTED"))
        elif now >= issue:
            items.append(AcquisitionItem(
                **base, status=AcquisitionStatus.FORECAST_ALREADY_ISSUED,
                reason="FIRST_ISSUE_PASSED"))
        elif now >= deadline:
            items.append(AcquisitionItem(
                **base, status=AcquisitionStatus.TOO_LATE,
                reason="SAFE_DEADLINE_PASSED"))
        elif start - now > horizon:
            items.append(AcquisitionItem(
                **base, status=AcquisitionStatus.OUTSIDE_HORIZON,
                reason="SCHEDULED_START_BEYOND_HORIZON"))
        elif fixture.tournament_id is None:
            items.append(AcquisitionItem(
                **base, status=AcquisitionStatus.IDENTITY_UNAVAILABLE,
                reason="NO_TOURNAMENT_ID_FOR_BATCHING"))
        else:
            eligible.append(fixture)

    # Earliest deadline first. Cached identity is a tie-breaker: it avoids the
    # discovery request and is cheaper, but never outranks an earlier deadline.
    eligible.sort(key=lambda f: (_deadline(f, lead), f.match_id))
    scheduled: list[PreIssueFixture] = []
    scheduled_tournaments: list[int] = []
    discovery_required = False
    for fixture in eligible:
        deadline = _deadline(fixture, lead)
        candidate_tournaments = scheduled_tournaments + [int(fixture.tournament_id)]
        needs_discovery = discovery_required or not fixture.has_cached_identity
        plan = plan_oddspapi_qualification(
            tournament_ids=candidate_tournaments,
            books=books,
            discovery_book=books[0],
            policy=policy,
            include_discovery=needs_discovery,
        )
        try:
            enforce_oddspapi_budget(
                plan, policy=policy,
                monthly_used_estimate=0 if monthly_remaining is None
                else max(0, policy.monthly_limit - monthly_remaining),
                max_retries_per_request=max_retries_per_request)
        except OddspapiBudgetError:
            items.append(AcquisitionItem(
                fixture=fixture, deadline=deadline,
                status=AcquisitionStatus.BUDGET_DEFERRED,
                reason="PER_CYCLE_OR_MONTHLY_BUDGET"))
            continue
        scheduled.append(fixture)
        scheduled_tournaments = candidate_tournaments
        discovery_required = needs_discovery

    for fixture in scheduled:
        items.append(AcquisitionItem(
            fixture=fixture, deadline=_deadline(fixture, lead),
            status=AcquisitionStatus.READY,
            reason="READY_PRE_ISSUE"))
    request_plan = plan_oddspapi_qualification(
        tournament_ids=scheduled_tournaments, books=books,
        discovery_book=books[0], policy=policy,
        include_discovery=discovery_required,
    ) if scheduled_tournaments else OddspapiRequestPlan((), (), 0, 0, 0)
    return AcquisitionPlan(
        generated_at=now, lead=lead, items=tuple(items),
        request_plan=request_plan, estimated_requests=request_plan.total_requests,
        discovery_required=discovery_required,
    )


def executable_items(plan: AcquisitionPlan) -> tuple[AcquisitionItem, ...]:
    return tuple(item for item in plan.items
                 if item.status == AcquisitionStatus.READY)


def execute_preissue_acquisition(
    plan: AcquisitionPlan,
    *,
    resolve_identity: Callable[[int], object | None],
    fetch_prices: Callable[[int, object], Iterable[object]],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_wait: timedelta = DEFAULT_MAX_WAIT,
    attempt_cap: OddspapiAttemptCap | None = None,
) -> tuple[AcquisitionResult, ...]:
    """Execute ready items without ever blocking forecasting.

    Every provider, budget and cap failure is converted to a terminal result.
    The caller continues to forecast immediately after this function returns.
    """
    if max_wait < timedelta(0):
        raise ValueError("max_wait must be non-negative")
    started = clock()
    results: list[AcquisitionResult] = []
    for item in executable_items(plan):
        now = clock()
        if now >= item.deadline:
            results.append(AcquisitionResult(
                item.fixture.match_id, AcquisitionStatus.TOO_LATE,
                "SAFE_DEADLINE_PASSED", completed_at=now))
            continue
        if now - started >= max_wait:
            results.append(AcquisitionResult(
                item.fixture.match_id, AcquisitionStatus.TIMEOUT,
                "ACQUISITION_WAIT_BUDGET_EXHAUSTED", completed_at=now))
            continue
        try:
            if attempt_cap is not None:
                attempt_cap.take()
            identity = resolve_identity(item.fixture.match_id)
            if identity is None:
                results.append(AcquisitionResult(
                    item.fixture.match_id,
                    AcquisitionStatus.IDENTITY_UNAVAILABLE,
                    "NO_VERIFIED_IDENTITY", completed_at=clock()))
                continue
            prices = tuple(fetch_prices(item.fixture.match_id, identity))
            if not prices:
                results.append(AcquisitionResult(
                    item.fixture.match_id, AcquisitionStatus.PRICE_UNAVAILABLE,
                    "NO_BOTH_SIDED_MARKET_WINNER", identity=identity,
                    completed_at=clock()))
                continue
            results.append(AcquisitionResult(
                item.fixture.match_id, AcquisitionStatus.CAPTURED,
                "CAPTURED_BEFORE_FIRST_ISSUE", identity=identity,
                prices=prices, completed_at=clock()))
        except OddspapiBudgetExceeded:
            results.append(AcquisitionResult(
                item.fixture.match_id,
                AcquisitionStatus.ATTEMPT_CAP_EXHAUSTED,
                "PER_CYCLE_ATTEMPT_CAP", completed_at=clock()))
        except Exception as exc:  # noqa: BLE001 - provider failures must not block forecasting
            results.append(AcquisitionResult(
                item.fixture.match_id, AcquisitionStatus.PROVIDER_ERROR,
                type(exc).__name__, completed_at=clock()))
    return tuple(results)


__all__ = [
    "DEFAULT_HORIZON",
    "DEFAULT_LEAD",
    "DEFAULT_MAX_WAIT",
    "AcquisitionItem",
    "AcquisitionPlan",
    "AcquisitionResult",
    "AcquisitionStatus",
    "PreIssueFixture",
    "executable_items",
    "execute_preissue_acquisition",
    "plan_preissue_acquisition",
]
