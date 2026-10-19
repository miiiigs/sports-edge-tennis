"""Classify per-day fixture coverage without changing provider status semantics.

A completed runner is not proof every requested date had usable fixtures.
The only benign rejection classes here are explicitly out-of-scope
competitions; unrecognized statuses and identity/data defects are operational
warnings, not permission to infer an unknown state as SCHEDULED.

No provider calls, database reads or writes, or scoring changes occur here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# Non-target fixtures explicitly excluded by the Source A tour policy. All
# other rejection reasons are conservatively considered unverified data gaps.
OUT_OF_SCOPE_REJECTIONS = frozenset(
    {"out_of_scope_event_type", "unsupported_event_type",
     "unsupported_competition_tier"}
)


@dataclass(frozen=True)
class SlateFixtureCoverage:
    status: str  # HEALTHY | WARNING | EMPTY_OUT_OF_SCOPE | EMPTY_NO_SOURCE | EMPTY_NO_SCHEDULED
    reason: str
    seen: int
    ingested: int
    rejected: int
    targets: int
    unverified_rejections: int
    unknown_status_rows: int

    @property
    def is_warning(self) -> bool:
        return self.status == "WARNING"


def classify_slate_fixture_coverage(
    *,
    seen: int,
    ingested: int,
    rejected: int,
    targets: int,
    rejection_reasons: Iterable[tuple[str, int]],
) -> SlateFixtureCoverage:
    """Conservatively classify one date using existing reconciled counters.

    Zero source fixtures is an explicitly UNKNOWN coverage observation, not a
    scheduled fixture failure; we cannot infer a positive expected slate from
    a missing API response. Pure out-of-scope rejection is a legitimate empty
    slate. Non-scoped rejection reasons merit attention even if another date
    or other valid matches produce picks. Unknown numeric status '1' remains
    rejected without any mapping or new predictions.
    """
    if min(seen, ingested, rejected, targets) < 0:
        raise ValueError("slate coverage counters cannot be negative")
    reasons = dict(rejection_reasons)
    if any(count < 0 for count in reasons.values()):
        raise ValueError("rejection reason counts cannot be negative")

    unknown_status_rows = reasons.get("unsupported_event_status", 0)
    unverified = sum(
        count for name, count in reasons.items()
        if name not in OUT_OF_SCOPE_REJECTIONS
    )
    # If a source only supplies aggregate rejected count, it is NOT evidence
    # those rejections were benign and should never be silently ignored.
    unmatched = max(0, rejected - sum(reasons.values()))
    unverified += unmatched

    if seen != ingested + rejected or targets > ingested:
        status, reason = "WARNING", "RECONCILIATION_MISMATCH"
    elif unknown_status_rows:
        status, reason = "WARNING", "UNSUPPORTED_EVENT_STATUS"
    elif unverified:
        status, reason = "WARNING", "UNVERIFIED_IN_SCOPE_REJECTIONS"
    elif targets:
        status, reason = "HEALTHY", "SCHEDULED_TARGETS_PRESENT"
    elif seen == 0:
        status, reason = "EMPTY_NO_SOURCE", "NO_PROVIDER_FIXTURES_OBSERVED"
    elif ingested == 0 and rejected == seen:
        status, reason = "EMPTY_OUT_OF_SCOPE", "ALL_FIXTURES_EXCLUDED_BY_SCOPE"
    else:
        status, reason = "EMPTY_NO_SCHEDULED", "NO_CURRENT_SCHEDULED_TARGETS"

    return SlateFixtureCoverage(
        status=status,
        reason=reason,
        seen=seen,
        ingested=ingested,
        rejected=rejected,
        targets=targets,
        unverified_rejections=unverified,
        unknown_status_rows=unknown_status_rows,
    )
