"""Settlement scheduling: source, ordering, revision and staleness gates."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.ops.settlement_schedule import (
    ACTION_BLOCKED,
    ACTION_BOOKMAKER,
    ACTION_CORRECTION,
    ACTION_NONE,
    ACTION_SPORTING,
    PATHWAY_BOOKMAKER,
    PATHWAY_SPORTING,
    REASON_ALREADY_SETTLED,
    REASON_BOOKMAKER_GRADE_MISSING,
    REASON_BOOKMAKER_READY,
    REASON_BOOKMAKER_RULE_UNAPPROVED,
    REASON_CORRECTION,
    REASON_PARTICIPANT_ORDER,
    REASON_RESCHEDULED_AFTER_RESULT,
    REASON_RESULT_BEFORE_START,
    REASON_SPORTING_READY,
    REASON_STALE_RESULT,
    REASON_UNAPPROVED_SOURCE,
    SettlementCandidate,
    plan_bookmaker_settlement,
    plan_settlements,
    plan_sporting_settlement,
)

AS_OF = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
START = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
RESULT = START + timedelta(hours=3)


def _candidate(**overrides) -> SettlementCandidate:
    base = dict(
        decision_id=1,
        match_id=10,
        scheduled_start=START,
        original_start=START,
        revised_start=None,
        actual_start=START,
        result_status="COMPLETED",
        result_observed_at=RESULT,
        result_revision=1,
        source_system="OFFICIAL",
        source_dataset="RESULTS",
        source_reference="ref-1",
        source_approved=True,
        source_participants=(101, 102),
        canonical_participants=(101, 102),
        winner_player_id=101,
        selection_player_id=101,
    )
    base.update(overrides)
    return SettlementCandidate(**base)


# Sporting pathway


def test_completed_result_with_correct_orientation_and_source_is_ready():
    plan = plan_sporting_settlement(_candidate(), as_of=AS_OF)
    assert plan.pathway == PATHWAY_SPORTING
    assert plan.action == ACTION_SPORTING
    assert plan.reason == REASON_SPORTING_READY
    assert plan.grade == "WON"


def test_lost_selection_is_graded_from_the_winner_not_inferred():
    plan = plan_sporting_settlement(
        _candidate(winner_player_id=102), as_of=AS_OF
    )
    assert plan.grade == "LOST"


def test_unapproved_source_is_blocked():
    plan = plan_sporting_settlement(
        _candidate(source_approved=False), as_of=AS_OF
    )
    assert plan.action == ACTION_BLOCKED
    assert plan.reason == REASON_UNAPPROVED_SOURCE


def test_participant_order_mismatch_is_blocked():
    plan = plan_sporting_settlement(
        _candidate(source_participants=(999, 101)), as_of=AS_OF
    )
    assert plan.action == ACTION_BLOCKED
    assert plan.reason == REASON_PARTICIPANT_ORDER


def test_result_observed_before_scheduled_start_is_blocked():
    plan = plan_sporting_settlement(
        _candidate(result_observed_at=START - timedelta(minutes=5)), as_of=AS_OF
    )
    assert plan.action == ACTION_BLOCKED
    assert plan.reason == REASON_RESULT_BEFORE_START


def test_rescheduled_start_after_the_result_is_blocked():
    plan = plan_sporting_settlement(
        _candidate(
            revised_start=RESULT + timedelta(hours=1),
            actual_start=None,
        ),
        as_of=AS_OF,
    )
    assert plan.action == ACTION_BLOCKED
    assert plan.reason == REASON_RESCHEDULED_AFTER_RESULT


def test_stale_outcome_is_blocked():
    plan = plan_sporting_settlement(
        _candidate(result_observed_at=START + timedelta(minutes=30)),
        as_of=AS_OF,
        stale_after=timedelta(hours=1),
    )
    assert plan.action == ACTION_BLOCKED
    assert plan.reason == REASON_STALE_RESULT


def test_same_revision_is_not_settled_twice():
    plan = plan_sporting_settlement(
        _candidate(current_settlement_revision=1, current_sporting_grade="WON"),
        as_of=AS_OF,
    )
    assert plan.action == ACTION_NONE
    assert plan.reason == REASON_ALREADY_SETTLED


def test_newer_result_revision_requires_an_explicit_correction():
    plan = plan_sporting_settlement(
        _candidate(
            result_revision=2,
            winner_player_id=102,
            current_settlement_revision=1,
            current_sporting_grade="WON",
        ),
        as_of=AS_OF,
    )
    assert plan.action == ACTION_CORRECTION
    assert plan.reason == REASON_CORRECTION
    assert plan.grade == "LOST"
    assert plan.supersedes_revision == 1


def test_cancellation_before_start_is_allowed_and_graded_cancelled():
    plan = plan_sporting_settlement(
        _candidate(
            result_status="CANCELLED",
            winner_player_id=None,
            result_observed_at=START - timedelta(hours=2),
        ),
        as_of=AS_OF,
        stale_after=timedelta(days=3),
    )
    assert plan.action == ACTION_SPORTING
    assert plan.grade == "CANCELLED"


def test_retirement_is_void_for_sporting_accuracy_not_a_bookmaker_result():
    plan = plan_sporting_settlement(
        _candidate(result_status="RETIRED", winner_player_id=None), as_of=AS_OF
    )
    assert plan.action == ACTION_SPORTING
    assert plan.grade == "VOID"
    book = plan_bookmaker_settlement(
        _candidate(
            result_status="RETIRED",
            winner_player_id=None,
            bookmaker="book-a",
            bookmaker_rule_version="v1",
            bookmaker_rule_reference="ref-a",
            approved_bookmaker_rules={("book-a", "v1"): "ref-a"},
        ),
        as_of=AS_OF,
    )
    assert book.grade is None
    assert book.reason == REASON_BOOKMAKER_GRADE_MISSING


# Bookmaker pathway


def test_bookmaker_rule_must_be_approved():
    plan = plan_bookmaker_settlement(
        _candidate(
            bookmaker="book-a",
            bookmaker_rule_version="v1",
            bookmaker_rule_reference="ref-a",
            bookmaker_grade="WON",
            approved_bookmaker_rules={("book-a", "v1"): "different"},
        ),
        as_of=AS_OF,
    )
    assert plan.action == ACTION_BLOCKED
    assert plan.reason == REASON_BOOKMAKER_RULE_UNAPPROVED


def test_explicit_grade_under_approved_rule_is_ready():
    plan = plan_bookmaker_settlement(
        _candidate(
            bookmaker="book-a",
            bookmaker_rule_version="v1",
            bookmaker_rule_reference="ref-a",
            bookmaker_grade="WON",
            approved_bookmaker_rules={("book-a", "v1"): "ref-a"},
        ),
        as_of=AS_OF,
    )
    assert plan.pathway == PATHWAY_BOOKMAKER
    assert plan.action == ACTION_BOOKMAKER
    assert plan.reason == REASON_BOOKMAKER_READY


def test_plan_settlements_keeps_the_two_pathways_separate():
    plans = plan_settlements([_candidate()], as_of=AS_OF)
    assert [plan.pathway for plan in plans] == [PATHWAY_SPORTING, PATHWAY_BOOKMAKER]
    assert plans[0].grade == "WON"
    assert plans[1].grade is None
