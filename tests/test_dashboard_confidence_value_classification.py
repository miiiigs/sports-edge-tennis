"""Backend classification -> client concept mapping.

``recommendation_classification`` remains the authoritative backend reading of a
match. ``picks.pick_kind`` is the only place that collapses it into the two
concepts the client sees, so both halves are pinned here.
"""

from sports_edge_tennis.dashboard.picks import (
    BLOCKED,
    KIND_LABELS,
    MODEL_PICK,
    VALUE_BET,
    pick_kind,
)
from sports_edge_tennis.dashboard.presentation import recommendation_classification


def test_manager_non_consensus_remains_no_bet_recommendation():
    assert recommendation_classification("NO_BET", "BET") == "NO_BET"
    # A non-consensus match is not a pick and never reaches the client.
    assert pick_kind("NO_BET") is None


def test_manager_consensus_with_value_bet_is_a_value_bet():
    classification = recommendation_classification("CONSENSUS", "BET")
    assert classification == "HIGH_CONFIDENCE_VALUE"
    assert pick_kind(classification) == VALUE_BET
    assert KIND_LABELS[VALUE_BET] == "VALUE BET"


def test_manager_consensus_without_value_remains_a_visible_model_pick():
    classification = recommendation_classification("CONSENSUS", "NO_BET")
    assert classification == "HIGH_CONFIDENCE_PICK"
    assert pick_kind(classification) == MODEL_PICK
    assert KIND_LABELS[MODEL_PICK] == "MODEL PICK"


def test_manager_consensus_without_market_decision_remains_price_pending():
    classification = recommendation_classification("CONSENSUS", None)
    assert classification == "PRICE_PENDING"
    # Still a model pick to the client: the winner prediction exists, the price
    # verdict does not yet.
    assert pick_kind(classification) == MODEL_PICK


def test_vetoed_consensus_is_never_presented_as_a_bet():
    classification = recommendation_classification("CONSENSUS", "VETOED")
    assert classification == "VETOED"
    assert pick_kind(classification) == BLOCKED
    assert KIND_LABELS[BLOCKED] != KIND_LABELS[VALUE_BET]
