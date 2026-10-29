"""Brian's mobile acceptance: all original aligned Oct27 picks visible and reconciled.

Synthetic counts reproduce production's 41 = 31W + 8L + 2VOID, not real
player identities or an invented Manager/tracking split.
"""
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.demo import build_demo_state
from sports_edge_tennis.dashboard.model_pick_history import DailyModelPickSummary
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.dashboard.read_model import AgentView
from sports_edge_tennis.runner.contracts import AGENT_1, AGENT_2, AGENT_3


DAY = date(2026, 10, 27)
EXPECTED = DailyModelPickSummary(
    decision_date=DAY, picks=41, scored=39, pending=0,
    voids=2, wins=31, losses=8, hit_rate=31 / 39,
)


def _synthetic_state():
    source = build_demo_state().matches[0]
    manager = [
        replace(
            source, match_id=i, player_1=f"Original winner {i}",
            player_2=f"Original opponent {i}", manager_status="CONSENSUS",
            selected_side=1, consensus_probability=Decimal("0.71"),
            reason_code="NONE",
        )
        for i in range(1, 21)
    ]
    tracking = [
        replace(
            source, match_id=i, player_1=f"Tracking winner {i}",
            player_2=f"Tracking opponent {i}", manager_status="NO_BET",
            selected_side=None, consensus_probability=None,
            reason_code="AGENT3_ABSTENTION_SPREAD_EXCEEDED",
            agents={
                AGENT_1: AgentView(AGENT_1, "PREDICTED", 1, Decimal("0.80")),
                AGENT_2: AgentView(AGENT_2, "PREDICTED", 1, Decimal("0.62")),
                AGENT_3: AgentView(AGENT_3, "ABSTAINED", None, None),
            },
        )
        for i in range(21, 42)
    ]
    return replace(
        build_demo_state(), run_date=DAY,
        matches=tuple(manager + tracking),
    )


def _client(monkeypatch, *, summary=EXPECTED, results=None):
    monkeypatch.setattr(dashboard_app, "_load_state", lambda *args: _synthetic_state())
    monkeypatch.setattr(dashboard_app, "_load_issued_market_overlay", lambda *args: {})
    monkeypatch.setattr(dashboard_app, "_load_guide_odds", lambda *args: {})
    if results is None:
        results = {
            i: ("WON" if i <= 31 else "LOST" if i <= 39 else "VOID")
            for i in range(1, 42)
        }
    monkeypatch.setattr(dashboard_app, "_load_issued_model_pick_results", lambda *args: results)
    monkeypatch.setattr(dashboard_app, "_load_aligned_day_summary", lambda *args: summary)
    monkeypatch.setattr(dashboard_app, "_latest_activity", lambda: None)
    monkeypatch.setattr(dashboard_app, "_load_live_health", lambda: None)
    return TestClient(app)


def test_oct27_41_picks_all_visible_with_correct_results_on_phone(monkeypatch):
    client = _client(monkeypatch)
    response = client.get("/today?date=2026-10-27&tz=philippines")
    assert response.status_code == 200
    body = response.text
    assert "41 original aligned selections (20 Manager · 21 A1/A2 tracking)" in body
    assert body.count('<article class="pick-card') == 41
    assert 'id="tracking-only-disclosure"' not in body
    assert "Tracking winner 21" in body
    assert "A1/A2 aligned tracking · not a Value Bet" in body
    for text in ("31", "8", "2", "79.5%", "All 41 match cards below reconcile"):
        assert text in body
    # Every result shown on a card, not merely the headline.
    assert body.count('<span class="metric-value">Won</span>') == 31
    assert body.count('<span class="metric-value">Lost</span>') == 8
    assert body.count('<span class="metric-value">Void</span>') == 2

    payload = client.get("/api/dashboard/today?date=2026-10-27&tz=philippines").json()
    assert payload["original_aligned_day"] == {
        "picks": 41, "wins": 31, "losses": 8, "voids": 2,
        "pending": 0, "graded": 39, "win_rate": 31 / 39,
        "displayed_cards": 41, "cards_reconciled": True,
    }
    assert len(payload["picks"]) == 41
    assert payload["pick_counts"]["wide_spread_model_picks"] == 21
    assert sum(p["result_status"] == "WON" for p in payload["picks"]) == 31


def test_missing_cards_or_unresolved_results_trigger_visible_warning(monkeypatch):
    wrong_total = replace(EXPECTED, picks=42)
    page = _client(monkeypatch, summary=wrong_total).get(
        "/today?date=2026-10-27"
    ).text
    assert "full original result ledger contains 42 picks" in page
    assert "only 41 cards" in page
    assert 'role="alert"' in page

    results = {i: ("WON" if i <= 31 else "LOST" if i <= 39 else "VOID")
               for i in range(1, 42)}
    results.pop(1)
    client = _client(monkeypatch, results=results)
    payload = client.get("/api/dashboard/today?date=2026-10-27").json()
    assert payload["original_aligned_day"]["cards_reconciled"] is False


def test_scorecard_manila_day_links_to_full_issued_picks():
    from sports_edge_tennis.dashboard import production_app
    root = Path(production_app.__file__).resolve().parent
    template = (root / "templates" / "model_pick_results.html").read_text()
    assert 'href="/today?date={{ day.date }}&amp;tz=philippines"' in template
