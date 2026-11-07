"""Historical walk-forward invariants; no credentials or production data needed."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from sports_edge_tennis.performance.v2_quality_research import FrozenPick, POLICIES
from sports_edge_tennis.performance.v2_walkforward import walk_forward


def _t(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def _p(mid: int, *, day: int, result: str = "WON", raw: float = .76,
       observed: datetime | None = None, ingested: datetime | None = None) -> FrozenPick:
    finished = observed if observed is not None else _t(day, 10)
    return FrozenPick(
        match_id=mid, day=date(2026, 10, day),
        picked_at=_t(day - 1, 6),
        kind="MANAGER_CONSENSUS", side=1, raw=raw,
        surface="HARD", tier="ATP_TOUR",
        a1_side=1, a2_side=1, a1=.76, a2=.77,
        a3_status="ABSTAINED", a3_side=None,
        result=result, result_as_of=finished,
        result_source_observed_at=finished,
        result_ingested_at=ingested or finished,
    )


def test_daily_training_cutoff_only_includes_results_visible_before_midnight():
    early = _p(1, day=27, observed=_t(28, 0))
    later = _p(2, day=27, observed=_t(30, 0))
    win = _p(3, day=29, observed=_t(30, 10))
    loss = _p(4, day=29, result="LOST", raw=.69, observed=_t(30, 10))
    r = walk_forward(
        (early, later, win, loss),
        start=date(2026, 10, 27), end=date(2026, 10, 30),
        evaluated_at=_t(31),
    )
    folds = {fold["date"]: fold for fold in r["folds"]}
    assert folds["2026-10-27"]["calibration_training"]["prior_visible_graded"] == 0
    assert folds["2026-10-28"]["calibration_training"]["prior_visible_graded"] == 0
    assert folds["2026-10-29"]["calibration_training"]["prior_visible_graded"] == 1
    assert folds["2026-10-30"]["calibration_training"]["prior_visible_graded"] == 1
    assert (folds["2026-10-29"]["policies"]["original"]["won"],
            folds["2026-10-29"]["policies"]["original"]["lost"]) == (1, 1)
    assert (folds["2026-10-29"]["policies"]["confidence70"]["won"],
            folds["2026-10-29"]["policies"]["confidence70"]["lost"]) == (1, 0)
    assert folds["2026-10-29"]["policies"]["confidence70"]["avoided_losses"] == 1
    assert r["summary"]["original"]["picks"] == 4
    assert r["summary"]["confidence70"]["picks"] == 3
    assert r["summary"]["v2_calibrated"]["picks"] == 0
    assert r["rule_selection"] == "PREDECLARED_FIXED_CANDIDATES_ONLY"
    assert r["not_prospective_validation"] is True
    assert r["live_selection_changed"] is False


def test_result_ingested_after_folds_cutoff_is_excluded_from_training():
    before = _p(1, day=27, observed=_t(27, 12))
    delayed = replace(before, match_id=2, result_ingested_at=_t(29, 9))
    outcome = _p(3, day=29, result="LOST")
    r = walk_forward(
        (before, delayed, outcome),
        start=date(2026, 10, 29), end=date(2026, 10, 29),
        evaluated_at=_t(31),
    )
    assert r["folds"][0]["calibration_training"]["prior_visible_graded"] == 1
    assert r["folds"][0]["policies"]["original"]["lost"] == 1


def test_later_known_truth_is_pending_when_scored_at_earlier_instant():
    # A future-ingested WON cannot be displayed in a Nov 07 09:00 local report.
    future = _p(1, day=29, result="WON", observed=_t(29, 8),
                ingested=datetime(2026, 11, 8, 0, tzinfo=UTC))
    actual = _p(2, day=29, result="LOST", raw=.8, observed=_t(29, 8))
    r = walk_forward(
        (future, actual),
        start=date(2026, 10, 29), end=date(2026, 10, 29),
        evaluated_at=datetime(2026, 11, 7, 1, tzinfo=UTC),
    )
    orig = r["folds"][0]["policies"]["original"]
    assert (orig["won"], orig["lost"], orig["pending"]) == (0, 1, 1)
    assert r["summary"]["original"]["graded"] == 1
    assert orig["passes_73_daily"] is False


def test_future_unissued_predictions_are_not_treated_as_historical():
    first = _p(1, day=29)
    later = replace(_p(2, day=30), picked_at=_t(31))
    r = walk_forward((first, later),
        start=date(2026, 10, 29), end=date(2026, 10, 30),
        evaluated_at=_t(30, 6),
    )
    assert r["summary"]["original"]["picks"] == 1
    assert r["folds"][1]["policies"]["original"]["picks"] == 0


def test_no_rewriting_and_no_posthoc_outcome_gate():
    pick = _p(1, day=29, result="WON")
    lost = replace(pick, match_id=2, result="LOST")
    original = (pick, lost)
    r = walk_forward(original,
        start=date(2026, 10, 29), end=date(2026, 10, 29),
        evaluated_at=_t(31),
    )
    assert original == (pick, lost)
    assert set(r["summary"]) == set(POLICIES)
    assert r["summary"]["v2_agreement"]["picks"] == 2
    assert r["folds"][0]["policies"]["v2_agreement"]["won"] == 1
    assert r["folds"][0]["policies"]["v2_agreement"]["lost"] == 1
    assert not r["folds"][0]["policies"]["v2_agreement"]["passes_73_daily"]


def test_rejects_bad_interval_timezones_duplicates():
    p = _p(1, day=29)
    with pytest.raises(ValueError, match="reversed"):
        walk_forward((p,), start=date(2026, 10, 30),
                     end=date(2026, 10, 29), evaluated_at=_t(31))
    with pytest.raises(ValueError, match="timezone"):
        walk_forward((p,), start=date(2026, 10, 29),
                     end=date(2026, 10, 29), evaluated_at=datetime(2026, 10, 31))
    with pytest.raises(ValueError, match="duplicate"):
        walk_forward((p, p), start=date(2026, 10, 29),
                     end=date(2026, 10, 29), evaluated_at=_t(31))
    with pytest.raises(ValueError, match="beyond"):
        walk_forward((p,), start=date(2026, 11, 1),
                     end=date(2026, 11, 2), evaluated_at=_t(31))
