from pathlib import Path

from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.production_app import app


def test_today_focuses_on_model_picks_without_bet_promotion(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)
    response = client.get("/today")
    assert response.status_code == 200
    assert "Guide odds" in response.text
    assert "Result" in response.text
    assert "MODEL PICK" in response.text
    assert "Prediction details" in response.text
    # Value Bets are hidden from primary navigation, not removed from backend.
    assert "Stake" not in response.text
    assert ">Value Bets<" not in response.text
    assert ">Value<" not in response.text


def test_agent_three_abstention_is_explained_on_performance():
    """Agent-level evidence moved off Today, but the explanation must survive.

    Today answers "what are we picking"; the meaning of an Agent 3 abstention
    belongs with the model evidence, not on the morning screen.
    """
    template_path = (
        Path(dashboard_app.__file__).resolve().parent / "templates" / "performance.html"
    )
    template = template_path.read_text(encoding="utf-8")

    assert "Agent 3 · Matchup" in template
    assert "This is not a vote for the other player" in template


def test_client_navigation_uses_question_oriented_labels(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)

    response = client.get("/today")

    assert response.status_code == 200
    # Primary navigation is understandable without knowing Sports Edge internals.
    assert ">Model Picks<" in response.text
    assert ">Results<" in response.text
    assert ">Performance<" in response.text
    assert ">Value<" not in response.text
    assert ">Value Bets<" not in response.text
    # Operational views stay out of the client navigation.
    assert ">Run History<" not in response.text
    assert ">Model Lab<" not in response.text


def test_model_lab_separates_prediction_quality_from_profit(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)

    response = client.get("/performance")

    assert response.status_code == 200
    assert "Performance" in response.text
    assert "prediction quality" in response.text
    assert "whether the system makes money" in response.text


def test_today_card_shows_original_match_date_time_and_zone(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)
    utc = client.get("/today?tz=utc")
    ph = client.get("/today?tz=philippines")
    assert utc.status_code == 200
    assert ph.status_code == 200
    # The same original canonical fixture instant, localized, includes date.
    assert "07 Sep 2026 14:00 UTC" in utc.text
    assert "07 Sep 2026 22:00 PHT" in ph.text


def test_today_warning_copy_does_not_assume_every_warning_is_stale_odds():
    path = Path(dashboard_app.__file__).resolve().parent / "templates" / "today.html"
    body = path.read_text(encoding="utf-8")
    assert "Live data coverage or freshness requires attention." in body
    assert "Live data is older than expected." not in body


def test_wide_spread_a1_a2_research_stays_out_of_primary_manager_picks(monkeypatch):
    """Do not present an unqualified A1/A2 average as a customer Model Pick."""
    from dataclasses import replace
    from decimal import Decimal

    from sports_edge_tennis.dashboard.demo import build_demo_state
    from sports_edge_tennis.dashboard.read_model import AgentView

    original = build_demo_state()
    source = original.matches[0]
    tracking_only = replace(
        source,
        match_id=987654,
        player_1="TRACKING TEST PLAYER",
        player_2="TRACKING TEST OPPONENT",
        manager_status="NO_BET",
        selected_side=None,
        consensus_probability=None,
        reason_code="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
        active_agent_count=2,
        abstaining_agent_count=1,
        agents={
            "AGENT_1_SURFACE_ELO": AgentView(
                code="AGENT_1_SURFACE_ELO",
                status="PREDICTED",
                selection=1,
                probability=Decimal("0.81"),
            ),
            "AGENT_2_HIERARCHICAL_MARKOV": AgentView(
                code="AGENT_2_HIERARCHICAL_MARKOV",
                status="PREDICTED",
                selection=1,
                probability=Decimal("0.65"),
            ),
            "AGENT_3_INTRANSITIVITY": AgentView(
                code="AGENT_3_INTRANSITIVITY",
                status="ABSTAINED",
                selection=None,
                probability=None,
            ),
        },
    )
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    monkeypatch.setattr(
        dashboard_app,
        "build_demo_state",
        lambda: replace(original, matches=(*original.matches, tracking_only)),
    )
    response = TestClient(app).get("/today?tz=utc")
    assert response.status_code == 200
    page = response.text
    assert "3 Manager-qualified selections" in page
    assert 'id="tracking-only-disclosure"' in page
    assert "TRACKING TEST PLAYER" in page
    assert "research-only tracking" in page
    assert "not published Manager Model Picks" in page
    main_cards = page.split('id="today-pick-list"', 1)[1].split("</section>", 1)[0]
    assert "Carlos Alcaraz" in main_cards
    assert "TRACKING TEST PLAYER" not in main_cards
    tracking_section = page.split('id="tracking-only-disclosure"', 1)[1]
    assert "TRACKING TEST PLAYER" in tracking_section
    assert "16.00%" in tracking_section
    # Historical/JSON source populations are intentionally not mutated.
    source_api = TestClient(app).get("/api/dashboard/today?tz=utc").json()
    assert source_api["counts"]["total_model_picks"] == 4
