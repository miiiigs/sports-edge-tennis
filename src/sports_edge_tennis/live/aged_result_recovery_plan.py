"""Budgeted, auditable long-tail recovery plan (not an inferred settlement).

The normal <3-day result writer stays unchanged. This planner chooses at
most ONE historical UTC day to query in a daily diagnostic run. Aged
results remain visible until an authoritative terminal record is stored.

It produces no provider calls or database writes by itself. A real
provider probe must use refresh_pending_results(...,dry_run=True,
explicit_window=(date,date),max_targets<=10).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sports_edge_tennis.live.pending_result_diagnostics import (
    PendingResultDiagnosticsReport,
)

MIN_OLD_AGE_DAYS = 3
MAX_OLD_AGE_DAYS = 90
MAX_TARGETS_PER_REQUEST = 10
MAX_REQUESTS_PER_DAY = 1

# Never infer a winner from stale schedule, unmapped event or walkover.
# These can be probed for provider evidence but not auto-settled.
NO_AUTOMATIC_SETTLEMENT = (
    "TERMINAL_FIXTURE_WITHOUT_RESULT",
    "NO_API_TENNIS_LINK",
    "PROVIDER_FIXTURE_NOT_REOBSERVED",
)


@dataclass(frozen=True)
class AgedRecoveryPlan:
    at: datetime
    provider_request_budget: int
    selected_day: date | None
    selected_match_ids: tuple[int, ...]
    manual_review_match_ids: tuple[int, ...]
    overdue_match_ids: tuple[int, ...]
    skipped_future: int
    skipped_unobserved: int
    skipped_outside_90_days: int

    def log_line(self) -> str:
        return (
            f"PENDING_AGED_RECOVERY_PLAN status=READ_ONLY_DRYRUN "
            f"budget_requests={self.provider_request_budget} "
            f"selected_utc_date={self.selected_day.isoformat() if self.selected_day else '-'} "
            f"targets={len(self.selected_match_ids)} "
            f"manual={len(self.manual_review_match_ids)} "
            f"overdue={len(self.overdue_match_ids)} "
            f"future_skipped={self.skipped_future} "
            f"unobserved_skipped={self.skipped_unobserved} "
            f"outside_90d_skipped={self.skipped_outside_90_days}"
        )


def plan_aged_recovery(
    report: PendingResultDiagnosticsReport,
    *,
    as_of: datetime,
) -> AgedRecoveryPlan:
    """Exactly one time-bounded provider query per day when explicitly enabled.

    Oldest/unseen cases are not silently dropped: report/manual counts
    include every unresolved case, even if it is never safe to probe.
    Windows rotate with day ordinal rather than endlessly hitting
    one stale fixture. No provider query happens without a valid
    UTC date and an observed match start.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    today = as_of.astimezone(UTC).date()
    by_date: dict[date, list[int]] = defaultdict(list)
    manual: list[int] = []
    overdue: list[int] = []
    future = outside = unobserved = 0
    seen: set[int] = set()
    for item in report.items:
        mid = item.match_id
        if mid in seen:
            raise ValueError("duplicate canonical pending match")
        seen.add(mid)
        ev = item.evidence
        start = ev.latest_match_start_at
        if start is None:
            unobserved += 1
            manual.append(mid)
            continue
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("fixture time must be timezone-aware")
        when = start.astimezone(UTC)
        if when > as_of:
            future += 1
            continue
        age_days = (today - when.date()).days
        if item.hours_since_start is not None and item.hours_since_start >= 24:
            overdue.append(mid)
        if item.reason in NO_AUTOMATIC_SETTLEMENT:
            manual.append(mid)
        if age_days < MIN_OLD_AGE_DAYS:
            continue
        if age_days > MAX_OLD_AGE_DAYS:
            outside += 1
            manual.append(mid)
            continue
        # This is only a provider evidence dry-run, never a winner/void
        # assignment. No invalid or future identity is queried.
        by_date[when.date()].append(mid)

    day: date | None = None
    chosen: tuple[int, ...] = ()
    if by_date:
        days = sorted(by_date)
        day = days[today.toordinal() % len(days)]
        chosen = tuple(sorted(by_date[day])[:MAX_TARGETS_PER_REQUEST])
    return AgedRecoveryPlan(
        at=as_of,
        provider_request_budget=MAX_REQUESTS_PER_DAY if chosen else 0,
        selected_day=day,
        selected_match_ids=chosen,
        manual_review_match_ids=tuple(sorted(set(manual))),
        overdue_match_ids=tuple(sorted(set(overdue))),
        skipped_future=future,
        skipped_unobserved=unobserved,
        skipped_outside_90_days=outside,
    )
