"""Do not silently grade terminal provider fixtures lacking verified results."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from sports_edge_tennis.dashboard import model_pick_history
from sports_edge_tennis.dashboard.model_pick_history import (
    DailyModelPickSummary,
    ModelPickResultsView,
    ModelPickRow,
)
from sports_edge_tennis.dashboard.production_app import app


DAY = date(2026, 10, 22)


def _row(status: str, result: str = "PENDING") -> ModelPickRow:
    return ModelPickRow(
        manager_consensus_result_id=800,
        picked_at=datetime(2026, 10, 22, 6, tzinfo=UTC),
        match_start_at=datetime(2026, 10, 22, 12, tzinfo=UTC),
        tournament="Test Open",
        player_1="Test Player 1",
        player_2="Test Player 2",
        selection="Test Player 1",
        consensus_probability=Decimal("0.71"),
        result_status=result,
        is_value_bet=False,
        manager_policy_version=None,
        model_regime_key=None,
        pick_type="A1_A2_WIDE_SPREAD",
        match_id=132533,
        latest_fixture_status=status,
    )


def test_pending_reason_observations_never_infer_final_result():
    labels = {
        "WALKOVER": "Walkover reported · result verification pending",
        "RETIRED": "Retirement reported · result verification pending",
        "COMPLETED": "Match ended · final result not verified",
        "CANCELLED": "Cancellation reported · result verification pending",
        "SCHEDULED": "Awaiting confirmed match result",
        "POSTPONED": "Postponed by provider · not graded",
    }
    for status, hint in labels.items():
        row = _row(status)
        assert row.pending_explanation == hint
        assert row.result_status == "PENDING"
    assert _row("WALKOVER", "VOID").pending_explanation is None
    assert _row("COMPLETED", "WON").pending_explanation is None


def test_results_page_and_api_show_pending_evidence_not_fabricated_grades(monkeypatch):
    summary = DailyModelPickSummary(
        decision_date=DAY, picks=1, scored=0, pending=1,
        voids=0, wins=0, losses=0, hit_rate=None
    )
    view = ModelPickResultsView(
        all_summaries=(),
        aligned_summaries=(summary,),
        selected_date=DAY,
        selected_summary=summary,
        picks=(_row("WALKOVER"),),
    )
    monkeypatch.setattr(model_pick_history, "_load_history", lambda *a, **kw: view)
    client = TestClient(app)
    page = client.get("/model-pick-results?date=2026-10-22")
    assert page.status_code == 200
    assert "Walkover reported · result verification pending" in page.text
    payload = client.get("/api/dashboard/model-pick-results?date=2026-10-22")
    assert payload.status_code == 200
    row = payload.json()["picks"][0]
    assert row["result"] == "PENDING"
    assert row["pending_explanation"] == "Walkover reported · result verification pending"
    assert row["latest_fixture_status"] == "WALKOVER"
    assert row["is_tracking_only"] is True
    assert payload.json()["days"][0]["pending"] == 1
    assert payload.json()["days"][0]["wins"] == 0
