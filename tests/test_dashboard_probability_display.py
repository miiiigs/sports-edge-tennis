"""Client-facing probability display must never contradict classification.

The raw model probability is a ``Decimal``. The 60% rule is applied to that raw
value. These tests pin the presentation contract between the two: whatever the
display precision, a value on one side of 0.600000 may never render on the
other side of 60.0%.

The formatter truncates at one decimal, so a sub-60% probability can never round
up to ``60.0%`` and a qualifying probability can never round down below it.
Nothing here changes the raw probability, Manager decisions, Market rules, or
the 60% threshold.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
import sports_edge_tennis.dashboard.pick_lifecycle_routes as lifecycle_routes
from sports_edge_tennis.dashboard import presentation as pres
from sports_edge_tennis.dashboard.market_overlay import MarketDecisionView
from sports_edge_tennis.dashboard.pick_lifecycle import PickLifecycleView
from sports_edge_tennis.dashboard.picks import VALUE_BET, build_today_picks
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.dashboard.read_model import AgentView, MatchView
from sports_edge_tennis.runner.contracts import AGENT_1, AGENT_2, AGENT_3

START = datetime(2026, 9, 7, 14, 30, tzinfo=UTC)

# The single product threshold this lane protects. Applied to the raw Decimal,
# never to a rendered string.
STRONG_THRESHOLD = Decimal("0.600000")

BOUNDARY_VALUES = (
    "0.5994",
    "0.5995",
    "0.5999",
    "0.6000",
    "0.6001",
    "0.6005",
    "0.6450",
)


def _rendered_meets_threshold(rendered: str) -> bool:
    return Decimal(rendered.rstrip("%")) >= Decimal("60.0")


def _bet_decision(match_id: int) -> MarketDecisionView:
    return MarketDecisionView(
        match_id=match_id,
        decision_status="BET",
        reason_code="NONE",
        selected_side=1,
        market_decimal_price=Decimal("1.80"),
        sharp_reference_price=Decimal("1.60"),
        price_bookmaker_code="BET365",
        edge_pct=Decimal("0.0350"),
        min_acceptable_odds=Decimal("1.70"),
        suggested_stake_fraction=Decimal("0.0100"),
        published_at=START,
    )


def _manager_match(probability: str) -> MatchView:
    return MatchView(
        match_id=1,
        player_1="Carlos Alcaraz",
        player_2="Jannik Sinner",
        tournament="ATP Tokyo",
        competition_tier="ATP_500",
        scheduled_start=START,
        agents={},
        manager_status="CONSENSUS",
        selected_side=1,
        consensus_probability=Decimal(probability),
        reason_code="NONE",
        active_agent_count=3,
        abstaining_agent_count=0,
        player_1_price=Decimal("1.60"),
        player_2_price=Decimal("2.35"),
        sharp_source_observed_at=START,
    )


def _wide_spread_match(a1: str, a2: str) -> MatchView:
    agents = {
        AGENT_1: AgentView(AGENT_1, "PREDICTED", 1, Decimal(a1)),
        AGENT_2: AgentView(AGENT_2, "PREDICTED", 1, Decimal(a2)),
        AGENT_3: AgentView(AGENT_3, "ABSTAINED", None, None),
    }
    return MatchView(
        match_id=2,
        player_1="Carlos Alcaraz",
        player_2="Jannik Sinner",
        tournament="ATP Tokyo",
        competition_tier="ATP_500",
        scheduled_start=START,
        agents=agents,
        manager_status="NO_BET",
        selected_side=None,
        consensus_probability=None,
        reason_code="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
        active_agent_count=2,
        abstaining_agent_count=1,
        player_1_price=Decimal("1.60"),
        player_2_price=Decimal("2.35"),
        sharp_source_observed_at=START,
    )


# --------------------------------------------------------------- formatter rule


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.596", "59.6%"),
        ("0.5994", "59.9%"),
        ("0.5995", "59.9%"),
        ("0.5999", "59.9%"),
        ("0.6000", "60.0%"),
        ("0.6001", "60.0%"),
        ("0.6005", "60.0%"),
        ("0.603", "60.3%"),
        ("0.6450", "64.5%"),
        ("0.683", "68.3%"),
    ],
)
def test_percent_uses_one_decimal(raw: str, expected: str) -> None:
    assert pres.percent(Decimal(raw)) == expected


@pytest.mark.parametrize("raw", BOUNDARY_VALUES)
def test_display_never_contradicts_the_60_percent_threshold(raw: str) -> None:
    value = Decimal(raw)
    rendered = pres.percent(value)

    # The raw Decimal is untouched by presentation.
    assert value == Decimal(raw)
    # Display and classification agree on which side of the threshold we are on.
    assert _rendered_meets_threshold(rendered) == (value >= STRONG_THRESHOLD)


# ------------------------------------------------------- Manager consensus path


@pytest.mark.parametrize("raw", BOUNDARY_VALUES)
def test_manager_probability_display_matches_raw_classification(raw: str) -> None:
    (pick,) = build_today_picks([_manager_match(raw)], {}).picks

    assert pick.win_probability == Decimal(raw)
    assert pick.probability_basis_label == "Manager Consensus"
    assert pick.probability_text == pres.percent(Decimal(raw))
    assert _rendered_meets_threshold(pick.probability_text) == (
        Decimal(raw) >= STRONG_THRESHOLD
    )
    assert pick.is_weak == (Decimal(raw) < STRONG_THRESHOLD)


def test_manager_sub_60_weak_and_exact_60_strong() -> None:
    weak = build_today_picks([_manager_match("0.5999")], {}).picks[0]
    strong = build_today_picks([_manager_match("0.6000")], {}).picks[0]

    assert weak.probability_text == "59.9%"
    assert weak.is_weak is True
    assert strong.probability_text == "60.0%"
    assert strong.is_weak is False


# -------------------------------------------------- A1/A2 wide-spread tracking


@pytest.mark.parametrize(
    ("a1", "a2", "expected"),
    [
        ("0.5994", "0.5994", "59.9%"),
        ("0.5999", "0.5999", "59.9%"),
        ("0.6000", "0.6000", "60.0%"),
        ("0.6001", "0.6001", "60.0%"),
        ("0.6450", "0.6450", "64.5%"),
        ("0.596", "0.603", "59.9%"),
        ("0.596", "0.604", "60.0%"),
    ],
)
def test_wide_spread_tracking_average_precision(
    a1: str, a2: str, expected: str
) -> None:
    (pick,) = build_today_picks([_wide_spread_match(a1, a2)], {}).picks

    assert pick.is_wide_spread is True
    assert pick.probability_basis_label == "A1/A2 Tracking Average"
    assert pick.primary_average_probability_text == expected


@pytest.mark.parametrize("raw", BOUNDARY_VALUES)
def test_wide_spread_average_display_matches_raw_classification(raw: str) -> None:
    (pick,) = build_today_picks([_wide_spread_match(raw, raw)], {}).picks

    average = (Decimal(raw) + Decimal(raw)) / Decimal(2)
    assert pick.primary_average_probability == average
    assert pick.primary_average_probability_text == pres.percent(average)
    assert _rendered_meets_threshold(pick.primary_average_probability_text) == (
        average >= STRONG_THRESHOLD
    )
    assert pick.is_weak == (average < STRONG_THRESHOLD)


def test_wide_spread_average_is_not_conflated_with_manager_probability() -> None:
    (pick,) = build_today_picks([_wide_spread_match("0.64", "0.60")], {}).picks

    # No Manager consensus exists for this row, and it is never a bet.
    assert pick.win_probability is None
    assert pick.probability_text == "—"
    assert pick.is_actionable_bet is False
    assert pick.primary_average_probability_text == "62.0%"
    assert pick.probability_basis_label == "A1/A2 Tracking Average"

    payload = dashboard_app._pick_payload(pick)
    assert payload["win_probability"] == "—"
    assert payload["tracking_probability"] == "62.0%"
    assert payload["probability_basis"] == "A1_A2_TRACKING_AVERAGE"


def test_wide_spread_sub_60_average_stays_tracking_only() -> None:
    (pick,) = build_today_picks([_wide_spread_match("0.5994", "0.5999")], {}).picks

    assert pick.is_wide_spread is True
    assert pick.is_actionable_bet is False
    assert pick.stake_text is None
    assert pick.primary_average_probability_text == "59.9%"


# ------------------------------------------------------- Value Bet logic intact


def test_value_bet_logic_untouched_by_display_precision() -> None:
    matches = [_manager_match("0.599999")]
    decisions = {1: _bet_decision(1)}

    (pick,) = build_today_picks(matches, decisions).picks

    # Market v2 still qualifies the bet; the weak flag is presentation-only.
    assert pick.kind == VALUE_BET
    assert pick.is_actionable_bet is True
    assert pick.is_weak is True
    assert pick.probability_text == "59.9%"


# ----------------------------------------------------------- API/UI consistency


@pytest.fixture()
def demo_client(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    return TestClient(app)


def test_api_and_html_share_one_probability_string(demo_client) -> None:
    body = demo_client.get("/api/dashboard/today").json()
    html = demo_client.get("/today").text

    alcaraz = next(p for p in body["picks"] if p["player"] == "Carlos Alcaraz")
    assert alcaraz["win_probability"] == "70.0%"
    assert alcaraz["probability_basis"] == "MANAGER_CONSENSUS"
    # The same rendered value reaches the server-rendered card, not a second
    # client-side rounding of the raw probability.
    assert alcaraz["win_probability"] in html
    assert "Probability basis: Manager Consensus" in html


# ------------------------------------------------ lifecycle API stays type-safe


def _lifecycle_view(**overrides) -> PickLifecycleView:
    values = {
        "match_id": 130361,
        "player_1": "B. Shick",
        "player_2": "E. Arutiunian",
        "tournament": "Tiburon",
        "competition_tier": "CHALLENGER",
        "scheduled_start": datetime(2026, 9, 15, 18, 30, tzinfo=UTC),
        "first_pick_at": datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
        "first_selected_side": 2,
        "first_probability": Decimal("0.5999"),
        "latest_model_at": datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        "latest_manager_status": "CONSENSUS",
        "latest_selected_side": 2,
        "latest_probability": Decimal("0.6001"),
        "latest_active_agent_count": 2,
        "latest_abstaining_agent_count": 1,
        "latest_market_status": "NO_BET",
        "latest_market_reason": "EDGE_BELOW_THRESHOLD",
        "latest_market_price": Decimal("1.45"),
        "latest_bookmaker": "PINNACLE",
        "latest_edge_pct": Decimal("0.01"),
    }
    values.update(overrides)
    return PickLifecycleView(**values)


class _FakeConn:
    def close(self) -> None:
        pass


def _lifecycle_payload(monkeypatch, view: PickLifecycleView) -> dict:
    monkeypatch.setattr(lifecycle_routes, "DEMO_MODE", False)
    monkeypatch.setattr(lifecycle_routes, "_connect", lambda: _FakeConn())
    monkeypatch.setattr(
        lifecycle_routes,
        "load_pick_lifecycle",
        lambda conn, *, slate_date: (view,),
    )
    response = lifecycle_routes.api_pick_lifecycle(date="2026-09-15")
    return json.loads(response.body)


def test_lifecycle_raw_probability_fields_stay_raw_and_numeric(monkeypatch) -> None:
    payload = _lifecycle_payload(monkeypatch, _lifecycle_view())
    row = payload["picks"][0]

    # Raw fields keep the original machine-friendly Decimal string.
    assert row["first_probability"] == "0.5999"
    assert row["latest_probability"] == "0.6001"
    # Old consumers can still parse them numerically.
    assert Decimal(row["first_probability"]) == Decimal("0.5999")
    assert Decimal(row["latest_probability"]) == Decimal("0.6001")
    # Numeric consumers can compare against the raw 60% threshold.
    assert Decimal(row["first_probability"]) < STRONG_THRESHOLD
    assert Decimal(row["latest_probability"]) >= STRONG_THRESHOLD


def test_lifecycle_display_fields_are_presentation_only(monkeypatch) -> None:
    payload = _lifecycle_payload(monkeypatch, _lifecycle_view())
    row = payload["picks"][0]

    assert row["first_probability_text"] == "59.9%"
    assert row["latest_probability_text"] == "60.0%"
    # Display strings agree with the shared formatter and never contradict the
    # raw classification.
    assert row["first_probability_text"] == pres.percent(Decimal(row["first_probability"]))
    assert _rendered_meets_threshold(row["first_probability_text"]) is False
    assert _rendered_meets_threshold(row["latest_probability_text"]) is True


def test_lifecycle_display_fields_handle_missing_probability(monkeypatch) -> None:
    payload = _lifecycle_payload(
        monkeypatch,
        _lifecycle_view(
            latest_manager_status="NO_BET",
            latest_selected_side=None,
            latest_probability=None,
        ),
    )
    row = payload["picks"][0]

    assert row["latest_probability"] is None
    assert row["latest_probability_text"] == "—"
