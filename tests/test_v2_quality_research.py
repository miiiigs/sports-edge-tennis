"""Contract tests for leakage-safe Tennis V2 quality research."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports_edge_tennis.performance.v2_quality_research import (
    CALIBRATION_MIN_GRADED, FrozenPick, POLICY_VERSION, _eligible_before, allows,
    audit, parse_export,
)


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def _pick(mid: int, *, day: int = 29, raw: float = .76, kind: str = "MANAGER_CONSENSUS",
          result: str = "WON", a1: float | None = .76, a2: float | None = .77,
          a1_side: int | None = 1, a2_side: int | None = 1,
          tier: str = "ATP_TOUR", surface: str = "HARD",
          result_as_of: datetime | None = None) -> FrozenPick:
    return FrozenPick(
        match_id=mid, day=date(2026, 10, day), picked_at=_dt(day - 1), kind=kind,
        side=1, raw=raw, surface=surface, tier=tier,
        a1_side=a1_side, a2_side=a2_side, a1=a1, a2=a2,
        a3_status="ABSTAINED", a3_side=None, result=result,
        result_as_of=result_as_of or _dt(day, 10),
        result_source_observed_at=result_as_of or _dt(day, 10),
        result_ingested_at=result_as_of or _dt(day, 10),
    )


def test_v2_gates_are_predeclared_not_fitted_to_oct30_losses():
    good = _pick(1)
    assert allows(good, "original")
    assert allows(good, "confidence70")
    assert allows(good, "v2_agreement")
    assert allows(good, "v2_tier_guard")
    assert not allows(good, "v2_a3_confirmed")
    assert allows(replace(good, a3_status="PREDICTED", a3_side=1), "v2_a3_confirmed")
    assert not allows(replace(good, a3_status="PREDICTED", a3_side=2), "v2_a3_confirmed")
    # A3 abstention must be neutral, not an automatic rejection. A verified
    # dissent is counted only as a hypothetical veto, not as an accuracy lift.
    assert allows(good, "v2_a3_dissent_veto")
    assert allows(replace(good, a3_status="PREDICTED", a3_side=1), "v2_a3_dissent_veto")
    assert not allows(replace(good, a3_status="PREDICTED", a3_side=2), "v2_a3_dissent_veto")
    assert not allows(replace(good, a3_status="PREDICTED", a3_side=None), "v2_a3_dissent_veto")
    assert not allows(replace(good, a3_status=None), "v2_a3_dissent_veto")
    assert not allows(replace(good, a3_status="ABSTAINED", raw=.69), "v2_a3_dissent_veto")
    assert not allows(good, "v2_calibrated")  # requires prior-trained calibrator
    assert not allows(replace(good, raw=.699), "confidence70")
    assert not allows(replace(good, a2_side=2), "v2_agreement")
    assert not allows(replace(good, a1=.90, a2=.70), "v2_agreement")
    assert not allows(replace(good, surface="UNKNOWN"), "v2_agreement")
    assert not allows(replace(good, a1=None), "v2_agreement")
    assert not allows(replace(good, tier="UNKNOWN"), "v2_tier_guard")
    challenger = replace(good, tier="CHALLENGER", raw=.75)
    assert allows(challenger, "v2_agreement")
    assert not allows(challenger, "v2_tier_guard")
    assert allows(replace(challenger, raw=.76), "v2_tier_guard")
    with pytest.raises(ValueError):
        allows(good, "unapproved_magic_gate")


def test_holdout_truth_does_not_choose_a_gate_or_train_calibrator():
    cutoff = datetime(2026, 10, 29, 0, tzinfo=timezone(timedelta(hours=8)))
    earlier = _pick(1, day=27)
    assert _eligible_before(earlier, cutoff)
    late = replace(_pick(2, day=27), result_as_of=_dt(29, 5))
    assert not _eligible_before(late, cutoff)
    assert not _eligible_before(replace(earlier, picked_at=_dt(29, 4)), cutoff)
    assert not _eligible_before(replace(earlier, result_source_observed_at=None), cutoff)
    assert not _eligible_before(replace(earlier, result_ingested_at=None), cutoff)
    assert not _eligible_before(replace(earlier, result_ingested_at=_dt(29, 5)), cutoff)

    winner = _pick(10, day=29, result="WON")
    loss = replace(winner, match_id=11, result="LOST", raw=.69)
    sample = (earlier, late, winner, loss)
    r = audit(sample, start=date(2026, 10, 29), end=date(2026, 10, 30), as_of=date(2026, 11, 7))
    assert r["training"]["graded"] == 1
    assert not r["training"]["calibrator_eligible"]
    assert r["policies"]["original"]["won"] == 1
    assert r["policies"]["original"]["lost"] == 1
    assert r["policies"]["confidence70"]["won"] == 1
    assert r["policies"]["confidence70"]["lost"] == 0
    assert r["policies"]["confidence70"]["missed_winners"] == 0
    assert r["policies"]["confidence70"]["avoided_losses"] == 1
    assert r["policies"]["v2_calibrated"]["picks"] == 0
    assert r["policies"]["confidence70"]["daily"]["2026-10-29"]["passes_73_daily"] is False
    assert r["policy_version"] == POLICY_VERSION
    assert r["status"] == "RESEARCH_ONLY_NOT_DEPLOYED"


def test_training_minimum_and_holdout_as_of_are_reproducible():
    # Synthetic source-observed results strictly before the Nov 1 holdout.
    historical = tuple(
        replace(
            _pick(i, day=27, raw=.62 + (i % 15) * .02,
                  result="WON" if (i % 15) >= 5 else "LOST"),
            picked_at=_dt(26), result_as_of=_dt(28), result_source_observed_at=_dt(28),
        )
        for i in range(1, CALIBRATION_MIN_GRADED + 1)
    )
    valid = _pick(1001, day=30)
    all_rows = historical + (valid,)
    report = audit(all_rows, start=date(2026, 10, 30), end=date(2026, 10, 30), as_of=date(2026, 11, 1))
    assert report["training"]["graded"] == CALIBRATION_MIN_GRADED
    assert report["holdout"]["as_of"] == "2026-11-01"
    # No assertion on improvement: calibration may improve or worsen on unseen data.
    assert report["holdout_calibration"]["raw_brier"] is not None
    assert report["holdout_calibration"]["raw_log_loss"] is not None
    # A later result must not affect calibration based on old results.
    patched = tuple(
        replace(p, result_as_of=_dt(31), result_source_observed_at=_dt(31))
        if p.match_id == 1 else p for p in historical
    ) + (valid,)
    changed = audit(patched, start=date(2026, 10, 30), end=date(2026, 10, 30), as_of=date(2026, 11, 1))
    assert changed["training"]["graded"] == CALIBRATION_MIN_GRADED - 1


def test_outcome_not_a_gate_feature_and_pending_never_scored():
    good = _pick(1, result="WON")
    lost = replace(good, match_id=2, result="LOST")
    assert allows(good, "v2_tier_guard") == allows(lost, "v2_tier_guard")
    pending = replace(good, match_id=3, result="PENDING")
    report = audit((good, lost, pending), start=date(2026, 10, 29),
                   end=date(2026, 10, 29), as_of=date(2026, 11, 7))
    day = report["policies"]["original"]["daily"]["2026-10-29"]
    assert (day["won"], day["lost"], day["pending"], day["graded"]) == (1, 1, 1, 2)
    assert not day["passes_73_daily"]


def test_export_rejects_duplicate_invalid_probability_and_missing_timestamps():
    row = dict(
        match_id=1, manila_date="2026-10-29",
        picked_at="2026-10-28T09:00:00+08:00",
        aligned_pick_type="A1_A2_WIDE_SPREAD", selected_side=1,
        aligned_probability="0.70", a1_prob="0.72", a2_prob="0.68",
        a1_side=1, a2_side=1, surface="HARD", tier="CHALLENGER",
        result_status="VOID", result_observed_at="2026-10-30T01:00:00+08:00",
        result_source_observed_at="2026-10-30T01:00:00+08:00",
        result_ingested_at="2026-10-30T01:15:00+08:00",
    )
    parsed = parse_export({"data": [row]})
    assert len(parsed) == 1
    assert parsed[0].result == "VOID"
    assert parsed[0].raw == .70
    with pytest.raises(ValueError, match="duplicate"):
        parse_export({"data": [row, row]})
    with pytest.raises(ValueError, match="probability"):
        parse_export({"data": [{**row, "aligned_probability": "NaN"}]})
    with pytest.raises(ValueError, match="timezone"):
        parse_export({"data": [{**row, "picked_at": "2026-10-29T00:00:00"}]})
    with pytest.raises(ValueError, match="outcome"):
        parse_export({"data": [{**row, "result_status": "WIN_ASSUMED"}]})


def test_read_only_calibration_dataset_exposes_both_observation_timestamps():
    sql = (Path(__file__).parents[1] / "ops/calibration_dataset.sql").read_text()
    assert "mro.as_of AS result_observed_at" in sql
    assert "mro.source_observed_at AS result_source_observed_at" in sql
    assert "mro.ingested_at AS result_ingested_at" in sql
    assert "source_observed_at<=p.picked_at" in sql
    assert "SELECT DISTINCT ON (match_id)" in sql
    assert "UPDATE match_result_observations" not in sql
    assert "DELETE FROM match_result_observations" not in sql


def test_a3_veto_shadow_separates_agreement_from_observed_outcome():
    p = _pick(1, result="WON")
    dissenting_winner = replace(p, match_id=2, a3_status="PREDICTED",
                                a3_side=2, result="WON")
    dissenting_loser = replace(p, match_id=3, a3_status="PREDICTED",
                               a3_side=2, result="LOST")
    agreed_loser = replace(p, match_id=4, a3_status="PREDICTED",
                           a3_side=1, result="LOST")
    rows = (p, dissenting_winner, dissenting_loser, agreed_loser)
    report = audit(rows, start=date(2026, 10, 29),
                   end=date(2026, 10, 29), as_of=date(2026, 11, 7))
    a1a2 = report["policies"]["v2_agreement"]
    veto = report["policies"]["v2_a3_dissent_veto"]
    assert a1a2["picks"] == 4
    assert veto["picks"] == 2
    assert veto["won"] == 1
    assert veto["lost"] == 1
    assert veto["missed_winners"] == 1
    assert veto["avoided_losses"] == 1
    # Altering observed outcomes must never change the pre-match gate.
    for row in rows:
        flipped = replace(row, result="LOST" if row.result == "WON" else "WON")
        assert allows(row, "v2_a3_dissent_veto") == allows(flipped, "v2_a3_dissent_veto")
