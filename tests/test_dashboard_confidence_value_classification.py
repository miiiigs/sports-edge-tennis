from sports_edge_tennis.dashboard.presentation import (
    recommendation_classification,
    recommendation_label,
)


def test_manager_non_consensus_remains_no_bet_recommendation():
    assert recommendation_classification("NO_BET", "BET") == "NO_BET"
    assert recommendation_label("NO_BET") == "No Bet"


def test_manager_consensus_with_value_bet_is_high_confidence_plus_value():
    classification = recommendation_classification("CONSENSUS", "BET")
    assert classification == "HIGH_CONFIDENCE_VALUE"
    assert recommendation_label(classification) == "High-Confidence + Value"


def test_manager_consensus_without_value_remains_visible_high_confidence_pick():
    classification = recommendation_classification("CONSENSUS", "NO_BET")
    assert classification == "HIGH_CONFIDENCE_PICK"
    assert recommendation_label(classification) == "High-Confidence Pick"


def test_manager_consensus_without_market_decision_remains_price_pending():
    classification = recommendation_classification("CONSENSUS", None)
    assert classification == "PRICE_PENDING"
    assert recommendation_label(classification) == "High-Confidence Pick · Price Pending"
