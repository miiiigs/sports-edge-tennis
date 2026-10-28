"""Locked point-in-time features and safe research export behavior."""

import pytest

from research.tennis_aligned_oct_replay_20261028.export_from_db import sanitized_ledger
from research.tennis_aligned_oct_replay_20261028.replay import losing_pick_diagnostics, parse_ledger


def _rec(match_id, ptype, prob, surface, tier, result):
    return {
        "match_id": match_id,
        "manila_date": "2026-10-24",
        "aligned_probability": prob,
        "aligned_pick_type": ptype,
        "picked_at": "2026-10-23T23:00:00+00:00",
        "surface": surface,
        "tier": tier,
        "result_status": result,
        "player_1": "Private Player",
        "market_decimal_price": 2.35,
    }


def test_export_only_whitelisted_fields_and_both_aligned_groups():
    rows = [
        _rec(11, "MANAGER_CONSENSUS", 0.82, "CLAY", "ATP_TOUR", "WON"),
        _rec(12, "A1_A2_WIDE_SPREAD", 0.65, "UNKNOWN", "CHALLENGER", "LOST"),
    ]
    ledger = sanitized_ledger(rows)
    assert ledger["row_count"] == 2
    assert len(ledger["sha256"]) == 64
    assert "Private Player" not in str(ledger)
    assert "market_decimal_price" not in str(ledger)
    parsed = parse_ledger(ledger)
    assert {x.pick_type for x in parsed} == {
        "MANAGER_CONSENSUS", "A1_A2_WIDE_SPREAD"
    }
    stats = losing_pick_diagnostics(parsed)
    assert stats["surface"]["CLAY"]["won"] == 1
    assert stats["surface"]["UNKNOWN"]["lost"] == 1
    assert stats["aligned_pick_type"]["A1_A2_WIDE_SPREAD"]["lost"] == 1
    assert stats["tournament_tier"]["CHALLENGER"]["lost"] == 1


def test_malformed_or_duplicate_original_matches_fail_closed():
    rows = [
        _rec(11, "MANAGER_CONSENSUS", .81, "CLAY", "ATP_TOUR", "WON"),
        _rec(11, "MANAGER_CONSENSUS", .82, "HARD", "ATP_TOUR", "LOST"),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        sanitized_ledger(rows)
