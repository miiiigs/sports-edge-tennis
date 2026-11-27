"""Operational activity context for January and inactivity readiness.

The published evidence is clear: there is no start-of-season favourite effect
(January +0.1 point at equal probability across 4,263 matches), while returning
after a month without playing is associated with a large shortfall against Elo.
Those are different populations. A blanket "January penalty" would therefore be
wrong, and this module never applies one.

The classifier only *labels* the context so the pipeline, dashboard and
diagnostics can distinguish:

* a normal seasonal interruption (the tour's own off-season);
* an extended individual absence (injury, suspension, personal absence);
* a player with missing lower-level history;
* a withdrawal or retirement;
* a surface change;
* a return from a short absence;
* unknown evidence.

Every context carries ``forecast_adjustment = 0.0`` and
``january_universal_penalty_applied = False``. A future model change must justify
itself separately; this module is metadata, not a silent probability shift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping

NORMAL_SEASONAL_INTERRUPTION = "NORMAL_SEASONAL_INTERRUPTION"
EXTENDED_INDIVIDUAL_ABSENCE = "EXTENDED_INDIVIDUAL_ABSENCE"
SHORT_RETURN_FROM_ABSENCE = "SHORT_RETURN_FROM_ABSENCE"
MISSING_LOWER_LEVEL_HISTORY = "MISSING_LOWER_LEVEL_HISTORY"
WITHDRAWAL = "WITHDRAWAL"
RETIREMENT = "RETIREMENT"
SURFACE_CHANGE = "SURFACE_CHANGE"
INSUFFICIENT_ACTIVITY_EVIDENCE = "INSUFFICIENT_ACTIVITY_EVIDENCE"

STATUS_ACTIVE = "ACTIVE"
STATUS_WITHDRAWN = "WITHDRAWN"
STATUS_RETIRED = "RETIRED"
STATUS_UNKNOWN = "UNKNOWN"

VALID_STATUSES = (STATUS_ACTIVE, STATUS_WITHDRAWN, STATUS_RETIRED, STATUS_UNKNOWN)

ADJUSTMENT_POLICY = "NONE_METADATA_ONLY"


@dataclass(frozen=True)
class ActivityThresholds:
    """Configurable, documented thresholds. No threshold changes a probability."""

    short_absence_days: int = 30
    extended_absence_days: int = 90
    normal_offseason_max_days: int = 120
    season_opening_days: int = 21
    min_tour_level_matches: int = 20
    min_surface_matches: int = 10
    offseason_last_match_month: int = 10  # October or later is the prior season

    def __post_init__(self) -> None:
        if self.short_absence_days < 0 or self.extended_absence_days < 0:
            raise ValueError("absence thresholds must not be negative")
        if self.short_absence_days >= self.extended_absence_days:
            raise ValueError("short absence must be below extended absence")
        if self.normal_offseason_max_days < self.extended_absence_days:
            raise ValueError("normal off-season window must not be shorter than "
                             "the extended-absence threshold")
        if not (1 <= self.offseason_last_match_month <= 12):
            raise ValueError("offseason_last_match_month must be 1..12")


@dataclass(frozen=True)
class PlayerActivityObservation:
    player_id: int
    as_of: date
    competition_start: date
    last_match_date: date | None
    status: str = STATUS_ACTIVE
    matches_last_90_days: int | None = None
    matches_last_365_days: int | None = None
    tour_level_matches: int | None = None
    lower_level_matches: int | None = None
    current_surface: str | None = None
    previous_surface: str | None = None
    surface_matches: int | None = None

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"unknown status {self.status!r}")
        if self.last_match_date is not None and self.last_match_date > self.as_of:
            raise ValueError("last_match_date cannot be after as_of")
        for name in (
            "matches_last_90_days",
            "matches_last_365_days",
            "tour_level_matches",
            "lower_level_matches",
            "surface_matches",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")

    @property
    def days_inactive(self) -> int | None:
        if self.last_match_date is None:
            return None
        return (self.as_of - self.last_match_date).days


@dataclass(frozen=True)
class ActivityContext:
    player_id: int
    flags: tuple[str, ...]
    primary: str
    days_inactive: int | None
    is_january: bool
    is_competition_opening: bool
    forecast_adjustment: float
    january_universal_penalty_applied: bool
    adjustment_policy: str
    evidence: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {
            "player_id": self.player_id,
            "flags": list(self.flags),
            "primary": self.primary,
            "days_inactive": self.days_inactive,
            "is_january": self.is_january,
            "is_competition_opening": self.is_competition_opening,
            "forecast_adjustment": self.forecast_adjustment,
            "january_universal_penalty_applied": self.january_universal_penalty_applied,
            "adjustment_policy": self.adjustment_policy,
            "evidence": dict(self.evidence),
        }


def classify_activity(
    observation: PlayerActivityObservation,
    *,
    thresholds: ActivityThresholds = ActivityThresholds(),
) -> ActivityContext:
    """Classify activity context without changing any probability."""

    flags: list[str] = []
    evidence: dict[str, object] = {
        "last_match_date": (
            observation.last_match_date.isoformat()
            if observation.last_match_date
            else None
        ),
        "status": observation.status,
        "matches_last_90_days": observation.matches_last_90_days,
        "matches_last_365_days": observation.matches_last_365_days,
        "tour_level_matches": observation.tour_level_matches,
        "lower_level_matches": observation.lower_level_matches,
        "current_surface": observation.current_surface,
        "previous_surface": observation.previous_surface,
        "surface_matches": observation.surface_matches,
    }
    days = observation.days_inactive
    is_january = observation.as_of.month == 1
    opening = (
        is_january
        and abs((observation.competition_start - observation.as_of).days)
        <= thresholds.season_opening_days
    )

    if observation.status == STATUS_RETIRED:
        flags.append(RETIREMENT)
        primary = RETIREMENT
    elif observation.status == STATUS_WITHDRAWN:
        flags.append(WITHDRAWAL)
        primary = WITHDRAWAL
    else:
        primary = INSUFFICIENT_ACTIVITY_EVIDENCE
        if days is None:
            flags.append(INSUFFICIENT_ACTIVITY_EVIDENCE)
        else:
            normal_offseason = (
                opening
                and is_january
                and observation.last_match_date is not None
                and observation.last_match_date.month
                >= thresholds.offseason_last_match_month
                and days <= thresholds.normal_offseason_max_days
            )
            if normal_offseason:
                flags.append(NORMAL_SEASONAL_INTERRUPTION)
                primary = NORMAL_SEASONAL_INTERRUPTION
            elif days > thresholds.extended_absence_days:
                flags.append(EXTENDED_INDIVIDUAL_ABSENCE)
                primary = EXTENDED_INDIVIDUAL_ABSENCE
            elif days >= thresholds.short_absence_days:
                flags.append(SHORT_RETURN_FROM_ABSENCE)
                primary = SHORT_RETURN_FROM_ABSENCE
            else:
                primary = STATUS_ACTIVE

        missing_history = (
            observation.tour_level_matches is not None
            and observation.tour_level_matches < thresholds.min_tour_level_matches
            and (
                observation.lower_level_matches is None
                or observation.lower_level_matches > 0
            )
        )
        if missing_history:
            flags.append(MISSING_LOWER_LEVEL_HISTORY)
            if primary == INSUFFICIENT_ACTIVITY_EVIDENCE:
                primary = MISSING_LOWER_LEVEL_HISTORY

    surface_change = (
        observation.current_surface is not None
        and observation.previous_surface is not None
        and observation.current_surface != observation.previous_surface
    )
    if surface_change:
        flags.append(SURFACE_CHANGE)
        if primary == STATUS_ACTIVE:
            primary = SURFACE_CHANGE

    if (
        observation.surface_matches is not None
        and observation.surface_matches < thresholds.min_surface_matches
        and SURFACE_CHANGE in flags
    ):
        flags.append(MISSING_LOWER_LEVEL_HISTORY)

    return ActivityContext(
        player_id=observation.player_id,
        flags=tuple(dict.fromkeys(flags)),
        primary=primary,
        days_inactive=days,
        is_january=is_january,
        is_competition_opening=opening,
        forecast_adjustment=0.0,
        january_universal_penalty_applied=False,
        adjustment_policy=ADJUSTMENT_POLICY,
        evidence=evidence,
    )


def attach_activity_metadata(
    metadata: Mapping[str, object],
    *,
    activity: ActivityContext,
) -> dict[str, object]:
    """Return a diagnostics payload with activity context attached.

    This is JSONB-safe, so it can travel in an existing diagnostics column
    without a schema migration. The forecast probability is untouched.
    """

    payload = dict(metadata)
    payload["activity_context"] = activity.to_payload()
    payload["forecast_probability_changed_by_activity"] = False
    return payload


def assert_no_universal_january_penalty(
    january_context: ActivityContext,
    july_context: ActivityContext,
) -> None:
    """Guard used by tests: the month must not change the adjustment."""

    if january_context.forecast_adjustment != 0.0:
        raise AssertionError("January context must not carry a penalty")
    if july_context.forecast_adjustment != 0.0:
        raise AssertionError("non-January context must not carry a penalty")
    if january_context.january_universal_penalty_applied:
        raise AssertionError("universal January penalty flag must be false")
