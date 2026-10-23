"""Synthetic-only chronological shadow filter test: never changes live rules."""
from datetime import datetime, timedelta, timezone

import pytest

from research.tennis_pit_rank_evidence_20261022.enrichment import enrich
from research.tennis_pit_rank_evidence_20261022.exclusion_holdout import (
    assess_predeclared_filter, paired_bootstrap_delta, scored, snapshot
)


def row(i, *, band=True, win=False, date="2026-09-01"):
    picked = f"{date}T08:00:00Z"
    return {
        "population": "MANAGER", "canonical_match_id": f"c-{i}",
        "picked_at": picked, "outcome_status": "COMPLETED",
        "winner_side": 1 if win else 2, "manager_side": 1, "manager_p": .70,
        "a1_side": 1, "a1_p": .65, "a2_side": 2, "a2_p": .60,
        "selected_rank_status": "VERIFIED" if band else "NO_AUTHORIZED_VERIFIED_PRE_PICK_RANK",
        "selected_rank_band": "401-600" if band else "MISSING_OR_UNRANKED",
        "surface": "HARD", "tournament_tier": "CHALLENGER",
    }


def test_unknown_is_retained_not_silently_counted_as_outside_rank_band():
    data = scored([row(1, band=True, win=False), row(2, band=False, win=True)],
                  "manager", "MANAGER")
    snap = snapshot(data)
    assert (snap["evaluated"], snap["excluded"], snap["retained"]) == (2, 1, 1)
    assert snap["lost_winning_selections"] == 0
    assert snap["accuracy_delta"] == .5


def test_lost_winners_and_void_not_treated_as_losses():
    data = [row(1, win=True), row(2, band=False, win=True),
            dict(row(3, win=False), outcome_status="WALKOVER", winner_side=None)]
    result = snapshot(scored(data, "manager", "MANAGER"))
    assert result["baseline_wins"] == 2
    assert result["evaluated"] == 2
    assert result["lost_winning_selections"] == 1
    assert result["retention"] == .5


def test_insufficient_train_and_holdout_never_approves_filter():
    rows = [row(1, date="2026-09-01"), row(2, band=False, win=True, date="2026-10-01")]
    report = assess_predeclared_filter(
        rows, agent="manager", population="MANAGER",
        holdout_starts_at="2026-09-15T00:00:00Z"
    )
    assert report["train"]["evaluated"] == 1
    assert report["holdout"]["evaluated"] == 1
    assert report["holdout_delta_bootstrap_95"] is None
    assert not report["candidate_training_gate"]
    assert not report["holdout_statistical_gate"]
    assert report["decision"] == "NO_LIVE_RULE_CHANGE"
    assert report["roi"] is None


def test_repeated_canonical_match_rejected():
    data = [row(1), row(1)]
    with pytest.raises(ValueError, match="one selection"):
        scored(data, "manager", "MANAGER")


def test_paired_bootstrap_is_deterministic_and_never_invents_small_sample_ci():
    assert paired_bootstrap_delta(scored([row(1)], "manager", "MANAGER")) is None
    data = [row(i, band=(i % 2 == 0), win=(i % 3 == 0), date="2026-10-01")
            for i in range(80)]
    samples = scored(data, "manager", "MANAGER")
    assert paired_bootstrap_delta(samples) == paired_bootstrap_delta(samples)


def test_first_production_split_by_pick_time_not_event_result_date():
    rows = [row(i, band=(i % 2 == 0), win=(i % 3 == 0),
                date="2026-09-01" if i < 70 else "2026-10-01")
            for i in range(100)]
    report = assess_predeclared_filter(
        rows, agent="manager", population="MANAGER",
        holdout_starts_at="2026-09-15T00:00:00Z"
    )
    assert report["train"]["evaluated"] == 70
    assert report["holdout"]["evaluated"] == 30
    assert report["holdout_statistical_gate"] is False
    assert "2026-10" in report["monthly_holdout"]
