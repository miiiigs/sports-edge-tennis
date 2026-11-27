from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from sports_edge_tennis.validation.draw_revision_rehearsal import (
    DrawSlot, DrawSnapshot, compare_synthetic_draws,
)

NOW = datetime(2026,11,27,10,tzinfo=UTC)
SLOT = DrawSlot("r1-01",1,10,20,"DIRECT","DIRECT",
                NOW+timedelta(days=2),"SCHEDULED")
BEFORE = DrawSnapshot("synthetic-edition","synthetic-source","1",NOW,(SLOT,))


def after(*slots):
    return replace(BEFORE,revision_id="2",observed_at=NOW+timedelta(hours=1),
                   slots=slots)


def test_declared_lucky_loser_and_reschedule_preserve_original():
    revised = replace(SLOT,player_2_id=30,entry_2="LUCKY_LOSER",
                      scheduled_start=SLOT.scheduled_start+timedelta(hours=1))
    report = compare_synthetic_draws(BEFORE,after(revised))
    row = report["changes"][0]
    assert {"PARTICIPANT_REPLACEMENT","DECLARED_LUCKY_LOSER","RESCHEDULED"} <= set(row["changes"])
    assert row["original"]["player_2_id"] == 20
    assert row["revised"]["player_2_id"] == 30
    assert row["preserve_original_forecast"] is True
    assert report["actual_2027_draw_verified"] is False


def test_missing_slot_is_not_proof_of_withdrawal():
    row = compare_synthetic_draws(BEFORE,after())["changes"][0]
    assert row["changes"] == ["SLOT_MISSING_NOT_PROVEN_WITHDRAWAL"]


def test_swapped_orientation_and_cancellation_are_explicit():
    revised = replace(SLOT,player_1_id=20,player_2_id=10,status="CANCELLED")
    flags = compare_synthetic_draws(BEFORE,after(revised))["changes"][0]["changes"]
    assert "PARTICIPANT_ORIENTATION_CHANGED" in flags
    assert "STATUS_CHANGED_CANCELLED" in flags
    assert "PARTICIPANT_REPLACEMENT" not in flags


def test_duplicate_slots_and_nonadvancing_revision_rejected():
    with pytest.raises(ValueError,match="duplicate"):
        compare_synthetic_draws(BEFORE,after(SLOT,SLOT))
    with pytest.raises(ValueError,match="advancing"):
        compare_synthetic_draws(BEFORE,BEFORE)


def test_qualifier_placeholder_transition_and_bye():
    placeholder = replace(SLOT,player_2_id=None,entry_2="TBD",status="TBD")
    initial = replace(BEFORE,slots=(placeholder,))
    qualifying = replace(SLOT,entry_2="QUALIFIER")
    flags = compare_synthetic_draws(initial,after(qualifying))["changes"][0]["changes"]
    assert "DECLARED_QUALIFIER" in flags
    bye = replace(placeholder,entry_2="BYE",status="BYE")
    assert "STATUS_CHANGED_BYE" in compare_synthetic_draws(initial,after(bye))["changes"][0]["changes"]
