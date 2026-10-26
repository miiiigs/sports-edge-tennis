"""Regression: Brian's 73% on five consecutive completed days requirement."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from sports_edge_tennis.dashboard import model_pick_history
from sports_edge_tennis.dashboard.manager_five_day_target import manager_five_day_target
from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary,
    ModelPickResultsView,
)
from sports_edge_tennis.dashboard.production_app import app


AS_OF = date(2026, 10, 27)


def row(days_ago: int, wins: int, losses: int, pending: int = 0, voids: int = 0):
    scored = wins + losses
    return DailyModelPickSummary(
        decision_date=AS_OF - timedelta(days=days_ago),
        picks=scored + pending + voids,
        scored=scored,
        pending=pending,
        voids=voids,
        wins=wins,
        losses=losses,
        hit_rate=wins / scored if scored else None,
    )


def test_five_consecutive_fully_settled_days_meet_target():
    report = manager_five_day_target([row(i, 3, 1) for i in range(1, 6)], as_of_date=AS_OF)
    assert report["observed_consecutive_days"] == 5
    assert report["target_met"] is True
    assert all(x["win_rate"] == 0.75 for x in report["days"])
    assert report["low_sample_warning"] is True


def test_72_point_7_percent_does_not_round_up_to_73():
    report = manager_five_day_target([row(1, 8, 3), row(2, 3, 1)], as_of_date=AS_OF)
    assert report["observed_consecutive_days"] == 0
    assert report["days"][0]["status"] == "BELOW_TARGET"


def test_pending_on_a_winning_day_blocks_streak_and_void_not_scored():
    report = manager_five_day_target(
        [row(1, 3, 1, pending=1, voids=2), row(2, 3, 1)],
        as_of_date=AS_OF,
    )
    assert report["days"][0]["win_rate"] == 0.75
    assert report["days"][0]["status"] == "AWAITING_RESULTS"
    assert report["days"][0]["scored"] == 4
    assert report["observed_consecutive_days"] == 0


def test_missing_day_does_not_skip_to_a_prior_winning_day():
    report = manager_five_day_target([row(1, 3, 1), row(3, 3, 1)], as_of_date=AS_OF)
    assert report["days"][1]["status"] == "NO_PICKS"
    assert report["observed_consecutive_days"] == 1


def test_current_day_is_excluded_even_if_hundred_percent():
    report = manager_five_day_target([row(0, 50, 0)], as_of_date=AS_OF)
    assert report["target_met"] is False
    assert report["days"][0]["status"] == "NO_PICKS"


def test_all_void_no_graded_and_duplicate_days_cannot_pass():
    report = manager_five_day_target([row(1, 0, 0, voids=4), row(2, 1, 0), row(2, 1, 0)], as_of_date=AS_OF)
    assert report["days"][0]["status"] == "NO_GRADED"
    assert report["days"][1]["status"] == "DUPLICATE_DAY"


def test_inconsistent_counts_never_qualify():
    original = row(1, 3, 1)
    inconsistent = DailyModelPickSummary(
        decision_date=original.decision_date, picks=3, scored=4, pending=0,
        voids=0, wins=3, losses=1, hit_rate=0.75
    )
    report = manager_five_day_target([inconsistent], as_of_date=AS_OF)
    assert report["days"][0]["status"] == "INCONSISTENT"


def test_dashboard_api_and_html_use_manager_only_not_aligned_tracking(monkeypatch):
    # Aligned contains 9 wins / 1 loss but Manager actually lost 3 of 4.
    view = ModelPickResultsView(
        all_summaries=(row(1, 1, 3),),
        aligned_summaries=(row(1, 9, 1),),
        selected_date=AS_OF - timedelta(days=1),
    )
    monkeypatch.setattr(model_pick_history, "_load_history", lambda *a, **kw: view)
    monkeypatch.setattr(model_pick_history, "dashboard_today", lambda tz: AS_OF)
    client = TestClient(app)
    api = client.get("/api/dashboard/model-pick-results?date=2026-10-26")
    assert api.status_code == 200
    payload = api.json()
    report = payload["manager_accuracy_target"]
    assert report["scope"] == "FIRST_PRODUCTION_MANAGER_ONLY"
    assert report["days"][0]["wins"] == 1
    assert report["days"][0]["losses"] == 3
    assert report["days"][0]["status"] == "BELOW_TARGET"
    assert payload["days"][0]["wins"] == 9
    page = client.get("/model-pick-results?date=2026-10-26")
    assert page.status_code == 200
    assert "Manager accuracy target" in page.text
    assert "0 / 5 consecutive days meet 73%" in page.text
