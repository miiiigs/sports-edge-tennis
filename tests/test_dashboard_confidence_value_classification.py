from sports_edge_tennis.dashboard.presentation import client_state, state_label


def test_manager_non_consensus_remains_no_bet():
    assert client_state("NO_BET", True, "BET") == "NO_BET_MODEL"


def test_manager_consensus_with_value_bet_is_high_confidence_plus_value():
    state = client_state("CONSENSUS", True, "BET")
    assert state == "HIGH_CONFIDENCE_VALUE"
    assert state_label(state) == "High-Confidence + Value"


def test_manager_consensus_without_value_remains_visible_high_confidence_pick():
    state = client_state("CONSENSUS", True, "NO_BET")
    assert state == "HIGH_CONFIDENCE_PICK"
    assert state_label(state) == "High-Confidence Pick"


def test_manager_consensus_without_market_decision_is_not_erased():
    assert client_state("CONSENSUS", False, None) == "MODEL_CONSENSUS"
    assert state_label("MODEL_CONSENSUS") == "High-Confidence Pick"


def test_consensus_with_sharp_but_pending_offered_price_stays_pending():
    state = client_state("CONSENSUS", True, None)
    assert state == "AWAITING_OFFERED_PRICE"
    assert state_label(state) == "High-Confidence Pick · Price Pending"
