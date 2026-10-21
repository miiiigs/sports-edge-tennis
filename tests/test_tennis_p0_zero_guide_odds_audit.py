"""No-network regression checks for the P0 Guide Odds root-cause classifier."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ops.tennis_p0_zero_guide_odds_audit import classify_missing, completion_result

PICK = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
START = PICK + timedelta(hours=4)


def quote(**changes):
    q = {
        "bookmaker_code": "BET365",
        "source_system": "OFFERED",
        "odds_origin": "LIVE_PROVIDER",
        "market_type": "MATCH_WINNER",
        "market_status": "OPEN",
        "selection_type": "PLAYER_1",
        "decimal_price": Decimal("1.83"),
        "source_observed_at": PICK + timedelta(hours=1),
        "as_of": PICK + timedelta(hours=1),
        "ingested_at": PICK + timedelta(hours=1),
    }
    q.update(changes)
    return q


def reason(quotes, *, side=1, start=START, same=True):
    return classify_missing(
        quotes, side=side, picked_at=PICK,
        match_start_at=start, same_orientation=same,
    )


def test_missing_record_is_not_claimed_as_missing_api_event():
    assert reason([]) == "NO_STORED_OFFERED_QUOTE_FOR_MATCH"


def test_stored_quote_after_match_start_is_not_presented_as_bet():
    assert reason([quote(ingested_at=START + timedelta(minutes=5))]) == (
        "PRICE_FIRST_AVAILABLE_AFTER_MATCH_START"
    )


def test_wrong_player_side_and_unknown_bookmaker_are_rejected():
    assert reason([quote()], side=2) == "NO_LIVE_MATCH_WINNER_QUOTE_ON_PICKED_SIDE"
    assert reason([quote(bookmaker_code="UNKNOWN")]) == "NO_SUPPORTED_BOOKMAKER_QUOTE"


def test_valid_quote_should_surface_in_candidate_reader():
    assert reason([quote()]) == "ELIGIBLE_QUOTE_BUT_DASHBOARD_READER_MISSED"


def test_pit_orientation_change_blocks_nonmatching_selected_side():
    assert reason([quote()], same=False) == "PLAYER_ORIENTATION_NOT_VERIFIED"


def test_older_than_issue_minus_twelve_hours_quote_is_rejected():
    age = PICK - timedelta(hours=13)
    assert reason([quote(source_observed_at=age, ingested_at=age, as_of=age)]) == (
        "QUOTE_OBSERVED_BEFORE_12H_PRE_ISSUE_WINDOW"
    )


def test_only_active_open_market_with_decimal_price_over_one_is_valid():
    assert reason([quote(market_status="SUSPENDED")]) == "NO_OPEN_VALID_PRICE"
    assert reason([quote(decimal_price=Decimal("1.0"))]) == "NO_OPEN_VALID_PRICE"


def test_weekly_accuracy_excludes_pending_walkover_and_conflicting_truth():
    assert completion_result([], 1) == "PENDING_OR_UNVERIFIED"
    assert completion_result([{"outcome_status": "WALKOVER", "winner_side": None}], 1) == "NOT_COMPARABLE_OR_VOID"
    assert completion_result([{"outcome_status": "COMPLETED", "winner_side": 1}], 1) == "WON"
    assert completion_result([{"outcome_status": "COMPLETED", "winner_side": 2}], 1) == "LOST"
    assert completion_result([
        {"outcome_status": "COMPLETED", "winner_side": 1},
        {"outcome_status": "COMPLETED", "winner_side": 2},
    ], 1) == "CONFLICTING_TERMINAL_OBSERVATIONS"
