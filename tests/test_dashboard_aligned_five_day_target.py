"""Regression: Brian's five-day target begins Oct 27 on ALL original aligned picks."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from sports_edge_tennis.dashboard import model_pick_history as history
from sports_edge_tennis.dashboard.aligned_five_day_target import aligned_five_day_target
from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary, ModelPickResultsView,
)
from sports_edge_tennis.dashboard.production_app import app

TODAY = date(2026, 10, 29)
OCT27 = date(2026, 10, 27)
OCT28 = date(2026, 10, 28)


def row(day, wins=0, losses=0, voids=0, pending=0):
    graded = wins + losses
    return DailyModelPickSummary(
        decision_date=day, picks=graded + voids + pending,
        scored=graded, pending=pending, voids=voids, wins=wins,
        losses=losses, hit_rate=wins / graded if graded else None,
    )


def test_oct27_and_oct28_both_qualify_after_original_results_settle():
    rows = [
        row(OCT27, 31, 8, 2),
        row(OCT28, 9, 3, 1),
        row(TODAY, 1, 3, 1, 16),
    ]
    report = aligned_five_day_target(rows, as_of_date=TODAY)
    assert report["scope"] == "ALL_ORIGINAL_ALIGNED_MODEL_PICKS"
    assert report["start"] == "2026-10-27"
    assert report["end"] == "2026-10-31"
    assert report["completed_qualifying_days"] == 2
    assert report["target_met"] is False
    assert report["days"][0]["status"] == "MET_73"
    assert report["days"][0]["picks"] == 41
    assert report["days"][0]["win_rate"] == 31 / 39
    assert report["days"][1]["status"] == "MET_73"
    assert report["days"][1]["picks"] == 13
    assert report["days"][1]["win_rate"] == 0.75
    assert report["days"][2]["status"] == "IN_PROGRESS"


def test_oct27_with_unsettled_original_picks_does_not_qualify():
    report = aligned_five_day_target(
        [row(OCT27, 26, 8, 2, 5), row(OCT28, 9, 3, 1)],
        as_of_date=TODAY,
    )
    assert report["days"][0]["status"] == "UNSETTLED"
    assert report["completed_qualifying_days"] == 1


def test_rounded_72_point_7_is_not_73_and_pending_blocks_75():
    baseline = [row(OCT27, 31, 8, 2)]
    low = aligned_five_day_target(baseline + [row(OCT28, 8, 3, 1)], as_of_date=TODAY)
    assert low["days"][1]["status"] == "BELOW_73"
    assert low["completed_qualifying_days"] == 1
    incomplete = aligned_five_day_target(
        baseline + [row(OCT28, 9, 3, 1, 1)], as_of_date=TODAY
    )
    assert incomplete["days"][1]["status"] == "UNSETTLED"
    assert incomplete["completed_qualifying_days"] == 1


def test_no_picks_duplicates_bad_counts_and_future_never_qualify():
    assert aligned_five_day_target([], as_of_date=TODAY)["days"][0]["status"] == "NO_PICKS"
    duplicate = aligned_five_day_target(
        [row(OCT27, 3, 1), row(OCT27, 3, 1)], as_of_date=TODAY
    )
    assert duplicate["days"][0]["status"] == "INCONSISTENT"
    bad = DailyModelPickSummary(OCT27, 3, 4, 0, 0, 3, 1, 0.75)
    assert aligned_five_day_target([bad], as_of_date=TODAY)["days"][0]["status"] == "INCONSISTENT"
    future = aligned_five_day_target([row(TODAY, 100, 0)], as_of_date=TODAY)
    assert future["days"][2]["status"] == "IN_PROGRESS"


def test_initial_window_and_rolling_dates_never_skip_bad_days():
    rows = [row(OCT27 + timedelta(days=i), 3, 1) for i in range(7)]
    initial = aligned_five_day_target(rows, as_of_date=date(2026, 11, 1))
    assert initial["start"] == "2026-10-27"
    assert initial["end"] == "2026-10-31"
    assert initial["target_met"] is True
    rolling = aligned_five_day_target(rows, as_of_date=date(2026, 11, 3))
    assert rolling["start"] == "2026-10-29"
    assert rolling["end"] == "2026-11-02"
    assert rolling["target_met"] is True
    missing = aligned_five_day_target(
        [r for r in rows if r.decision_date != date(2026, 10, 30)],
        as_of_date=date(2026, 11, 3),
    )
    assert missing["target_met"] is False
    assert missing["days"][1]["status"] == "NO_PICKS"


def _fake_view():
    aligned = (row(OCT27, 31, 8, 2), row(OCT28, 9, 3, 1))
    manager = row(OCT28, 1, 3)
    return ModelPickResultsView(
        aligned_summaries=aligned, aligned_acceptance_summaries=aligned,
        all_summaries=(manager,), selected_date=OCT28,
    )


def test_html_and_api_prioritize_all_aligned_not_manager(monkeypatch):
    monkeypatch.setattr(history, "_load_history", lambda *a, **kw: _fake_view())
    monkeypatch.setattr(history, "dashboard_today", lambda tz: TODAY)
    monkeypatch.setattr(history, "_aligned_acceptance_today", lambda: TODAY)
    client = TestClient(app)
    api = client.get("/api/dashboard/model-pick-results")
    assert api.status_code == 200
    payload = api.json()
    target = payload["aligned_accuracy_target"]
    assert target["scope"] == "ALL_ORIGINAL_ALIGNED_MODEL_PICKS"
    assert target["completed_qualifying_days"] == 2
    assert [x["wins"] for x in target["days"][:2]] == [31, 9]
    assert payload["manager_accuracy_target"]["days"][0]["wins"] == 1
    assert payload["manager_accuracy_target"]["days"][0]["status"] == "BELOW_TARGET"
    page = client.get("/model-pick-results")
    assert page.status_code == 200
    assert "Brian's 73% goal" in page.text
    assert "2 / 5 qualifying completed days" in page.text
    assert "Manager-only accuracy" in page.text


def test_manila_acceptance_independent_of_viewer_timezone(monkeypatch):
    view = ModelPickResultsView(
        aligned_summaries=(row(OCT28, 1, 3),),
        aligned_acceptance_summaries=(row(OCT27, 31, 8, 2), row(OCT28, 9, 3, 1)),
        all_summaries=(row(OCT28, 1, 3),),
    )
    monkeypatch.setattr(history, "_load_history", lambda *a, **kw: view)
    monkeypatch.setattr(history, "dashboard_today", lambda tz: TODAY)
    monkeypatch.setattr(history, "_aligned_acceptance_today", lambda: TODAY)
    client = TestClient(app)
    for zone in ("utc", "london", "philippines"):
        client.cookies.set("tennis_dashboard_timezone", zone)
        reply = client.get("/api/dashboard/model-pick-results")
        assert reply.status_code == 200
        target = reply.json()["aligned_accuracy_target"]
        assert target["timezone"] == "Asia/Manila"
        assert target["start"] == "2026-10-27"
        assert [x["wins"] for x in target["days"][:2]] == [31, 9]
