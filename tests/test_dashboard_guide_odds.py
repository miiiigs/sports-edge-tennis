"""Brian's read-only Guide Odds contract for mobile Tennis Model Picks."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.guide_odds import (
    GuideOddsQuote,
    get_issued_guide_odds,
)
from sports_edge_tennis.dashboard.market_overlay import MarketDecisionView
from sports_edge_tennis.dashboard.picks import build_today_picks
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.dashboard.read_model import MatchView

NOW = datetime(2026, 10, 19, 7, 10, tzinfo=timezone.utc)


def _match(*, match_id=11, selected_side=1):
    return MatchView(
        match_id=match_id,
        player_1="Player A",
        player_2="Player B",
        tournament="ATP Example",
        competition_tier="ATP_250",
        scheduled_start=NOW,
        agents={},
        manager_status="CONSENSUS",
        selected_side=selected_side,
        consensus_probability=Decimal("0.80"),
        reason_code="NONE",
        active_agent_count=2,
        abstaining_agent_count=1,
        player_1_price=None,
        player_2_price=None,
        sharp_source_observed_at=None,
    )


def _quote(match_id=11, side=1, price="1.50"):
    return GuideOddsQuote(
        match_id=match_id,
        selected_side=side,
        decimal_price=Decimal(price),
        bookmaker_code="BET365",
        observed_at=NOW,
        odds_snapshot_id=912,
        issued_before_quote=False,
    )


def _bet(match_id=11, side=1):
    return MarketDecisionView(
        match_id=match_id,
        decision_status="BET",
        reason_code="NONE",
        selected_side=side,
        market_decimal_price=Decimal("1.65"),
        sharp_reference_price=Decimal("1.55"),
        price_bookmaker_code="UNIBET",
        edge_pct=Decimal("0.05"),
        min_acceptable_odds=Decimal("1.59"),
    )


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return self

    def fetchall(self):
        return self.rows


def test_guide_lookup_uses_same_issued_picks_and_refuses_mismatched_market_evidence():
    conn = _Connection([
        (11, 1, Decimal("1.50"), "BET365", NOW, 912, False),
        (12, 2, Decimal("2.75"), "UNIBET", NOW, 913, True),
        (13, 1, None, None, None, None, None),
        (14, 1, Decimal("0.90"), "BET365", NOW, 915, False),
    ])
    result = get_issued_guide_odds(
        conn, decision_date=date(2026, 10, 19), timezone_name="Asia/Manila"
    )

    assert list(result) == [11, 12]
    assert result[11].decimal_price == Decimal("1.50")
    assert result[12].issued_before_quote is True
    assert conn.params == ("Asia/Manila", "Asia/Manila", date(2026, 10, 19))
    sql = conn.sql
    for invariant in (
        "aligned_model_picks AS",
        "o.match_id = p.match_id",
        "o.source_system = 'OFFERED'",
        "o.odds_origin = 'LIVE_PROVIDER'",
        "o.market_type = 'MATCH_WINNER'",
        "o.market_status = 'OPEN'",
        "o.selection_type::text",
        "mv.player_1_id = latest_mv.player_1_id",
        "mv.player_2_id = latest_mv.player_2_id",
        "o.source_observed_at <= mv.match_start_at",
        "p.picked_at < mv.match_start_at",
    ):
        assert invariant in sql
    assert "o.source_observed_at <= p.picked_at + INTERVAL '24 hours'" in sql
    assert "o.as_of <= CURRENT_TIMESTAMP" in sql


def test_model_pick_receives_observed_guide_odds_without_becoming_value_bet():
    (pick,) = build_today_picks(
        [_match()], {}, {11: _quote()}, {11: "WON"}
    ).picks

    assert pick.kind == "MODEL_PICK"
    assert not pick.is_actionable_bet
    assert pick.guide_odds_display_text == "1.50"
    assert pick.guide_bookmaker == "BET365"
    assert pick.guide_odds_source == "OFFERED_LIVE_PROVIDER"
    assert pick.result_display_text == "Won"
    assert pick.stake_text is None


@pytest.mark.parametrize(
    "wrong_quote",
    [_quote(match_id=999), _quote(side=2)],
)
def test_wrong_match_or_other_player_quote_is_unavailable(wrong_quote):
    (pick,) = build_today_picks([_match()], {}, {11: wrong_quote}).picks
    assert pick.guide_odds_price is None
    assert pick.guide_odds_display_text == "Unavailable"


def test_winner_side_two_uses_correct_price_and_provenance():
    (pick,) = build_today_picks([_match(selected_side=2)], {}, {11: _quote(side=2)}).picks
    assert pick.player == "Player B"
    assert pick.guide_odds_display_text == "1.50"


def test_issued_bet_price_fallback_requires_matching_selected_side():
    (matching,) = build_today_picks([_match()], {11: _bet()}).picks
    (mismatch,) = build_today_picks([_match()], {11: _bet(side=2)}).picks
    assert matching.guide_odds_display_text == "1.65"
    assert matching.guide_odds_source == "ISSUED_BET"
    assert mismatch.guide_odds_price is None
    assert mismatch.guide_odds_display_text == "Unavailable"


def test_no_quote_never_converts_80_percent_prediction_to_implied_price():
    (pick,) = build_today_picks([_match()], {}, {}).picks
    assert pick.probability_display_text == "80%"
    assert pick.guide_odds_display_text == "Unavailable"
    assert pick.guide_odds_price is None


def test_current_client_hides_value_nav_but_preserves_read_only_old_routes(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)
    body = client.get("/today").text
    assert 'href="/value"' not in body
    assert 'href="/value-bet-results"' not in body
    assert 'href="/today"' in body
    assert "Guide odds" in body
    assert "Unavailable" in body
    assert "Result" in body
    assert "1.72" in body  # Original price for demo issued bet.
    assert client.get("/value").status_code == 200
    assert client.get("/value-bet-results").status_code == 200

    api = client.get("/api/dashboard/today").json()
    assert all("guide_odds" in pick for pick in api["picks"])
    assert all("result_status" in pick for pick in api["picks"])
    assert all("match_id" not in pick for pick in api["picks"])
