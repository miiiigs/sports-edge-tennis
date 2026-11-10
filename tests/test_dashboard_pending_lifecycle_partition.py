"""Lifecycle partition: don't call scheduled/postponed fixtures pending results."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from sports_edge_tennis.dashboard import model_pick_history as viewmod
from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary,
    ModelPickResultsView,
    ModelPickRow,
    _summary_from_row,
    load_aligned_daily_model_pick_summaries,
)
from sports_edge_tennis.dashboard.production_app import app


DAY = date(2026, 10, 27)
START = datetime(2026, 10, 27, 8, tzinfo=UTC)


def pick(i, status, *, schedule_changed=False, latest_start=None):
    return ModelPickRow(
        manager_consensus_result_id=i,
        picked_at=START - timedelta(hours=5),
        match_start_at=START,
        tournament="Synthetic Open",
        player_1=f"Player {i}", player_2=f"Opponent {i}",
        selection=f"Player {i}",
        consensus_probability=Decimal("0.80"),
        result_status="PENDING",
        is_value_bet=False,
        manager_policy_version="v1",
        model_regime_key=None,
        pick_type="MANAGER_CONSENSUS" if i == 1 else "A1_A2_WIDE_SPREAD",
        match_id=10000 + i,
        latest_fixture_status=status,
        latest_match_start_at=latest_start or START,
        schedule_changed=schedule_changed,
    )


def test_five_unsettled_fixtures_have_separate_customer_states():
    rows = (
        pick(1, "IN_PROGRESS"),
        pick(2, "NOT_STARTED"),
        pick(3, "POSTPONED"),
        pick(4, "SCHEDULED", schedule_changed=True, latest_start=START + timedelta(days=1)),
        pick(5, "COMPLETED"),
    )
    assert [p.lifecycle_status for p in rows] == [
        "AWAITING_RESULT", "NOT_STARTED", "POSTPONED",
        "RESCHEDULED", "NEEDS_RESULT_REVIEW"
    ]
    assert all(p.result_status == "PENDING" for p in rows)
    # Terminal provider observation needs settlement evidence even if rescheduled.
    assert pick(7, "COMPLETED", schedule_changed=True).lifecycle_status == "NEEDS_RESULT_REVIEW"
    assert pick(8, "WALKOVER").lifecycle_status == "NEEDS_RESULT_REVIEW"


def test_five_buckets_reconcile_to_raw_unsettled_and_do_not_change_winrate():
    # Exact existing SQL tuple column order plus 5 new classification columns.
    summary = _summary_from_row((DAY, 7, 2, 5, 0, 1, 1, 1, 1, 1, 1, 1))
    assert summary.picks == 7
    assert summary.scored == 2
    assert summary.pending == 5
    assert summary.awaiting_result == 1
    assert summary.not_started == 1
    assert summary.rescheduled == 1
    assert summary.postponed == 1
    assert summary.needs_review == 1
    assert summary.hit_rate == 0.5
    assert sum((
        summary.awaiting_result, summary.not_started, summary.rescheduled,
        summary.postponed, summary.needs_review
    )) == summary.pending


def test_html_and_api_split_status_but_keep_raw_result_and_manager_integrity(monkeypatch):
    summary = _summary_from_row((DAY, 5, 0, 5, 0, 0, 0, 1, 1, 1, 1, 1))
    rows = (
        pick(1, "IN_PROGRESS"), pick(2, "NOT_STARTED"), pick(3, "POSTPONED"),
        pick(4, "NOT_STARTED", schedule_changed=True, latest_start=START + timedelta(days=1)),
        pick(5, "WALKOVER"),
    )
    view = ModelPickResultsView(
        all_summaries=(DailyModelPickSummary(DAY, 1, 0, 1, 0, 0, 0, None),),
        aligned_summaries=(summary,),
        selected_date=DAY,
        selected_summary=summary,
        picks=rows,
    )
    monkeypatch.setattr(viewmod, "_load_history", lambda *a, **kw: view)
    c = TestClient(app)
    page = c.get("/model-pick-results?date=2026-10-27")
    assert page.status_code == 200
    assert "Awaiting result" in page.text
    assert "Unsettled total" in page.text
    assert "NOT STARTED" in page.text
    assert "RESCHEDULED" in page.text
    payload = c.get("/api/dashboard/model-pick-results?date=2026-10-27")
    assert payload.status_code == 200
    body = payload.json()
    counts = body["days"][0]
    assert counts["pending"] == 5
    assert [counts[k] for k in ("awaiting_result","not_started","rescheduled","postponed","needs_review")] == [1,1,1,1,1]
    assert [r["lifecycle_status"] for r in body["picks"]] == [
        "AWAITING_RESULT", "NOT_STARTED", "POSTPONED",
        "RESCHEDULED", "NEEDS_RESULT_REVIEW"
    ]
    assert all(r["result"] == "PENDING" for r in body["picks"])
    assert body["manager_only_days"][0]["pending"] == 1
    assert body["manager_accuracy_target"]["target_met"] is False


def test_aligned_partition_query_runs_on_full_postgres_schema(db):
    # Real-schema query compilation: latest-state lateral joins + timezone-safe
    # rescheduled detection; no provider calls or writes.
    rows = load_aligned_daily_model_pick_summaries(db, limit=5, timezone_name="UTC")
    for s in rows:
        assert s.pending == (
            s.awaiting_result + s.not_started + s.rescheduled
            + s.postponed + s.needs_review
        )
