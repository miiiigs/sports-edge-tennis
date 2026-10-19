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
