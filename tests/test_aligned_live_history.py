from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.daily_history as daily_history
import sports_edge_tennis.dashboard.model_pick_history as model_pick_history
from sports_edge_tennis.dashboard.daily_bets import (
    DailyBetSummary,
    load_aligned_daily_bet_summaries,
)
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
        lambda requested: daily_history.ResultsView(
            all_summaries=rows,
            aligned_summaries=rows[:2],
        ),
    )

    body = TestClient(app).get("/api/dashboard/value-bet-results").json()

    aligned = body["aligned_live_history"]
    assert aligned["start_date"] == "2026-09-10"
    assert aligned["is_replay"] is False
    assert aligned["scope"] == "SEP10_LIVE_VALUE_BETS_ALIGNED_TO_CURRENT_60_PERCENT_FLOOR"
    assert aligned["min_model_probability"] == "0.600000"
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
        lambda requested: ModelPickResultsView(
            all_summaries=rows,
            aligned_summaries=rows[:2],
        ),
    )

    body = TestClient(app).get("/api/dashboard/model-pick-results").json()

    aligned = body["aligned_live_history"]
    assert aligned["start_date"] == "2026-09-10"
    assert aligned["is_replay"] is False
    assert aligned["scope"] == "SEP10_LIVE_MODEL_PICKS_ALIGNED_TO_CURRENT_60_PERCENT_RULE"
    assert aligned["min_model_probability"] == "0.600000"
    assert aligned["includes_a1_a2_wide_spread_tracking"] is True
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
        lambda requested: daily_history.ResultsView(
            all_summaries=bet_rows,
            aligned_summaries=bet_rows,
        ),
    )
    monkeypatch.setattr(
        model_pick_history,
        "_load_history",
        lambda requested: ModelPickResultsView(
            all_summaries=model_rows,
            aligned_summaries=model_rows,
        ),
    )
    monkeypatch.setattr(daily_history, "_latest_activity", lambda: None)
    monkeypatch.setattr(model_pick_history, "_latest_activity", lambda: None)

    client = TestClient(app)
    bets_page = client.get("/value-bet-results").text
    picks_page = client.get("/model-pick-results").text

    assert "Aligned live history · since 10 Sep 2026" in bets_page
    assert "issued model" in bets_page
    assert "at least 60%" in bets_page
    assert "original live ledger remains unchanged" in bets_page.lower()
    assert "Aligned live history · since 10 Sep 2026" in picks_page
    assert "A1+A2 arithmetic mean" in picks_page
    assert "at least 60%" in picks_page
    assert "never Value Bets" in picks_page



def test_aligned_model_pick_cte_includes_only_60_percent_normal_or_wide_spread():
    sql = model_pick_history.aligned_production_model_picks_cte()

    assert "mcr.consensus_probability >= 0.600000" in sql
    assert "AGENT3_ABSTENTION_SPREAD_EXCEEDED" in sql
    assert "AGENT_1_SURFACE_ELO" in sql
    assert "AGENT_2_HIERARCHICAL_MARKOV" in sql
    assert "AGENT_3_INTRANSITIVITY" in sql
    assert "a2.selection_side = a1.selection_side" in sql
    assert "((a1.win_probability + a2.win_probability) / 2.0) >= 0.600000" in sql
    assert "DISTINCT ON (match_id)" in sql



class _EmptyRows:
    def fetchall(self):
        return []


class _CaptureConn:
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        return _EmptyRows()


def test_aligned_value_bet_query_filters_before_first_per_match_selection():
    conn = _CaptureConn()

    assert load_aligned_daily_bet_summaries(
        conn,
        start_date=date(2026, 9, 10),
        min_consensus_probability=Decimal("0.600000"),
    ) == ()

    assert "aligned_first_bets AS" in conn.sql
    assert "SELECT DISTINCT ON (d.match_id)" in conn.sql
    assert "d.consensus_probability >= %s" in conn.sql
    assert "ORDER BY" in conn.sql
    assert conn.params[0] == Decimal("0.600000")
    assert conn.params[1] == date(2026, 9, 10)
