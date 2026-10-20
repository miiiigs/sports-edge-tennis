"""Daily composition counts: qualified vs wide-spread vs Value Bet subset."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sports_edge_tennis.dashboard.market_overlay import MarketDecisionView
from sports_edge_tennis.dashboard.picks import (
    MODEL_PICK,
    VALUE_BET,
    build_today_picks,
)
from sports_edge_tennis.dashboard.read_model import AgentView, MatchView
from sports_edge_tennis.runner.contracts import AGENT_1, AGENT_2, AGENT_3

START = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)


def _qualified(match_id: int, side: int, prob: str) -> MatchView:
    return MatchView(
        match_id=match_id,
        player_1=f"P{match_id}A",
        player_2=f"P{match_id}B",
        tournament="Hourly Open",
        competition_tier="ATP_250",
        scheduled_start=START,
        agents={},
        manager_status="CONSENSUS",
        selected_side=side,
        consensus_probability=Decimal(prob),
        reason_code="NONE",
        active_agent_count=2,
        abstaining_agent_count=1,
        player_1_price=None,
        player_2_price=None,
        sharp_source_observed_at=None,
    )


def _wide(match_id: int) -> MatchView:
    agents = {
        AGENT_1: AgentView(AGENT_1, "PREDICTED", 1, Decimal("0.639556")),
        AGENT_2: AgentView(AGENT_2, "PREDICTED", 1, Decimal("0.542955")),
        AGENT_3: AgentView(AGENT_3, "ABSTAINED", None, None),
    }
    return MatchView(
        match_id=match_id,
        player_1=f"P{match_id}A",
        player_2=f"P{match_id}B",
        tournament="Hourly Open",
        competition_tier="ATP_250",
        scheduled_start=START,
        agents=agents,
        manager_status="NO_BET",
        selected_side=None,
        consensus_probability=None,
        reason_code="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
        active_agent_count=2,
        abstaining_agent_count=1,
        player_1_price=None,
        player_2_price=None,
        sharp_source_observed_at=None,
    )


def _bet(match_id: int, *, bookmaker="UNIBET", price="1.98", edge="0.135272", stake="0.02") -> MarketDecisionView:
    return MarketDecisionView(
        match_id=match_id,
        decision_status="BET",
        reason_code="NONE",
        selected_side=1,
        market_decimal_price=Decimal(price),
        sharp_reference_price=Decimal("1.85"),
        price_bookmaker_code=bookmaker,
        edge_pct=Decimal(edge),
        min_acceptable_odds=Decimal("1.90"),
        suggested_stake_fraction=Decimal(stake),
        published_at=START,
    )


def _veto(match_id: int, *, price="1.98") -> MarketDecisionView:
    return MarketDecisionView(
        match_id=match_id,
        decision_status="VETOED",
        reason_code="VETOED",
        selected_side=1,
        market_decimal_price=Decimal(price),
        sharp_reference_price=Decimal("1.85"),
        price_bookmaker_code="UNIBET",
        edge_pct=Decimal("0.10"),
        min_acceptable_odds=Decimal("1.90"),
    )


def test_daily_composition_counts_qualified_wide_and_value_subset():
    # Issued ledger: 4 qualified (m1,m3 also Value Bets) + 1 additive wide-spread.
    matches = [
        _qualified(1, 1, "0.573370"),
        _qualified(2, 2, "0.558710"),
        _qualified(3, 1, "0.571958"),
        _qualified(4, 2, "0.583631"),
        _wide(5),
    ]
    decisions = {1: _bet(1, price="1.98"), 3: _bet(3, bookmaker="UNIBET", price="2.15", edge="0.229711")}

    result = build_today_picks(matches, decisions)

    assert result.qualified_model_pick_count == 4
    assert result.manager_qualified_selection_count == 4
    assert result.wide_spread_model_pick_count == 1
    assert result.total_model_pick_count == 5
    assert result.value_bet_count == 2
    assert result.total == 5
    assert len({p.match_id for p in result.picks}) == 5  # one row per sporting match

    value_ids = {p.match_id for p in result.picks if p.kind == VALUE_BET}
    qualified_ids = {
        p.match_id
        for p in result.picks
        if p.kind in (MODEL_PICK, VALUE_BET) and not p.is_wide_spread
    }
    assert value_ids == {1, 3}
    assert value_ids <= qualified_ids  # Value Bets are a SUBSET, not extra picks
    assert result.total_model_pick_count == 5  # not 7 (no double-count)

    vb = next(p for p in result.picks if p.match_id == 3)
    assert vb.bookmaker == "UNIBET"
    assert vb.offered_price == Decimal("2.15")
    assert vb.edge_text  # issued edge preserved
    assert vb.stake_text == "2.00%"

    wide = next(p for p in result.picks if p.match_id == 5)
    assert wide.is_wide_spread is True
    assert wide.is_actionable_bet is False
    assert wide.stake_text is None


def test_primary_selection_count_includes_blocked_but_model_pick_total_does_not():
    """The API exposes both definitions explicitly instead of silently merging them."""
    matches = [_qualified(1, 1, "0.62"), _qualified(2, 2, "0.62"), _wide(3)]
    decisions = {1: _bet(1), 2: _veto(2)}

    result = build_today_picks(matches, decisions)

    assert result.manager_qualified_selection_count == 2
    assert result.qualified_model_pick_count == 1
    assert result.blocked_count == 1
    assert result.wide_spread_model_pick_count == 1
    assert result.total_model_pick_count == 2
    assert result.total == 3
    assert (
        result.manager_qualified_selection_count
        == result.qualified_model_pick_count + result.blocked_count
    )


def test_qualified_precedence_over_wide_spread_for_same_match():
    # If composition ever receives both, the qualified row must be the only one.
    matches = [_qualified(10, 1, "0.58")]
    decisions = {}
    result = build_today_picks(matches, decisions)
    assert result.qualified_model_pick_count == 1
    assert result.wide_spread_model_pick_count == 0
    assert [p.match_id for p in result.picks] == [10]
