"""Fixture discovery, lifecycle and capture-health evidence primitives.

These functions are deliberately pure: they never write to the database and
never infer that an absent fixture was void. They exist so a fixture's original
scheduled start, every observed schedule/status change, the first observation
deadline and the final classification can be reconstructed and tested
independently of the live runner.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

PIT_LEAD = timedelta(minutes=30)

#: A fixture is retrospective when its first provider observation is later than
#: this lead before its original scheduled start. Such a fixture was discovered
#: after the point at which a pre-match forecast could lawfully have been issued.
MISSING_VERSION = "MISSING"
UNCHANGED = "UNCHANGED"
UPDATED = "UPDATED"
RESCHEDULED = "RESCHEDULED"
POSTPONED = "POSTPONED"
CANCELLED = "CANCELLED"
EXPLICIT_REMOVAL = "EXPLICIT_REMOVAL"
ABSENT_COMPLETE_SNAPSHOT = "ABSENT_COMPLETE_SNAPSHOT"
UNKNOWN_INCOMPLETE_SNAPSHOT = "UNKNOWN_INCOMPLETE_SNAPSHOT"
CREATED_OR_NEWLY_OBSERVED = "CREATED_OR_NEWLY_OBSERVED"

TERMINAL_OR_NON_SCHEDULED = {
    "CANCELLED",
    "COMPLETED",
    "RETIRED",
    "WALKOVER",
    "DEFAULTED",
}


@dataclass(frozen=True)
class MatchVersionObservation:
    """One append-only ``match_versions`` row relevant to a fixture timeline."""

    match_version_id: int
    match_start_at: datetime | None
    match_status: str
    source_observed_at: datetime
    as_of: datetime
    ingested_at: datetime
    round_code: str | None = None
    best_of_sets: int | None = None
    qualifying_flag: bool | None = None


@dataclass(frozen=True)
class ScheduleChange:
    observed_at: datetime
    from_start: datetime | None
    to_start: datetime | None
    from_status: str | None
    to_status: str | None


@dataclass(frozen=True)
class FixtureTimeline:
    match_id: int
    original_scheduled_start: datetime | None
    latest_scheduled_start: datetime | None
    original_status: str | None
    latest_status: str | None
    version_count: int
    distinct_start_count: int
    start_change_count: int
    status_change_count: int
    changes: tuple[ScheduleChange, ...]
    first_source_observed_at: datetime | None
    first_ingested_at: datetime | None
    latest_observed_at: datetime | None
    pit_visible_by_original_deadline: bool
    retrospective_discovery: bool
    explicitly_removed: bool
    classification: str


def _ordered(versions: Sequence[MatchVersionObservation]) -> list[MatchVersionObservation]:
    return sorted(
        versions,
        key=lambda v: (v.as_of, v.source_observed_at, v.match_version_id),
    )


def build_fixture_timeline(
    match_id: int,
    versions: Iterable[MatchVersionObservation],
    *,
    explicitly_removed: bool = False,
) -> FixtureTimeline:
    """Reconstruct one fixture's original start and every observed change.

    Ordering uses the append-only version clocks, never wall-clock ingestion
    order. The original scheduled start is the first version's start and is
    retained even when later versions move the fixture.
    """
    ordered = _ordered(list(versions))
    if not ordered:
        return FixtureTimeline(
            match_id=match_id,
            original_scheduled_start=None,
            latest_scheduled_start=None,
            original_status=None,
            latest_status=None,
            version_count=0,
            distinct_start_count=0,
            start_change_count=0,
            status_change_count=0,
            changes=(),
            first_source_observed_at=None,
            first_ingested_at=None,
            latest_observed_at=None,
            pit_visible_by_original_deadline=False,
            retrospective_discovery=False,
            explicitly_removed=explicitly_removed,
            classification=EXPLICIT_REMOVAL if explicitly_removed else MISSING_VERSION,
        )

    first, latest = ordered[0], ordered[-1]
    original_start = first.match_start_at
    changes: list[ScheduleChange] = []
    previous = first
    start_change_count = 0
    status_change_count = 0
    for current in ordered[1:]:
        if current.match_start_at != previous.match_start_at:
            start_change_count += 1
        if current.match_status != previous.match_status:
            status_change_count += 1
        if (
            current.match_start_at != previous.match_start_at
            or current.match_status != previous.match_status
        ):
            changes.append(
                ScheduleChange(
                    observed_at=current.source_observed_at,
                    from_start=previous.match_start_at,
                    to_start=current.match_start_at,
                    from_status=previous.match_status,
                    to_status=current.match_status,
                )
            )
        previous = current

    first_source_observed_at = min(v.source_observed_at for v in ordered)
    first_ingested_at = min(v.ingested_at for v in ordered)
    latest_observed_at = max(v.source_observed_at for v in ordered)
    pit_visible = bool(
        original_start is not None
        and any(
            v.as_of <= original_start - PIT_LEAD
            and v.source_observed_at <= original_start - PIT_LEAD
            and v.ingested_at <= original_start - PIT_LEAD
            for v in ordered
        )
    )
    retrospective = bool(
        original_start is not None
        and (
            first_source_observed_at > original_start - PIT_LEAD
            or first_ingested_at > original_start - PIT_LEAD
        )
    )

    if explicitly_removed:
        classification = EXPLICIT_REMOVAL
    elif latest.match_status == "CANCELLED":
        classification = CANCELLED
    elif latest.match_status == "POSTPONED":
        classification = POSTPONED
    elif start_change_count:
        classification = RESCHEDULED
    elif status_change_count or any(
        current.round_code != previous.round_code
        or current.best_of_sets != previous.best_of_sets
        or current.qualifying_flag != previous.qualifying_flag
        for previous, current in pairwise(ordered)
    ):
        classification = UPDATED
    else:
        classification = UNCHANGED

    return FixtureTimeline(
        match_id=match_id,
        original_scheduled_start=original_start,
        latest_scheduled_start=latest.match_start_at,
        original_status=first.match_status,
        latest_status=latest.match_status,
        version_count=len(ordered),
        distinct_start_count=len({v.match_start_at for v in ordered}),
        start_change_count=start_change_count,
        status_change_count=status_change_count,
        changes=tuple(changes),
        first_source_observed_at=first_source_observed_at,
        first_ingested_at=first_ingested_at,
        latest_observed_at=latest_observed_at,
        pit_visible_by_original_deadline=pit_visible,
        retrospective_discovery=retrospective,
        explicitly_removed=explicitly_removed,
        classification=classification,
    )


@dataclass(frozen=True)
class FixtureSnapshot:
    scheduled_start: str
    status: str = "SCHEDULED"


def reconcile_snapshot_changes(
    previous: Mapping[str, FixtureSnapshot],
    current: Mapping[str, FixtureSnapshot],
    *,
    current_complete: bool = False,
    explicit_removed: Iterable[str] = (),
    explicit_postponed: Iterable[str] = (),
) -> list[dict[str, object]]:
    """Classify snapshot changes without converting absence into a void.

    ``explicit_removed`` and ``explicit_postponed`` must come from an
    authorising feed. Absence from an incomplete snapshot remains unknown.
    """
    removed = set(explicit_removed)
    postponed = set(explicit_postponed)
    if removed & current.keys():
        raise ValueError("contradictory current snapshot and removal feed")
    if postponed & current.keys():
        raise ValueError("contradictory current snapshot and postponement feed")
    rows: list[dict[str, object]] = []
    for fixture_id in sorted(previous.keys() | current.keys() | removed | postponed):
        before = previous.get(fixture_id)
        after = current.get(fixture_id)
        if fixture_id in removed:
            status = EXPLICIT_REMOVAL
        elif fixture_id in postponed:
            status = POSTPONED
        elif before is None:
            status = CREATED_OR_NEWLY_OBSERVED
        elif after is None:
            status = (
                ABSENT_COMPLETE_SNAPSHOT
                if current_complete is True
                else UNKNOWN_INCOMPLETE_SNAPSHOT
            )
        elif before.scheduled_start != after.scheduled_start:
            status = RESCHEDULED
        elif before.status != after.status:
            status = UPDATED
        else:
            status = UNCHANGED
        rows.append(
            {
                "source_match_id": fixture_id,
                "previous_start": None if before is None else before.scheduled_start,
                "current_start": None if after is None else after.scheduled_start,
                "previous_status": None if before is None else before.status,
                "current_status": None if after is None else after.status,
                "status": status,
                "void_inferred": False,
            }
        )
    return rows


def select_prematch_targets(
    matches: Iterable[object],
    *,
    as_of: datetime,
    minimum_lead: timedelta = PIT_LEAD,
) -> tuple[list[int], list[dict[str, object]]]:
    """Fail closed on fixtures that cannot still be forecast before the deadline.

    A fixture without a scheduled start is not targetable: an unknown start
    cannot prove that the forecast would be pre-match. A fixture whose start is
    already inside the 30-minute lead is also excluded. The caller receives both
    the eligible IDs and an explicit exclusion record, so a retrospective
    backfill cannot silently become a new prediction target.
    """
    eligible: list[int] = []
    excluded: list[dict[str, object]] = []
    cutoff = as_of + minimum_lead
    for match in matches:
        match_id = int(match.match_id)
        status = str(getattr(match, "match_status", ""))
        start = getattr(match, "match_start_at", None)
        if status != "SCHEDULED":
            excluded.append({"match_id": match_id, "reason": "NOT_SCHEDULED"})
        elif start is None:
            excluded.append({"match_id": match_id, "reason": "MISSING_SCHEDULED_START"})
        elif start < cutoff:
            excluded.append(
                {
                    "match_id": match_id,
                    "reason": "START_INSIDE_OR_AFTER_DEADLINE",
                    "match_start_at": start.isoformat(),
                    "cutoff": cutoff.isoformat(),
                }
            )
        else:
            eligible.append(match_id)
    return eligible, excluded


@dataclass(frozen=True)
class CaptureHealth:
    status: str
    heartbeat_count: int
    first_heartbeat_at: datetime | None
    last_heartbeat_at: datetime | None
    stale_seconds: float | None
    gaps: tuple[dict[str, object], ...]


def detect_capture_gaps(
    heartbeats: Iterable[datetime],
    *,
    window_start: datetime,
    window_end: datetime,
    expected_interval: timedelta,
    tolerance: float = 1.5,
) -> CaptureHealth:
    """Detect missing capture heartbeats, including silence before/after a run.

    A silently stopped scheduler produces no failure rows at all. This function
    therefore checks the expected cadence itself: an internal gap longer than
    ``tolerance * expected_interval`` and a stale tail are both failures.
    """
    if expected_interval <= timedelta(0):
        raise ValueError("expected_interval must be positive")
    ordered = sorted(h for h in heartbeats if window_start <= h <= window_end)
    if not ordered:
        return CaptureHealth(
            status="NO_HEARTBEAT",
            heartbeat_count=0,
            first_heartbeat_at=None,
            last_heartbeat_at=None,
            stale_seconds=None,
            gaps=(
                {
                    "from": window_start.isoformat(),
                    "to": window_end.isoformat(),
                    "missing_expected_intervals": max(
                        1, int((window_end - window_start) / expected_interval)
                    ),
                },
            ),
        )

    threshold = expected_interval * tolerance
    gaps: list[dict[str, object]] = []
    if ordered[0] - window_start > threshold:
        gaps.append(
            {
                "from": window_start.isoformat(),
                "to": ordered[0].isoformat(),
                "missing_expected_intervals": max(
                    0, int((ordered[0] - window_start) / expected_interval) - 1
                ),
            }
        )
    for before, after in pairwise(ordered):
        if after - before > threshold:
            gaps.append(
                {
                    "from": before.isoformat(),
                    "to": after.isoformat(),
                    "missing_expected_intervals": max(
                        0, int((after - before) / expected_interval) - 1
                    ),
                }
            )
    stale_seconds = (window_end - ordered[-1]).total_seconds()
    if window_end - ordered[-1] > threshold:
        gaps.append(
            {
                "from": ordered[-1].isoformat(),
                "to": window_end.isoformat(),
                "missing_expected_intervals": max(
                    0, int((window_end - ordered[-1]) / expected_interval) - 1
                ),
            }
        )
    status = "GAP" if gaps else "HEALTHY"
    return CaptureHealth(
        status=status,
        heartbeat_count=len(ordered),
        first_heartbeat_at=ordered[0],
        last_heartbeat_at=ordered[-1],
        stale_seconds=stale_seconds,
        gaps=tuple(gaps),
    )


def reconcile_selection_populations(
    *,
    manager_consensus: Iterable[int],
    tracking_only: Iterable[int],
    published_model_picks: Iterable[int],
    settled: Iterable[int],
) -> dict[str, object]:
    """Report distinct selection populations without equating any two of them.

    Manager consensus is a model decision, tracking-only selections are not
    published Model Picks, published picks are the aligned read-model population,
    and settlement is a later lifecycle event. Overlaps are reported, never
    silently treated as the same cohort.
    """
    consensus = set(manager_consensus)
    tracking = set(tracking_only)
    published = set(published_model_picks)
    settlement = set(settled)
    return {
        "manager_consensus": len(consensus),
        "tracking_only": len(tracking),
        "published_model_picks": len(published),
        "settled": len(settlement),
        "tracking_only_outside_published": sorted(tracking - published),
        "published_outside_manager_consensus": sorted(published - consensus),
        "consensus_not_published": sorted(consensus - published),
        "published_not_settled": sorted(published - settlement),
        "settled_without_selection": sorted(settlement - published - consensus - tracking),
        "populations_are_equivalent": consensus == tracking == published == settlement,
        "void_or_cancellation_inferred": False,
    }

