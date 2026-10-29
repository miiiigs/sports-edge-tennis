"""Today must show picks, and only picks.

The client asked for one thing first: what are we picking today, and at what
probability. These tests pin the filtering rule and the Model Pick / Value Bet
distinction so a future layout change cannot quietly reintroduce a fixture
browser, or blur a model prediction into an executable bet.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.market_overlay import MarketDecisionView
from sports_edge_tennis.dashboard.picks import (
    BLOCKED,
    MODEL_PICK,
    VALUE_BET,
    build_today_picks,
    build_value_opportunities,
    pick_kind,
)
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.dashboard.read_model import AgentView, MatchView
from sports_edge_tennis.runner.contracts import AGENT_1, AGENT_2, AGENT_3


START = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)


def _match(
    match_id: int,
    *,
    manager_status: str,
    selected_side: int | None = 1,
    probability: str | None = "0.68",
    player_1: str = "Carlos Alcaraz",
    player_2: str = "Jannik Sinner",
    reason_code: str | None = None,
    agents: dict[str, AgentView] | None = None,
    abstaining_agent_count: int = 0,
) -> MatchView:
    return MatchView(
        match_id=match_id,
        player_1=player_1,
        player_2=player_2,
        tournament="ATP Tokyo",
        competition_tier="ATP_500",
        scheduled_start=START,
        agents=agents or {},
        manager_status=manager_status,
        selected_side=selected_side,
        consensus_probability=Decimal(probability) if probability else None,
        reason_code=reason_code
        or ("NONE" if manager_status == "CONSENSUS" else "DIRECTIONAL_DISAGREEMENT"),
        active_agent_count=3 - abstaining_agent_count,
        abstaining_agent_count=abstaining_agent_count,
        player_1_price=Decimal("1.60"),
        player_2_price=Decimal("2.35"),
        sharp_source_observed_at=START,
    )


def _decision(
    match_id: int,
    *,
    decision_status: str,
    stake: str | None = None,
    reason_code: str = "NONE",
) -> MarketDecisionView:
    return MarketDecisionView(
        match_id=match_id,
        decision_status=decision_status,
        reason_code=reason_code,
        selected_side=1,
        market_decimal_price=Decimal("1.80"),
        sharp_reference_price=Decimal("1.60"),
        price_bookmaker_code="BET365",
        edge_pct=Decimal("0.0350"),
        min_acceptable_odds=Decimal("1.70"),
        suggested_stake_fraction=Decimal(stake) if stake else None,
        published_at=START if decision_status == "BET" else None,
    )


# --------------------------------------------------------------- filter rule


def test_only_manager_consensus_records_become_picks() -> None:
    matches = [
        _match(1, manager_status="CONSENSUS"),
        _match(2, manager_status="NO_BET", selected_side=None, probability=None),
        _match(3, manager_status="NO_BET", selected_side=None, probability=None),
        _match(4, manager_status=None, selected_side=None, probability=None),
    ]

    result = build_today_picks(matches, {})

    assert result.evaluated_count == 4
    assert result.total == 1
    assert [p.match_id for p in result.picks] == [1]


def test_large_slate_collapses_to_the_few_actual_picks() -> None:
    """100 evaluated targets, 3 picks: Brian must see 3, not 97 rejections."""
    matches = [
        _match(i, manager_status="NO_BET", selected_side=None, probability=None)
        for i in range(1, 98)
    ]
    matches += [_match(i, manager_status="CONSENSUS") for i in (98, 99, 100)]

    result = build_today_picks(matches, {})

    assert result.evaluated_count == 100
    assert result.total == 3
    assert result.count_text == "3 picks today"


def test_no_bet_events_never_appear_as_picks() -> None:
    assert pick_kind("NO_BET") is None
    matches = [_match(1, manager_status="NO_BET", selected_side=None, probability=None)]
    assert build_today_picks(matches, {}).is_empty


def test_consensus_without_a_resolvable_side_is_not_rendered() -> None:
    matches = [_match(1, manager_status="CONSENSUS", selected_side=None)]
    assert build_today_picks(matches, {}).is_empty


def test_primary_models_same_side_wide_spread_is_visible_model_pick() -> None:
    agents = {
        AGENT_1: AgentView(
            code=AGENT_1,
            status="PREDICTED",
            selection=1,
            probability=Decimal("0.639556"),
        ),
        AGENT_2: AgentView(
            code=AGENT_2,
            status="PREDICTED",
            selection=1,
            probability=Decimal("0.542955"),
        ),
        AGENT_3: AgentView(
            code=AGENT_3,
            status="ABSTAINED",
            selection=None,
            probability=None,
        ),
    }
    match = _match(
        1,
        manager_status="NO_BET",
        selected_side=None,
        probability=None,
        reason_code="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
        agents=agents,
        abstaining_agent_count=1,
    )

    (pick,) = build_today_picks([match], {}).picks

    assert pick.kind == MODEL_PICK
    assert pick.classification == "WIDE_CONFIDENCE_SPREAD"
    assert pick.player == "Carlos Alcaraz"
    assert pick.is_wide_spread is True
    assert pick.kind_label == "MODEL PICK · WIDE SPREAD"
    assert pick.primary_a1_probability_text == "63.9%"
    assert pick.primary_a2_probability_text == "54.2%"
    assert pick.primary_average_probability_text == "59.1%"
    assert pick.probability_basis_label == "A1/A2 Tracking Average"
    assert pick.primary_probability_spread_text == "9.66%"
    assert pick.is_weak is True
    assert pick.is_actionable_bet is False
    assert pick.stake_text is None


# ------------------------------------------------- model pick vs value bet


def test_market_qualified_bet_is_a_value_bet() -> None:
    matches = [_match(1, manager_status="CONSENSUS")]
    decisions = {1: _decision(1, decision_status="BET", stake="0.0142")}

    (pick,) = build_today_picks(matches, decisions).picks

    assert pick.kind == VALUE_BET
    assert pick.kind_label == "VALUE BET"
    assert pick.is_actionable_bet is True
    assert pick.stake_text == "1.42%"


def test_consensus_without_market_value_is_a_model_pick() -> None:
    matches = [_match(1, manager_status="CONSENSUS")]
    decisions = {
        1: _decision(1, decision_status="NO_BET", reason_code="EDGE_BELOW_THRESHOLD")
    }

    (pick,) = build_today_picks(matches, decisions).picks

    assert pick.kind == MODEL_PICK
    assert pick.kind_label == "MODEL PICK"
    assert pick.is_actionable_bet is False


def test_consensus_awaiting_market_evaluation_is_a_model_pick() -> None:
    matches = [_match(1, manager_status="CONSENSUS")]

    (pick,) = build_today_picks(matches, {}).picks

    assert pick.kind == MODEL_PICK
    assert pick.classification == "PRICE_PENDING"


def test_weak_flag_is_display_only_and_uses_manager_probability_band() -> None:
    weak_value = build_today_picks(
        [_match(1, manager_status="CONSENSUS", probability="0.599999")],
        {1: _decision(1, decision_status="BET", stake="0.0100")},
    ).picks[0]
    strong_model = build_today_picks(
        [_match(2, manager_status="CONSENSUS", probability="0.600000")],
        {},
    ).picks[0]

    assert weak_value.kind == VALUE_BET
    assert weak_value.is_actionable_bet is True
    assert weak_value.is_weak is True
    assert weak_value.weak_reason == "Manager win probability is below 60%."

    assert strong_model.kind == MODEL_PICK
    assert strong_model.is_weak is False
    assert strong_model.weak_reason is None


def test_wide_spread_tracking_pick_uses_a1_a2_average_for_weak_filter() -> None:
    weak_agents = {
        AGENT_1: AgentView(AGENT_1, "PREDICTED", 1, Decimal("0.64")),
        AGENT_2: AgentView(AGENT_2, "PREDICTED", 1, Decimal("0.54")),
        AGENT_3: AgentView(AGENT_3, "ABSTAINED", None, None),
    }
    strong_agents = {
        AGENT_1: AgentView(AGENT_1, "PREDICTED", 1, Decimal("0.68")),
        AGENT_2: AgentView(AGENT_2, "PREDICTED", 1, Decimal("0.61")),
        AGENT_3: AgentView(AGENT_3, "ABSTAINED", None, None),
    }

    weak = build_today_picks(
        [
            _match(
                5,
                manager_status="NO_BET",
                selected_side=None,
                probability=None,
                reason_code="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
                agents=weak_agents,
                abstaining_agent_count=1,
            )
        ],
        {},
    ).picks[0]
    strong = build_today_picks(
        [
            _match(
                6,
                manager_status="NO_BET",
                selected_side=None,
                probability=None,
                reason_code="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
                agents=strong_agents,
                abstaining_agent_count=1,
            )
        ],
        {},
    ).picks[0]

    assert weak.primary_average_probability == Decimal("0.59")
    assert weak.is_weak is True
    assert weak.weak_reason == "A1/A2 tracking probability is below 60%."

    assert strong.primary_average_probability == Decimal("0.645")
    assert strong.is_weak is False
    assert strong.weak_reason is None
    assert strong.kind == MODEL_PICK
    assert strong.is_actionable_bet is False

    payload = dashboard_app._pick_payload(strong)
    assert payload["tracking_probability"] == "64.5%"
    assert payload["probability_basis"] == "A1_A2_TRACKING_AVERAGE"


def test_model_pick_and_value_bet_are_distinguishable_without_colour() -> None:
    """The two states must read differently in text alone."""
    assert MODEL_PICK != VALUE_BET
    matches = [_match(1, manager_status="CONSENSUS"), _match(2, manager_status="CONSENSUS")]
    decisions = {1: _decision(1, decision_status="BET", stake="0.0142")}

    picks = {p.kind: p for p in build_today_picks(matches, decisions).picks}

    assert picks[VALUE_BET].kind_label != picks[MODEL_PICK].kind_label
    assert picks[VALUE_BET].kind_summary != picks[MODEL_PICK].kind_summary
    assert picks[VALUE_BET].kind_summary
    assert picks[MODEL_PICK].kind_summary


# ---------------------------------------------------------------- veto/stake


def test_vetoed_pick_is_never_an_executable_bet() -> None:
    matches = [_match(1, manager_status="CONSENSUS")]
    decisions = {
        1: _decision(1, decision_status="VETOED", reason_code="VETO_TRIGGERED")
    }

    (pick,) = build_today_picks(matches, decisions).picks

    assert pick.kind == BLOCKED
    assert pick.is_actionable_bet is False
    assert pick.stake_text is None
    assert pick.kind_label != "VALUE BET"


def test_model_pick_never_shows_a_stake() -> None:
    matches = [_match(1, manager_status="CONSENSUS")]
    decisions = {1: _decision(1, decision_status="NO_BET", stake="0.0142")}

    (pick,) = build_today_picks(matches, decisions).picks

    # Even if a stake value were present on a non-bet row, it must not surface.
    assert pick.stake_text is None


def test_zero_or_missing_stake_is_never_shown_as_a_placeholder() -> None:
    matches = [_match(1, manager_status="CONSENSUS")]

    zero = build_today_picks(
        matches, {1: _decision(1, decision_status="BET", stake="0")}
    ).picks[0]
    missing = build_today_picks(
        matches, {1: _decision(1, decision_status="BET", stake=None)}
    ).picks[0]

    assert zero.stake_text is None
    assert missing.stake_text is None


# ------------------------------------------------------------------ ordering


def test_model_picks_default_sort_is_match_time_not_hidden_value_class() -> None:
    # The non-bet pick starts first, then the priced bet, then the blocked
    # pick. No hidden BET-first sort is allowed in Brian's unified Pick list.
    matches = [
        replace(
            _match(1, manager_status="CONSENSUS"),
            scheduled_start=START + timedelta(hours=2),
        ),
        replace(
            _match(2, manager_status="CONSENSUS"),
            scheduled_start=START,
        ),
        replace(
            _match(3, manager_status="CONSENSUS"),
            scheduled_start=START + timedelta(hours=1),
        ),
    ]
    decisions = {
        1: _decision(1, decision_status="VETOED", reason_code="VETO_TRIGGERED"),
        3: _decision(3, decision_status="BET", stake="0.0142"),
    }
    kinds = [p.kind for p in build_today_picks(matches, decisions).picks]
    assert kinds == [MODEL_PICK, VALUE_BET, BLOCKED]


# ---------------------------------------------------------------- value page


def test_value_opportunities_contain_only_qualified_bets() -> None:
    matches = [
        _match(1, manager_status="CONSENSUS"),
        _match(2, manager_status="CONSENSUS"),
        _match(3, manager_status="NO_BET", selected_side=None, probability=None),
    ]
    decisions = {2: _decision(2, decision_status="BET", stake="0.0142")}

    opportunities = build_value_opportunities(matches, decisions)

    assert [o.match_id for o in opportunities] == [2]
    assert all(o.kind == VALUE_BET for o in opportunities)


def test_wide_spread_model_pick_never_enters_value_opportunities() -> None:
    agents = {
        AGENT_1: AgentView(AGENT_1, "PREDICTED", 1, Decimal("0.64")),
        AGENT_2: AgentView(AGENT_2, "PREDICTED", 1, Decimal("0.54")),
        AGENT_3: AgentView(AGENT_3, "ABSTAINED", None, None),
    }
    wide = _match(
        4,
        manager_status="NO_BET",
        selected_side=None,
        probability=None,
        reason_code="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
        agents=agents,
        abstaining_agent_count=1,
    )

    assert build_today_picks([wide], {}).total == 1
    assert build_value_opportunities([wide], {}) == ()


# ----------------------------------------------------------------- rendering


@pytest.fixture()
def demo_client(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    return TestClient(app)


def test_today_page_renders_only_picks(demo_client) -> None:
    response = demo_client.get("/today")

    assert response.status_code == 200
    body = response.text
    assert "Model Picks" in body
    # Demo slate: 5 evaluated, 3 consensus picks.
    assert "3 original aligned selections (3 Manager · 0 A1/A2 tracking)" in body
    assert "Carlos Alcaraz" in body
    # The main pick list still contains only the three actual picks.
    assert body.count('<article class="pick-card') == 3
    # NO_BET matches may be inspected only inside the collapsed disclosure.
    assert "Show no-bet matches" in body
    assert "Andrey Rublev" in body
    assert "Stefanos Tsitsipas" in body



def test_no_bet_matches_are_collapsed_by_default_and_explain_the_reason(demo_client) -> None:
    body = demo_client.get("/today").text

    assert '<details class="no-bet-disclosure section">' in body
    assert "<details class=\"no-bet-disclosure section\" open" not in body
    assert "2 evaluated matches did not become a pick" in body
    assert "NO BET" in body
    assert "Models disagreed on the winner." in body


def test_today_page_shows_probability_prominently(demo_client) -> None:
    response = demo_client.get("/today")

    assert "Model Probability · Uncalibrated" in response.text
    assert "70%" in response.text


def test_today_page_has_weak_pick_toggle(demo_client) -> None:
    body = demo_client.get("/today").text

    assert "Filter lower-confidence picks:" in body
    assert 'id="weak-filter-state">Disabled<' in body
    assert "below 60% Manager win probability" in body
    assert "below 60% A1/A2 tracking average" not in body
    assert 'data-weak="' in body


def test_today_page_has_no_diagnostic_clutter(demo_client) -> None:
    body = demo_client.get("/today").text

    for noise in (
        "Matches Analyzed",
        "No Model Pick",
        "Pinnacle Matched",
        "Upcoming matches",
        "agent-table",
        "NO BET · MODEL FILTER",
    ):
        assert noise not in body


def test_value_page_carries_the_market_detail(demo_client) -> None:
    response = demo_client.get("/value")

    assert response.status_code == 200
    body = response.text
    assert "Value Opportunities" in body
    assert "Offered odds" in body
    assert "Edge" in body
    assert "Carlos Alcaraz" in body


def test_empty_slate_renders_a_polished_empty_state(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", False)
    monkeypatch.setattr(
        dashboard_app,
        "_load_state",
        lambda requested_date, tz=None: dashboard_app.RunState(
            kind="EMPTY_SLATE",
            run_date=requested_date,
            as_of=None,
            prediction_run_id=None,
            run_status="COMPLETED",
        ),
    )
    monkeypatch.setattr(dashboard_app, "_latest_activity", lambda: None)
    monkeypatch.setattr(dashboard_app, "_load_live_health", lambda: None)

    response = TestClient(app).get("/today")

    assert response.status_code == 200
    assert "No original aligned Model Picks for this date" in response.text
    assert "nothing currently meets the required criteria" in response.text
    # An empty result must never be padded with non-picks.
    assert "pick-card" not in response.text


def test_today_api_exposes_picks_without_internal_identifiers(demo_client) -> None:
    body = demo_client.get("/api/dashboard/today").json()

    counts = body["pick_counts"]
    assert counts["evaluated"] == 5
    assert counts["total"] == 3
    assert counts["value_bets"] == 1
    assert counts["model_picks"] == 1
    assert counts["blocked"] == 1
    assert counts["qualified_model_picks"] == 2
    assert counts["manager_qualified_selections"] == 3
    assert counts["manager_qualified_selections"] == (
        counts["model_picks"] + counts["value_bets"] + counts["blocked"]
    )
    assert counts["manager_qualified_selections"] == (
        counts["qualified_model_picks"] + counts["blocked"]
    )
    assert counts["total_model_picks"] == (
        counts["qualified_model_picks"] + counts["wide_spread_model_picks"]
    )
    for pick in body["picks"]:
        assert "match_id" not in pick
        assert pick["kind"] in {VALUE_BET, MODEL_PICK, BLOCKED}
        assert "is_weak" in pick
        assert "weak_reason" in pick
        if not pick["is_actionable_bet"]:
            assert pick["stake"] is None


def test_value_api_separates_bets_from_picks_and_blocked(demo_client) -> None:
    body = demo_client.get("/api/dashboard/value").json()

    assert len(body["value_bets"]) == 1
    assert len(body["blocked"]) == 1
    assert len(body["model_picks_without_value"]) == 1
    assert body["value_bets"][0]["is_actionable_bet"] is True
    assert body["blocked"][0]["is_actionable_bet"] is False
    assert body["blocked"][0]["stake"] is None
