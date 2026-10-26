"""Synthetic-only validation of research gates. No production claims."""

from datetime import date, timedelta

import pytest

from research.tennis_manager_73_shadow_20261027.evaluator import (
    Pick, _streak, evaluate, parse_ledger,
)


def _samples(days=90):
    first = date(2026, 1, 1)
    picks = []
    ident = 0
    for n in range(days):
        for j in range(20):
            ident += 1
            confidence = 0.82 if j < 16 else 0.63
            # High-confidence picks 13/16; lower-confidence picks 1/4.
            won = j < 13 or j == 16
            picks.append(Pick(
                match_id=ident, day=first + timedelta(days=n),
                confidence=confidence, result="WON" if won else "LOST",
                regime="TEST_FIXED_REGIME",
            ))
    return picks


def test_research_selects_only_validation_and_reports_holdout():
    report = evaluate(_samples())
    assert report["status"] == "HOLDOUT_EVALUATED_RESEARCH_ONLY"
    assert report["selected_threshold"] == 0.65
    assert report["baseline_holdout"]["win_rate"] == 0.70
    assert report["selected_holdout"]["win_rate"] == 13 / 16
    assert report["selected_holdout"]["calendar_streak"]["last_five_all_met"] is True
    assert report["live_approval"] is False


def test_missing_or_mixed_model_regime_blocks_analysis():
    cases = _samples()
    assert evaluate([Pick(x.match_id, x.day, x.confidence, x.result, "")
                     for x in cases])["status"].startswith("BLOCKED_MISSING")
    mixed = list(cases)
    mixed[-1] = Pick(mixed[-1].match_id, mixed[-1].day, mixed[-1].confidence, mixed[-1].result, "OTHER")
    assert evaluate(mixed)["status"].startswith("BLOCKED_MISSING")


def test_missing_day_and_pending_break_five_day_claim():
    d = date(2026, 1, 1)
    picks = [Pick(i, d + timedelta(days=i), 0.82, "WON", "REGIME") for i in (1, 2, 4, 5, 6)]
    assert not _streak(picks, 0.8)["last_five_all_met"]
    picks = [Pick(i, d + timedelta(days=i), 0.82, "WON", "REGIME") for i in range(1, 7)]
    picks[-1] = Pick(6, d + timedelta(days=6), 0.82, "PENDING", "REGIME")
    assert not _streak(picks, 0.8)["last_five_all_met"]


def test_ledger_manager_only_and_duplicates_fail_closed():
    row = {
        "aligned_pick_type": "MANAGER_CONSENSUS", "match_id": 3,
        "manila_date": "2026-10-20", "aligned_probability": 0.82,
        "result_status": "WON", "model_regime_key": "R1",
    }
    tracking = dict(row, aligned_pick_type="A1_A2_WIDE_SPREAD", match_id=4)
    parsed = parse_ledger({"data": [row, tracking]})
    assert len(parsed) == 1 and parsed[0].match_id == 3
    with pytest.raises(ValueError, match="duplicate"):
        parse_ledger({"data": [row, row]})
    with pytest.raises(ValueError, match="unknown settlement"):
        parse_ledger({"data": [dict(row, result_status="BLAH")]})
