"""January rehearsal: all operational scenarios are accounted for."""
from __future__ import annotations

from sports_edge_tennis.validation.january_rehearsal import (
    BLOCKED,
    PASS,
    run_january_rehearsal,
)


def test_rehearsal_covers_all_required_scenarios():
    payload = run_january_rehearsal()
    names = {item["scenario"] for item in payload["scenarios"]}
    required = {
        "FIRST_ELIGIBLE_ATP_FIXTURE",
        "FIRST_ELIGIBLE_CHALLENGER_FIXTURE",
        "AUSTRALIAN_OPEN_BO5_MAIN_DRAW",
        "AUSTRALIAN_OPEN_QUALIFYING_BO3_CLASSIFICATION",
        "WITHDRAWAL_AND_REPLACEMENT",
        "LUCKY_LOSER_REQUIRES_DRAW_DIFF",
        "RESCHEDULED_START_PRESERVES_BOTH_CLOCKS",
        "PROVIDER_FAILURE_NOT_EMPTY_SLATE",
        "MISSING_FIXTURE_REMAINS_IN_DENOMINATOR",
        "ARCHIVE_UNAVAILABLE_FAILS_RECEIPT",
        "SETTLEMENT_CORRECTION_APPEND_ONLY",
        "SCHEDULER_NON_EXECUTION",
        "NO_UNIVERSAL_JANUARY_PENALTY",
    }
    assert required <= names
    assert payload["provider_calls"] == 0
    assert payload["database_writes"] == 0
    assert payload["production_activation"] is False


def test_main_draw_and_qualifying_formats_are_not_conflated():
    payload = run_january_rehearsal()
    by_name = {item["scenario"]: item for item in payload["scenarios"]}
    main = by_name["AUSTRALIAN_OPEN_BO5_MAIN_DRAW"]["evidence"]
    qualifying = by_name[
        "AUSTRALIAN_OPEN_QUALIFYING_BO3_CLASSIFICATION"
    ]["evidence"]
    assert main["format_matches_scope"] is True
    assert main["forecast_enabled"] is True
    assert qualifying["format_matches_scope"] is True
    assert qualifying["forecast_enabled"] is False


def test_lucky_loser_is_blocked_not_assumed():
    payload = run_january_rehearsal()
    lucky = next(
        item
        for item in payload["scenarios"]
        if item["scenario"] == "LUCKY_LOSER_REQUIRES_DRAW_DIFF"
    )
    assert lucky["status"] == BLOCKED
    assert lucky["evidence"]["draw_diff_implemented"] is False


def test_unknowns_are_preserved_and_not_invented():
    payload = run_january_rehearsal()
    assert payload["preserved_unknowns"]["ao_2027_edition_rulebook"] == (
        "NOT_LOCATED"
    )
    assert payload["preserved_unknowns"]["entries_published"] is False
    assert payload["preserved_unknowns"]["draws_published"] is False
    assert payload["preserved_unknowns"]["qualifying_exact_first_match"] == (
        "UNKNOWN_PENDING_OFFICIAL_ORDER_OF_PLAY"
    )


def test_no_january_or_inactivity_penalty():
    payload = run_january_rehearsal()
    penalty = next(
        item
        for item in payload["scenarios"]
        if item["scenario"] == "NO_UNIVERSAL_JANUARY_PENALTY"
    )
    assert penalty["status"] == PASS
    assert penalty["evidence"]["forecast_adjustment"] == 0.0
    assert penalty["evidence"]["january_universal_penalty_applied"] is False
    assert payload["no_universal_january_or_inactivity_penalty"] is True
