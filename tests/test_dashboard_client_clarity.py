from pathlib import Path

from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.production_app import app


def test_today_separates_prediction_from_market_value(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)

    response = client.get("/today")

    assert response.status_code == 200
    assert "Our Pick" in response.text
    assert "Bet Status" in response.text
    assert "Who the models currently think is more likely to win" in response.text
    assert "A pick can remain valid without being a bet" in response.text
    assert "Quarter-Kelly never decides who the model thinks will win" in response.text


def test_today_template_explains_agent_three_abstention():
    template_path = (
        Path(dashboard_app.__file__).resolve().parent / "templates" / "today.html"
    )
    template = template_path.read_text(encoding="utf-8")

    # The demo fixture does not guarantee an Agent 3 abstention, so assert the
    # conditional client copy exists in the template rather than forcing demo
    # data to represent a production evidence state it does not contain.
    assert "Agent 3 · Matchup" in template
    assert "This is not a vote for the other player" in template


def test_client_navigation_uses_question_oriented_labels(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)

    response = client.get("/today")

    assert response.status_code == 200
    assert ">Today<" in response.text
    assert ">Results<" in response.text
    assert ">Model Lab<" in response.text
    assert ">Run History<" in response.text


def test_model_lab_separates_prediction_quality_from_profit(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)

    response = client.get("/performance")

    assert response.status_code == 200
    assert "Model Lab" in response.text
    assert "prediction quality" in response.text
    assert "whether the system makes money" in response.text
