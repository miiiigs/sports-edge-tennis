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


def test_today_explains_agent_three_abstention(monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)

    response = client.get("/today")

    assert response.status_code == 200
    assert "Agent 3 · Matchup" in response.text
    assert "This is not a vote for the other player" in response.text


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
