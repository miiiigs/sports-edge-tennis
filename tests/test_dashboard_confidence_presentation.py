"""Presentation-only confidence labelling/precision tests.

Stored probabilities and JSON API values must remain unchanged; only the
dashboard labels and displayed precision change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sports_edge_tennis.dashboard import app as dashboard_app
from sports_edge_tennis.dashboard import presentation as pres
from sports_edge_tennis.dashboard.model_pick_history import ModelPickRow
from sports_edge_tennis.dashboard.picks import (
    MODEL_PICK,
    WIDE_SPREAD_CLASSIFICATION,
    PickCard,
)


def _manager_card(probability: str) -> PickCard:
    return PickCard(
        match_id=1,
        kind=MODEL_PICK,
        classification="HIGH_CONFIDENCE_PICK",
        player="A. Player",
        opponent="B. Player",
        win_probability=Decimal(probability),
        tournament="Test Open",
        competition_tier="ATP_TOUR",
        scheduled_start=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )


def _wide_card(average: str) -> PickCard:
    return PickCard(
        match_id=2,
        kind=MODEL_PICK,
        classification=WIDE_SPREAD_CLASSIFICATION,
        player="A. Player",
        opponent="B. Player",
        win_probability=None,
        tournament="Test Open",
        competition_tier="CHALLENGER",
        scheduled_start=datetime(2026, 9, 30, tzinfo=timezone.utc),
        primary_a1_probability=Decimal("0.92"),
        primary_a2_probability=Decimal("0.86"),
        primary_average_probability=Decimal(average),
    )


def test_whole_percent_truncates_and_keeps_threshold_side():
    assert pres.whole_percent(Decimal("0.8997")) == "89%"
    assert pres.whole_percent(Decimal("0.9999")) == "99%"
    assert pres.whole_percent(Decimal("0.5999")) == "59%"
    assert pres.whole_percent(Decimal("0.6000")) == "60%"
    assert pres.whole_percent(None) == "—"
    assert float(pres.whole_percent(Decimal("0.5999")).rstrip("%")) < 60
    assert float(pres.whole_percent(Decimal("0.6000")).rstrip("%")) >= 60


def test_manager_card_labels_and_display():
    card = _manager_card("0.8997")
    assert card.probability_label == "Model Probability · Uncalibrated"
    assert card.probability_basis_label == "Manager Consensus"
    assert "Calibration" in card.probability_note
    assert card.probability_display_text == "89%"
    # One-decimal API/raw-facing property is unchanged.
    assert card.probability_text == "89.9%"


def test_wide_card_labels_and_display():
    card = _wide_card("0.8970")
    assert card.probability_label == "Wide-Spread Tracking Confidence"
    assert card.probability_basis_label == "A1/A2 Tracking Average"
    assert card.probability_note.startswith("Tracking confidence")
    assert card.primary_average_probability_display_text == "89%"
    assert card.primary_average_probability_text == "89.7%"


def test_api_payload_keeps_one_decimal_while_display_is_whole():
    card = _manager_card("0.7000")
    payload = dashboard_app._pick_payload(card)
    assert payload["win_probability"] == "70.0%"
    assert card.probability_display_text == "70%"
    # The stored probability Decimal is never mutated by presentation.
    assert card.win_probability == Decimal("0.7000")


def test_model_pick_row_labels_and_display():
    row = ModelPickRow(
        manager_consensus_result_id=1,
        picked_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        match_start_at=None,
        tournament="Test",
        player_1="A",
        player_2="B",
        selection="A",
        consensus_probability=Decimal("0.8474"),
        result_status="WON",
        is_value_bet=False,
        manager_policy_version="v1",
        model_regime_key="REGIME",
        pick_type="MANAGER_CONSENSUS",
    )
    assert row.probability_source_label == "Model Probability · Uncalibrated"
    assert row.probability_display_text == "84%"

    wide = ModelPickRow(
        manager_consensus_result_id=2,
        picked_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        match_start_at=None,
        tournament="Test",
        player_1="A",
        player_2="B",
        selection="A",
        consensus_probability=Decimal("0.8975"),
        result_status="WON",
        is_value_bet=False,
        manager_policy_version="v1",
        model_regime_key="REGIME",
        pick_type="A1_A2_WIDE_SPREAD",
    )
    assert wide.probability_source_label == (
        "Wide-Spread Tracking Confidence · A1/A2 Tracking Average"
    )
    assert wide.probability_display_text == "89%"
