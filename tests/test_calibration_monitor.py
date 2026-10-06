"""Tests for the read-only daily calibration monitor (pure aggregation)."""

from __future__ import annotations

from datetime import date

from sports_edge_tennis.performance.calibration_monitor import (
    PickObservation,
    _LEDGER_SQL_TEMPLATE,
    build_calibration_health,
    hypothetical_filter,
)


def _obs(
    day: date,
    match_id: int,
    probability: float,
    outcome: int | None,
    *,
    ptype: str = "MANAGER_CONSENSUS",
    surface: str | None = "HARD",
    a1: float | None = 0.9,
    a2: float | None = 0.9,
    result_status: str | None = None,
    value_bet: bool = False,
    odds: float | None = None,
    clv: float | None = None,
) -> PickObservation:
    if result_status is None:
        result_status = {1: "WON", 0: "LOST", None: "PENDING"}[outcome]
    return PickObservation(
        prediction_date=day,
        manila_date=day,
        match_id=match_id,
        player=f"P{match_id}",
        ptype=ptype,
        tier="CHALLENGER",
        surface=surface,
        probability=probability,
        outcome=outcome,
        result_status=result_status,
        a1=a1,
        a2=a2,
        a3_status="ABSTAINED",
        is_value_bet=value_bet,
        odds=odds,
        clv=clv,
    )


def test_windows_and_manager_wide_separation_exclude_pending():
    ref = date(2026, 10, 2)
    obs = [
        _obs(date(2026, 10, 2), 1, 0.85, 1),
        _obs(date(2026, 10, 2), 2, 0.82, None, ptype="A1_A2_WIDE_SPREAD"),
        _obs(date(2026, 9, 20), 3, 0.70, 0),
    ]
    h = build_calibration_health(obs, reference_date=ref)
    today = h["windows"]["TODAY"]
    assert today["combined"]["picks"] == 2
    assert today["combined"]["scored"] == 1  # pending excluded from scored
    assert today["manager"]["scored"] == 1
    assert today["wide"]["scored"] == 0
    assert today["wide"]["pending"] == 1
    assert h["windows"]["ALL_CLEAN_PRODUCTION"]["combined"]["scored"] == 2


def test_confidence_bucket_boundaries():
    ref = date(2026, 10, 2)
    obs = [
        _obs(ref, 1, 0.60, 1),
        _obs(ref, 2, 0.6499, 0),
        _obs(ref, 3, 0.65, 1),
        _obs(ref, 4, 0.8999, 1),
        _obs(ref, 5, 0.90, 0),
    ]
    buckets = build_calibration_health(obs, reference_date=ref)["windows"]["TODAY"][
        "confidence_buckets"
    ]
    assert buckets["60-65"]["scored"] == 2
    assert buckets["65-70"]["scored"] == 1
    assert buckets["85-90"]["scored"] == 1
    assert buckets["90+"]["scored"] == 1


def test_high_confidence_health_flags():
    ref = date(2026, 10, 2)
    green = [_obs(ref, i, 0.82, 1) for i in range(25)]
    assert build_calibration_health(green, reference_date=ref)["windows"][
        "ALL_CLEAN_PRODUCTION"
    ]["high_confidence"]["0.80"]["health"] == "GREEN"

    red = [_obs(ref, i, 0.95, 0) for i in range(25)]
    assert build_calibration_health(red, reference_date=ref)["windows"][
        "ALL_CLEAN_PRODUCTION"
    ]["high_confidence"]["0.90"]["health"] == "RED"

    amber = [_obs(ref, i, 0.85, 1 if i < 3 else 0) for i in range(5)]
    row = build_calibration_health(amber, reference_date=ref)["windows"][
        "ALL_CLEAN_PRODUCTION"
    ]["high_confidence"]["0.80"]
    assert row["health"] == "AMBER_INSUFFICIENT_SAMPLE"


def test_daily_top_selection_and_tie_break():
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    obs = [
        _obs(d1, 10, 0.80, 1),
        _obs(d1, 11, 0.95, 0),  # top of day 1 loses
        _obs(d2, 12, 0.90, 1),
        _obs(d2, 13, 0.90, 0),  # tie -> lower match_id (12) wins
    ]
    daily = build_calibration_health(obs, reference_date=d2)["daily_top_pick"]
    assert daily["decided"] == 2
    assert daily["wins"] == 1 and daily["losses"] == 1
    assert daily["current_loss_streak"] == 0
    assert daily["longest_loss_streak"] == 1
    assert daily["days"][1]["match_id"] == 12  # tie-break by match_id


def test_wide_divergence_and_unknown_surface_cohorts():
    ref = date(2026, 10, 2)
    obs = [
        _obs(ref, 1, 0.75, 1, ptype="A1_A2_WIDE_SPREAD", a1=0.80, a2=0.79),  # <2.5pt win
        _obs(ref, 2, 0.80, 0, ptype="A1_A2_WIDE_SPREAD", a1=0.95, a2=0.70, surface=None),  # >10pt loss, unknown
        _obs(ref, 3, 0.72, 0, ptype="A1_A2_WIDE_SPREAD", a1=0.90, a2=0.65, surface=None),  # >10pt loss unknown
    ]
    allw = build_calibration_health(obs, reference_date=ref)["windows"]["ALL_CLEAN_PRODUCTION"]
    assert allw["wide_divergence"]["<2.5pt"]["scored"] == 1
    assert allw["wide_divergence"][">10pt"]["scored"] == 2
    assert allw["unknown_surface_wide"]["scored"] == 2
    assert allw["known_surface_wide"]["scored"] == 1


def test_clv_summary_and_flags():
    ref = date(2026, 10, 2)
    obs = [
        _obs(ref, 1, 0.80, 1, value_bet=True, odds=1.5, clv=-0.05),
        _obs(ref, 2, 0.82, 0, value_bet=True, odds=1.4, clv=-0.02),
    ]
    h = build_calibration_health(obs, reference_date=ref)
    clv = h["windows"]["ALL_CLEAN_PRODUCTION"]["clv"]
    assert clv["value_bets"] == 2 and clv["clv_n"] == 2
    assert clv["clv_negative"] == 2 and clv["clv_positive"] == 0
    assert "INSUFFICIENT_CALIBRATION_SAMPLE" in h["flags"]
    assert "NEGATIVE_CLV_AVAILABLE" in h["flags"]


def test_hypothetical_filters_are_read_only_counterfactuals():
    ref = date(2026, 10, 2)
    obs = [
        _obs(ref, 1, 0.70, 1, ptype="MANAGER_CONSENSUS"),
        _obs(ref, 2, 0.85, 0, ptype="A1_A2_WIDE_SPREAD", surface=None, a1=0.95, a2=0.75),
        _obs(ref, 3, 0.80, 1, ptype="A1_A2_WIDE_SPREAD", surface="CLAY", a1=0.82, a2=0.81),
    ]
    unknown = hypothetical_filter(obs, exclude_unknown_surface_wide=True)
    assert unknown["removed_picks"] == 1 and unknown["retained_picks"] == 2
    divergence = hypothetical_filter(obs, exclude_high_divergence_wide=True)
    assert divergence["removed_picks"] == 1 and divergence["retained_picks"] == 2
    both = hypothetical_filter(
        obs, exclude_unknown_surface_wide=True, exclude_high_divergence_wide=True
    )
    assert both["retained_picks"] == 2
    # No mutation of inputs.
    assert len(obs) == 3


def test_ledger_sql_is_production_only():
    sql = _LEDGER_SQL_TEMPLATE.lower()
    assert "aligned_model_picks" in sql
    assert "experiment_id is null" in sql
    assert "predictive_agent_result_v1" in sql
    assert "run_status='completed'" in sql
