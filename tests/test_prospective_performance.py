from __future__ import annotations

from decimal import Decimal

import pytest

import sports_edge_tennis.performance.prospective as prospective
from sports_edge_tennis.performance import load_prospective_performance


def test_empty_database_has_no_prospective_performance(db):
    summary = load_prospective_performance(db)
    assert summary.initialized is False
    assert summary.total_decisions == 0
    assert summary.settled_bets == 0


def test_prospective_metrics_separate_model_scoring_from_wager_roi(monkeypatch):
    rows = [
        prospective._Row(
            decision_status="BET",
            selected_side=1,
            probability=0.60,
            market_price=Decimal("2.00"),
            settlement_status="WON",
            winner_side=1,
        ),
        prospective._Row(
            decision_status="BET",
            selected_side=2,
            probability=0.55,
            market_price=Decimal("1.90"),
            settlement_status="LOST",
            winner_side=1,
        ),
        prospective._Row(
            decision_status="BET",
            selected_side=1,
            probability=0.62,
            market_price=Decimal("2.10"),
            settlement_status=None,
            winner_side=None,
        ),
        prospective._Row(
            decision_status="VETOED",
            selected_side=1,
            probability=0.70,
            market_price=Decimal("2.05"),
            settlement_status=None,
            winner_side=1,
        ),
        prospective._Row(
            decision_status="NO_BET",
            selected_side=2,
            probability=0.65,
            market_price=None,
            settlement_status=None,
            winner_side=2,
        ),
    ]
    monkeypatch.setattr(prospective, "_load_rows", lambda conn: rows)

    summary = prospective.load_prospective_performance(object())

    assert summary.initialized is True
    assert summary.total_decisions == 5
    assert summary.bet_decisions == 3
    assert summary.no_bet_decisions == 1
    assert summary.vetoed_decisions == 1
    assert summary.settled_bets == 2
    assert summary.pending_bets == 1
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.one_unit_profit == Decimal("0.00")
    assert summary.roi == Decimal("0.00")
    assert summary.hit_rate == pytest.approx(0.5)

    # Calibration scores every matured Manager consensus call, including the
    # VETOED and NO_BET calls, but excludes the still-unmatured pending match.
    assert summary.calibration_sample == 4
    expected_brier = (
        (0.60 - 1) ** 2
        + (0.55 - 0) ** 2
        + (0.70 - 1) ** 2
        + (0.65 - 1) ** 2
    ) / 4
    assert summary.brier_score == pytest.approx(expected_brier)
    assert summary.log_loss is not None
    assert summary.ece is not None
    assert sum(bin_.sample for bin_ in summary.bins) == 4


def test_roi_uses_one_unit_stakes_and_voids_return_zero_profit(monkeypatch):
    rows = [
        prospective._Row("BET", 1, 0.60, Decimal("2.50"), "WON", 1),
        prospective._Row("BET", 1, 0.60, Decimal("2.00"), "LOST", 2),
        prospective._Row("BET", 1, 0.60, Decimal("1.80"), "VOID", None),
        prospective._Row("BET", 1, 0.60, Decimal("1.90"), "CANCELLED", None),
    ]
    monkeypatch.setattr(prospective, "_load_rows", lambda conn: rows)

    summary = prospective.load_prospective_performance(object())

    # +1.50 - 1.00 + 0 + 0 over four one-unit settled stakes.
    assert summary.one_unit_profit == Decimal("0.50")
    assert summary.roi == Decimal("0.125")
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.voids == 1
    assert summary.cancelled == 1
