"""Issue #256: aligned Results API days must reconcile with its detail cohort.

Synthetic pure-route regression. Production predictions, decisions and original
Manager-only summary remain immutable; only read-only API presentation changes.
"""

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


DAY = date(2026, 10, 21)


def _summary(picks: int) -> DailyModelPickSummary:
    return DailyModelPickSummary(
        decision_date=DAY,
        picks=picks,
        scored=0,
        pending=picks,
        voids=0,
        wins=0,
        losses=0,
        hit_rate=None,
    )


def _pick(i: int, kind: str) -> ModelPickRow:
    return ModelPickRow(
        manager_consensus_result_id=i,
        picked_at=datetime(2026, 10, 21, 6, 0, tzinfo=UTC),
        match_start_at=datetime(2026, 10, 21, 10, 0, tzinfo=UTC),
        tournament="Synthetic Open",
        player_1=f"Player {i}",
        player_2=f"Opponent {i}",
        selection=f"Player {i}",
        consensus_probability=Decimal("0.65"),
        result_status="PENDING",
        is_value_bet=False,
        manager_policy_version="v1",
        model_regime_key=None,
        pick_type=kind,
        match_id=50000 + i,
    )


def test_manager_and_tracking_populations_are_explicit_and_reconciled(monkeypatch):
    # Reproduces the reported 6 Manager day vs 16 aligned detail records.
    rows = tuple(
        _pick(i, "MANAGER_CONSENSUS" if i <= 6 else "A1_A2_WIDE_SPREAD")
        for i in range(1, 17)
    )
    view = ModelPickResultsView(
        all_summaries=(_summary(6),),
        aligned_summaries=(_summary(16),),
        selected_date=DAY,
        selected_summary=_summary(16),
        picks=rows,
    )
    monkeypatch.setattr(model_pick_history, "_load_history", lambda *a, **kw: view)
    response = TestClient(app).get(f"/api/dashboard/model-pick-results?date={DAY}")
    assert response.status_code == 200
    body = response.json()

    assert body["selected_date"] == DAY.isoformat()
    assert body["days"][0]["date"] == DAY.isoformat()
    assert body["days"][0]["picks"] == len(body["picks"]) == 16
    assert body["manager_only_days"][0]["picks"] == 6
    assert body["aligned_live_history"]["includes_a1_a2_wide_spread_tracking"] is True

    manager = [x for x in body["picks"] if x["is_manager_consensus"]]
    research = [x for x in body["picks"] if x["is_tracking_only"]]
    assert len(manager) == 6
    assert len(research) == 10
    assert all(x["pick_type"] == "MANAGER_CONSENSUS" for x in manager)
    assert all(x["pick_type"] == "A1_A2_WIDE_SPREAD" for x in research)
    assert all(not x["is_manager_consensus"] for x in research)
    assert all(not x["is_tracking_only"] for x in manager)
    assert all(not x["is_value_bet"] for x in research)


def test_empty_aligned_day_does_not_inherit_manager_audit_count(monkeypatch):
    # An empty aligned ledger must never display old Manager counts as picks.
    view = ModelPickResultsView(
        all_summaries=(_summary(6),),
        aligned_summaries=(),
        selected_date=DAY,
        picks=(),
    )
    monkeypatch.setattr(model_pick_history, "_load_history", lambda *a, **kw: view)
    body = TestClient(app).get(f"/api/dashboard/model-pick-results?date={DAY}").json()
    assert body["days"] == []
    assert body["picks"] == []
    assert body["manager_only_days"][0]["picks"] == 6
