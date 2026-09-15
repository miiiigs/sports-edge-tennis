from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sports_edge_tennis.performance.paper_bankroll import (
    PaperBet,
    simulate_paper_bankroll,
)

UTC = timezone.utc


def _bet(
    decision_id: int,
    *,
    decision_at: datetime,
    status: str,
    price: str = "2.00",
    fraction: str = "0.02",
    settled_after_hours: int | None = 2,
) -> PaperBet:
    settled_at = None
    if status != "PENDING" and settled_after_hours is not None:
        settled_at = decision_at + timedelta(hours=settled_after_hours)
    return PaperBet(
        decision_id=decision_id,
        match_id=1000 + decision_id,
        decision_at=decision_at,
        settlement_status=status,
        settled_at=settled_at,
        decimal_price=Decimal(price),
        stake_fraction=Decimal(fraction),
        selection="Player A",
        player_1="Player A",
        player_2="Player B",
        tournament="Test Challenger",
        bookmaker="PINNACLE",
    )


def test_paper_bankroll_replays_win_and_loss_without_real_execution():
    start = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    bets = (
        _bet(1, decision_at=start, status="WON", price="2.00"),
        _bet(2, decision_at=start + timedelta(hours=3), status="LOST", price="2.00"),
    )

    summary = simulate_paper_bankroll(bets)

    # Bet 1 stakes 2% of $1,000 = $20 and wins $20 profit -> $1,020.
    # Bet 2 then stakes 2% of $1,020 = $20.40 and loses.
    assert summary.starting_bankroll == Decimal("1000.00")
    assert summary.current_bankroll == Decimal("999.60")
    assert summary.realized_profit == Decimal("-0.40")
    assert summary.bets == 2
    assert summary.settled == 2
    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.pending == 0


def test_overlapping_bets_keep_open_stake_in_equity_for_next_stake_size():
    start = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    bets = (
        _bet(1, decision_at=start, status="WON", price="2.00", settled_after_hours=4),
        _bet(2, decision_at=start + timedelta(hours=1), status="PENDING"),
    )

    summary = simulate_paper_bankroll(bets)

    # The first open $20 remains part of bankroll equity at cost, so the second
    # 2% stake is also $20 rather than 2% of only the remaining cash.
    open_points = [point for point in summary.points if point.event == "OPEN"]
    assert open_points[0].open_exposure == Decimal("20.00")
    assert open_points[1].open_exposure == Decimal("40.00")
    assert summary.pending == 1
    assert summary.open_exposure == Decimal("20.00")
    assert summary.current_bankroll == Decimal("1020.00")


def test_void_returns_stake_without_profit():
    start = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    summary = simulate_paper_bankroll((_bet(1, decision_at=start, status="VOID"),))

    assert summary.current_bankroll == Decimal("1000.00")
    assert summary.realized_profit == Decimal("0.00")
    assert summary.voids == 1


def test_pending_position_is_not_fake_profit_or_loss():
    start = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    summary = simulate_paper_bankroll((_bet(1, decision_at=start, status="PENDING"),))

    assert summary.current_bankroll == Decimal("1000.00")
    assert summary.cash == Decimal("980.00")
    assert summary.open_exposure == Decimal("20.00")
    assert summary.realized_profit == Decimal("0.00")
    assert summary.pending == 1


def test_rejects_non_positive_starting_bankroll():
    with pytest.raises(ValueError, match="starting_bankroll must be positive"):
        simulate_paper_bankroll((), starting_bankroll=Decimal("0"))
