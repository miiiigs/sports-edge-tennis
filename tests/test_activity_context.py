"""January and inactivity metadata: distinguish contexts without a penalty."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from sports_edge_tennis.validation.activity_context import (
    EXTENDED_INDIVIDUAL_ABSENCE,
    INSUFFICIENT_ACTIVITY_EVIDENCE,
    MISSING_LOWER_LEVEL_HISTORY,
    NORMAL_SEASONAL_INTERRUPTION,
    RETIREMENT,
    SHORT_RETURN_FROM_ABSENCE,
    SURFACE_CHANGE,
    WITHDRAWAL,
    ActivityThresholds,
    PlayerActivityObservation,
    assert_no_universal_january_penalty,
    attach_activity_metadata,
    classify_activity,
)

AO_START = date(2027, 1, 17)
JANUARY = date(2027, 1, 5)
JULY = date(2027, 7, 1)


def _observation(**overrides) -> PlayerActivityObservation:
    base = dict(
        player_id=1,
        as_of=JANUARY,
        competition_start=AO_START,
        last_match_date=date(2026, 11, 20),
        status="ACTIVE",
        matches_last_90_days=0,
        matches_last_365_days=45,
        tour_level_matches=120,
        lower_level_matches=5,
        current_surface="HARD",
        previous_surface="HARD",
        surface_matches=80,
    )
    base.update(overrides)
    return PlayerActivityObservation(**base)


def test_normal_january_return_is_a_seasonal_interruption_not_extended_absence():
    context = classify_activity(_observation())
    assert context.primary == NORMAL_SEASONAL_INTERRUPTION
    assert NORMAL_SEASONAL_INTERRUPTION in context.flags
    assert EXTENDED_INDIVIDUAL_ABSENCE not in context.flags
    assert context.is_january is True
    assert context.is_competition_opening is True
    assert context.days_inactive == 46


def test_extended_individual_absence_in_january_is_not_called_normal():
    context = classify_activity(
        _observation(last_match_date=date(2026, 6, 1))
    )
    assert context.primary == EXTENDED_INDIVIDUAL_ABSENCE
    assert EXTENDED_INDIVIDUAL_ABSENCE in context.flags
    assert NORMAL_SEASONAL_INTERRUPTION not in context.flags


def test_extended_individual_absence_mid_season():
    context = classify_activity(
        _observation(
            as_of=JULY,
            competition_start=JULY,
            last_match_date=date(2026, 12, 1),
        )
    )
    assert context.primary == EXTENDED_INDIVIDUAL_ABSENCE
    assert context.is_january is False


def test_short_return_from_absence_is_its_own_context():
    context = classify_activity(
        _observation(last_match_date=date(2026, 11, 20))
    )
    assert context.primary == NORMAL_SEASONAL_INTERRUPTION

    context = classify_activity(
        _observation(
            as_of=JULY,
            competition_start=JULY,
            last_match_date=date(2027, 5, 15),
        )
    )
    assert context.primary == SHORT_RETURN_FROM_ABSENCE
    assert SHORT_RETURN_FROM_ABSENCE in context.flags


def test_withdrawal_and_retirement_are_distinct_terminal_contexts():
    withdrawn = classify_activity(_observation(status="WITHDRAWN"))
    assert withdrawn.primary == WITHDRAWAL
    assert withdrawn.flags == (WITHDRAWAL,)
    retired = classify_activity(_observation(status="RETIRED"))
    assert retired.primary == RETIREMENT
    assert retired.flags == (RETIREMENT,)


def test_missing_lower_level_history_is_flagged_without_a_penalty():
    context = classify_activity(
        _observation(tour_level_matches=8, lower_level_matches=30)
    )
    assert MISSING_LOWER_LEVEL_HISTORY in context.flags
    assert context.forecast_adjustment == 0.0


def test_surface_change_is_flagged_and_can_coexist_with_history_context():
    context = classify_activity(
        _observation(previous_surface="CLAY", current_surface="HARD", surface_matches=4)
    )
    assert SURFACE_CHANGE in context.flags
    assert MISSING_LOWER_LEVEL_HISTORY in context.flags
    assert context.forecast_adjustment == 0.0


def test_unknown_activity_evidence_never_becomes_a_penalty():
    context = classify_activity(
        _observation(last_match_date=None, tour_level_matches=None)
    )
    assert context.primary == INSUFFICIENT_ACTIVITY_EVIDENCE
    assert INSUFFICIENT_ACTIVITY_EVIDENCE in context.flags
    assert context.forecast_adjustment == 0.0


def test_january_and_july_return_the_same_zero_adjustment():
    january = classify_activity(_observation())
    july = classify_activity(
        _observation(
            as_of=JULY,
            competition_start=JULY,
            last_match_date=date(2027, 6, 20),
        )
    )
    assert january.forecast_adjustment == july.forecast_adjustment == 0.0
    assert january.january_universal_penalty_applied is False
    assert july.january_universal_penalty_applied is False
    assert_no_universal_january_penalty(january, july)


def test_diagnostics_attachment_does_not_touch_the_probability():
    context = classify_activity(_observation())
    payload = attach_activity_metadata(
        {"raw_probability": "0.6412", "agent": "AGENT_1_SURFACE_ELO"},
        activity=context,
    )
    assert payload["raw_probability"] == "0.6412"
    assert payload["forecast_probability_changed_by_activity"] is False
    assert payload["activity_context"]["forecast_adjustment"] == 0.0


def test_thresholds_are_validated():
    with pytest.raises(ValueError):
        ActivityThresholds(short_absence_days=100, extended_absence_days=90)
    with pytest.raises(ValueError):
        ActivityThresholds(normal_offseason_max_days=60, extended_absence_days=90)


def test_invalid_observations_fail_closed():
    with pytest.raises(ValueError):
        _observation(last_match_date=date(2027, 2, 1))
    with pytest.raises(ValueError):
        _observation(tour_level_matches=-1)
    with pytest.raises(ValueError):
        _observation(status="BANNED")
