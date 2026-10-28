"""Replay must never retroactively rewrite historical picks or tune on bad days."""
from datetime import date, timedelta

import pytest

from research.tennis_aligned_oct_replay_20261028.replay import (
    Pick, RULES, parse_ledger, replay, stats,
)


def pick(day, match_id, probability, outcome, surface="CLAY", pick_type="MANAGER_CONSENSUS"):
    return Pick(match_id, date(2026, 10, day), probability, surface, pick_type, outcome, "2026-10-01T00:00:00Z")


def test_original_counterfactual_retains_full_actual_audit_and_skips_correctly():
    rows = [
        pick(24, 1, .82, "WON"), pick(24, 2, .64, "LOST"),
        pick(24, 3, .82, "LOST"), pick(24, 4, .63, "WON"),
        pick(24, 5, .75, "PENDING"), pick(24, 6, .84, "VOID"),
    ]
    baseline = stats(rows, RULES[0])
    filtered = stats(rows, RULES[3])
    assert baseline["original"] == 6
    assert baseline["retained"] == 6
    assert baseline["wins"] == 2 and baseline["losses"] == 2
    assert filtered["retained"] == 3
    assert filtered["wins"] == 1 and filtered["losses"] == 1
    assert filtered["skipped_wins"] == 1 and filtered["skipped_losses"] == 1
    assert baseline["day_status"] == "UNSETTLED"
    assert filtered["day_status"].endswith("REPLAY")


def test_no_picks_or_all_void_cannot_claim_success():
    assert stats([], RULES[0])["day_status"] == "NO_ORIGINAL_PICKS"
    assert stats([pick(24, 1, .80, "VOID")], RULES[0])["day_status"] == "NO_GRADED_PICKS"


def test_candidate_selection_never_uses_oct24_27_outcomes():
    earlier = [
        pick(1 + (i // 10), i + 1, .82 if i % 2 else .62, "WON" if i % 4 else "LOST")
        for i in range(100)
    ]
    audit = [
        pick(d, 100 + d, .88, "LOST") for d in (24, 25, 26, 27)
    ]
    first = replay(earlier + audit)
    changed = replay(earlier + [
        pick(d, 100 + d, .88, "WON") for d in (24, 25, 26, 27)
    ])
    assert first["selected_from_prior_period_only"] == changed["selected_from_prior_period_only"]
    assert first["prior_period_selection_trials"] == changed["prior_period_selection_trials"]
    assert first["candidate_deployed"] is False
    assert first["oct24_27_used_to_select_candidate"] is False
    assert first["daily_counterfactuals"][0]["daily"] != changed["daily_counterfactuals"][0]["daily"]


def test_73_is_not_rounded_up_and_day_gaps_break_streak():
    rows = [pick(24, i+1, .82, "WON" if i < 8 else "LOST") for i in range(11)]
    assert stats(rows, RULES[0])["day_status"] == "BELOW_73_PROVISIONAL_REPLAY"
    result = replay(rows)
    assert result["daily_counterfactuals"][0]["all_four_days_met_73"] is False


def test_input_rejects_duplicates_invalid_confidence_unknown_status():
    row = {
        "match_id":1, "aligned_pick_type":"A1_A2_WIDE_SPREAD",
        "aligned_probability":.75, "picked_at":"2026-10-23T01:00:00Z",
        "manila_date":"2026-10-24", "surface":"HARD", "result_status":"LOST"
    }
    assert parse_ledger({"data":[row]})[0].pick_type == "A1_A2_WIDE_SPREAD"
    with pytest.raises(ValueError, match="duplicate"):
        parse_ledger({"data":[row,row]})
    with pytest.raises(ValueError, match="invalid"):
        parse_ledger({"data":[dict(row,aligned_probability=float("nan"))]})
    with pytest.raises(ValueError, match="unknown result"):
        parse_ledger({"data":[dict(row,result_status="REMOVED")]})


def test_unknown_surface_replay_requires_real_original_surface():
    rows = [pick(24,1,.82,"WON","UNKNOWN"),pick(24,2,.82,"LOST","HARD")]
    assert stats(rows, RULES[4])["skipped_wins"] == 1
    assert stats(rows, RULES[4])["losses"] == 1
