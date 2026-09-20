from pathlib import Path

from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.production_app import app


def test_today_separates_prediction_from_market_value(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)

    response = client.get("/today")

    assert response.status_code == 200
    # The two client concepts stay visually and verbally distinct: a model pick
    # is a statement about the player, a value bet is a statement about price.
    assert "MODEL PICK" in response.text
    assert "VALUE BET" in response.text
    assert "The model expects this player to win" in response.text
    assert "The current price does not qualify it as a bet" in response.text
    assert "the current price is better than our model thinks it should be" in response.text
    # A model pick is never dressed up as an executable bet.
    assert "Stake" not in response.text.split("MODEL PICK", 1)[1].split("</article>", 1)[0]


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
    assert ">Today<" in response.text
    assert ">Value<" in response.text
    assert ">Results<" in response.text
    assert ">Performance<" in response.text
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
