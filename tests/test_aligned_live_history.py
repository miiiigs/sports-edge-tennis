from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.daily_history as daily_history
import sports_edge_tennis.dashboard.model_pick_history as model_pick_history
from sports_edge_tennis.dashboard.daily_bets import DailyBetSummary
from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary,
    ModelPickResultsView,
)
from sports_edge_tennis.dashboard.production_app import app


def _bet(day: date, *, wins: int = 0, losses: int = 0) -> DailyBetSummary:
    settled = wins + losses
    profit = Decimal(wins) - Decimal(losses)
    return DailyBetSummary(
        decision_date=day,
        bets=1,
        settled=settled,
        pending=0,
        wins=wins,
        losses=losses,
        voids=0,
        cancelled=0,
        one_unit_profit=profit,
        roi=(profit / Decimal(settled)) if settled else None,
        hit_rate=(wins / settled) if settled else None,
    )


def _model(day: date, *, wins: int = 0, losses: int = 0) -> DailyModelPickSummary:
    scored = wins + losses
    return DailyModelPickSummary(
        decision_date=day,
        picks=1,
        scored=scored,
        pending=0,
        voids=0,
        wins=wins,
        losses=losses,
        hit_rate=(wins / scored) if scored else None,
    )


def test_value_bet_api_exposes_sep10_aligned_live_history(monkeypatch):
    rows = (
        _bet(date(2026, 9, 11), wins=1),
        _bet(date(2026, 9, 10), losses=1),
        _bet(date(2026, 9, 9), wins=1),
    )
    monkeypatch.setattr(
        daily_history,
        "_load_history",
        lambda requested: daily_history.ResultsView(all_summaries=rows),
    )

    body = TestClient(app).get("/api/dashboard/value-bet-results").json()

    aligned = body["aligned_live_history"]
    assert aligned["start_date"] == "2026-09-10"
    assert aligned["is_replay"] is False
    assert aligned["performance"]["all_time"]["bets"] == 2
    assert aligned["performance"]["all_time"]["wins"] == 1
    assert aligned["performance"]["all_time"]["losses"] == 1
    assert body["all_historical_performance"]["all_time"]["bets"] == 3


def test_model_pick_api_exposes_sep10_aligned_live_history(monkeypatch):
    rows = (
        _model(date(2026, 9, 11), wins=1),
        _model(date(2026, 9, 10), losses=1),
        _model(date(2026, 9, 9), wins=1),
    )
    monkeypatch.setattr(
        model_pick_history,
        "_load_history",
        lambda requested: ModelPickResultsView(all_summaries=rows),
    )

    body = TestClient(app).get("/api/dashboard/model-pick-results").json()

    aligned = body["aligned_live_history"]
    assert aligned["start_date"] == "2026-09-10"
    assert aligned["is_replay"] is False
    assert aligned["performance"]["all_time"]["picks"] == 2
    assert aligned["performance"]["all_time"]["wins"] == 1
    assert aligned["performance"]["all_time"]["losses"] == 1
    assert body["all_historical_model_performance"]["all_time"]["picks"] == 3


def test_results_pages_label_aligned_history_as_actual_live_not_replay(monkeypatch):
    bet_rows = (_bet(date(2026, 9, 10), wins=1),)
    model_rows = (_model(date(2026, 9, 10), wins=1),)
    monkeypatch.setattr(
        daily_history,
        "_load_history",
        lambda requested: daily_history.ResultsView(all_summaries=bet_rows),
    )
    monkeypatch.setattr(
        model_pick_history,
        "_load_history",
        lambda requested: ModelPickResultsView(all_summaries=model_rows),
    )
    monkeypatch.setattr(daily_history, "_latest_activity", lambda: None)
    monkeypatch.setattr(model_pick_history, "_latest_activity", lambda: None)

    client = TestClient(app)
    bets_page = client.get("/value-bet-results").text
    picks_page = client.get("/model-pick-results").text

    assert "Aligned live history · since 10 Sep 2026" in bets_page
    assert "not a backtest or replay" in bets_page
    assert "Aligned live history · since 10 Sep 2026" in picks_page
    assert "not a replay of today&#39;s model configuration" in picks_page or "not a replay of today's model configuration" in picks_page
