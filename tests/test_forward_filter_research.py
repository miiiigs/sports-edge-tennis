"""No-DB tests for the preregistered forward Model Picks monitor."""
from datetime import date

import pytest

from sports_edge_tennis.performance.calibration_monitor import PickObservation
from sports_edge_tennis.performance.forward_filter_research import (
    FORWARD_START_UTC,
    build_forward_filter_report,
)


def _pick(match_id, day, *, ptype="A1_A2_WIDE_SPREAD", surface="HARD", a1=0.70, a2=0.69, won=True):
    return PickObservation(
        prediction_date=day,
        manila_date=day,
        match_id=match_id,
        player="Example",
        ptype=ptype,
        tier="CHALLENGER",
        surface=surface,
        probability=0.70,
        outcome=int(won),
        result_status="WON" if won else "LOST",
        a1=a1,
        a2=a2,
        a3_status="ABSTAIN",
        is_value_bet=False,
        odds=None,
        clv=None,
    )


def test_forward_cohort_ignores_old_picks_and_does_not_promote_small_samples():
    start = FORWARD_START_UTC
    picks = [
        _pick(1, date(2026, 10, 17), won=False),  # Historical discovery data excluded.
        _pick(2, start, surface=None, won=False),
        _pick(3, start, a1=0.85, a2=0.70, won=False),
        _pick(4, start, won=True),
        _pick(5, start, ptype="MANAGER_CONSENSUS", won=True),
        _pick(6, date(2026, 10, 20), won=False),  # After report cutoff.
    ]
    report = build_forward_filter_report(picks, report_date_utc=start)
    assert report["status"] == "RESEARCH_ONLY"
    assert report["enrolled_picks"] == 4
    assert report["settled_picks"] == 4
    assert report["variants"]["baseline"]["wins"] == 2
    assert report["variants"]["baseline"]["losses"] == 2
    assert report["variants"]["exclude_unknown_surface_wide"]["removed"] == 1
    assert report["variants"]["exclude_high_divergence_wide"]["removed"] == 1
    assert report["variants"]["exclude_both"]["removed"] == 2
    assert report["variants"]["exclude_both"]["wins"] == 2
    assert report["variants"]["exclude_both"]["losses"] == 0
    assert report["variants"]["exclude_both"]["research_status"] == "INSUFFICIENT_EVIDENCE"


def test_no_shadow_cohort_before_registration():
    report = build_forward_filter_report([], report_date_utc=date(2026, 10, 17))
    assert report["status"] == "NOT_STARTED"
    assert report["variants"] == {}


def test_duplicate_match_id_fails_closed():
    pick = _pick(88, FORWARD_START_UTC)
    with pytest.raises(ValueError, match="duplicate sporting matches"):
        build_forward_filter_report([pick, pick], report_date_utc=FORWARD_START_UTC)
