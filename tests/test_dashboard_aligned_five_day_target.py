"""Regression contract: Brian acceptance uses original ALL-aligned picks."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from sports_edge_tennis.dashboard import model_pick_history as history
from sports_edge_tennis.dashboard.aligned_five_day_target import aligned_five_day_target
from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary, ModelPickResultsView,
)
from sports_edge_tennis.dashboard.production_app import app

TODAY = date(2026, 10, 29)


def row(day, wins=0, losses=0, voids=0, pending=0):
    graded = wins + losses
    return DailyModelPickSummary(
        decision_date=day, picks=graded + voids + pending,
        scored=graded, pending=pending, voids=voids, wins=wins,
        losses=losses, hit_rate=wins / graded if graded else None,
    )


def test_oct28_qualifies_when_last_source_confirmed_winner_arrives():
    rows = [row(date(2026, 10, 28), 9, 3, 1)]
    report = aligned_five_day_target(rows, as_of_date=TODAY)
    assert report["completed_qualifying_days"] == 1
    assert report["target_met"] is False
    assert report["days"][0]["status"] == "MET_73"
    assert report["days"][0]["win_rate"] == 0.75
    assert report["days"][1]["status"] == "IN_PROGRESS"


def test_rounded_72_point_7_is_not_73_and_pending_blocks_75():
    d = date(2026, 10, 28)
    report = aligned_five_day_target([row(d, 8, 3, 1)], as_of_date=TODAY)
    assert report["days"][0]["status"] == "BELOW_73"
    report = aligned_five_day_target([row(d, 9, 3, 1, 1)], as_of_date=TODAY)
    assert report["days"][0]["status"] == "UNSETTLED"
    assert report["completed_qualifying_days"] == 0


def test_no_picks_duplicate_inconsistent_and_future_never_qualify():
    d = date(2026, 10, 28)
    assert aligned_five_day_target([], as_of_date=TODAY)["days"][0]["status"] == "NO_PICKS"
    assert aligned_five_day_target([row(d, 3, 1), row(d, 3, 1)], as_of_date=TODAY)["days"][0]["status"] == "INCONSISTENT"
    bad = DailyModelPickSummary(d, 3, 4, 0, 0, 3, 1, 0.75)
    assert aligned_five_day_target([bad], as_of_date=TODAY)["days"][0]["status"] == "INCONSISTENT"
    future = aligned_five_day_target([row(TODAY, 100, 0)], as_of_date=TODAY)
    assert future["days"][1]["status"] == "IN_PROGRESS"


def test_rolling_window_never_skips_bad_or_missing_days():
    d = date(2026, 10, 28)
    rows = [row(d + timedelta(days=i), 3, 1) for i in range(6)]
    first = aligned_five_day_target(rows, as_of_date=date(2026, 11, 2))
    assert first["start"] == "2026-10-28"
    assert first["days"][-1]["status"] == "MET_73"
    assert first["target_met"]
    after = aligned_five_day_target(rows, as_of_date=date(2026, 11, 3))
    assert after["start"] == "2026-10-29"
    assert after["end"] == "2026-11-02"
    missing = aligned_five_day_target([x for x in rows if x.decision_date != date(2026, 10, 30)], as_of_date=date(2026, 11, 3))
    assert missing["target_met"] is False
    assert missing["days"][1]["status"] == "NO_PICKS"


def test_html_and_api_prioritize_all_aligned_not_manager(monkeypatch):
    day = date(2026, 10, 28)
    aligned = row(day, 9, 3, 1)
    manager = row(day, 1, 3)
    view = ModelPickResultsView(
        aligned_summaries=(aligned,),
        aligned_acceptance_summaries=(aligned,),
        all_summaries=(manager,), selected_date=day,
    )
    monkeypatch.setattr(history, "_load_history", lambda *a, **kw: view)
    monkeypatch.setattr(history, "dashboard_today", lambda tz: TODAY)
    monkeypatch.setattr(history, "_aligned_acceptance_today", lambda: TODAY)
    client = TestClient(app)
    api = client.get("/api/dashboard/model-pick-results")
    assert api.status_code == 200
    payload = api.json()
    assert payload["aligned_accuracy_target"]["scope"] == "ALL_ORIGINAL_ALIGNED_MODEL_PICKS"
    assert payload["aligned_accuracy_target"]["completed_qualifying_days"] == 1
    assert payload["aligned_accuracy_target"]["days"][0]["wins"] == 9
    assert payload["manager_accuracy_target"]["days"][0]["wins"] == 1
    assert payload["manager_accuracy_target"]["days"][0]["status"] == "BELOW_TARGET"
    page = client.get("/model-pick-results")
    assert page.status_code == 200
    assert "Brian's 73% goal" in page.text
    assert "1 / 5 qualifying completed days" in page.text
    assert "Manager-only accuracy" in page.text


def test_five_day_json_is_manila_fixed_even_if_viewer_changes_timezone(monkeypatch):
    d = date(2026, 10, 28)
    same = row(d, 9, 3, 1)
    view = ModelPickResultsView(
        aligned_summaries=(row(d, 1, 3),),
        aligned_acceptance_summaries=(same,),
        all_summaries=(row(d, 1, 3),),
    )
    monkeypatch.setattr(history, "_load_history", lambda *a, **kw: view)
    monkeypatch.setattr(history, "dashboard_today", lambda tz: TODAY)
    monkeypatch.setattr(history, "_aligned_acceptance_today", lambda: TODAY)
    c = TestClient(app)
    for k in ("utc", "london", "philippines"):
        p = c.get("/api/dashboard/model-pick-results", cookies={"tennis_dashboard_timezone": k})
        assert p.status_code == 200
        assert p.json()["aligned_accuracy_target"]["timezone"] == "Asia/Manila"
        assert p.json()["aligned_accuracy_target"]["days"][0]["wins"] == 9
