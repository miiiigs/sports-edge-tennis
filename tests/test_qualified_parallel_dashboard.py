"""Contract tests for read-only parallel qualified-picks display.

The comparison must use first-issued confidence without changing any original
pick, settlement, market decision or the official 73% scorecard.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.picks import (
    BLOCKED, MODEL_PICK, WIDE_SPREAD_CLASSIFICATION, PickCard,
)
from sports_edge_tennis.dashboard.production_app import app
from sports_edge_tennis.dashboard.qualified_parallel import (
    CONFIDENCE_FLOOR, POLICY_ID, build_qualified_parallel,
)


def _card(mid: int, prob: str | None, result: str | None) -> PickCard:
    return PickCard(
        match_id=mid,
        kind=MODEL_PICK,
        classification="HIGH_CONFIDENCE_PICK",
        player=f"Player {mid}",
        opponent="Opponent",
        win_probability=Decimal(prob) if prob is not None else None,
        tournament="Test tournament",
        competition_tier="ATP_CHALLENGER",
        scheduled_start=None,
        result_status=result,
    )


def test_parallel_gate_uses_exact_70_percent_and_original_results() -> None:
    cards = (
        _card(1, "0.700", "WON"),
        _card(2, "0.699999", "LOST"),
        _card(3, "0.80", "LOST"),
        _card(4, "0.90", "VOID"),
        _card(5, "0.91", None),
    )
    out = build_qualified_parallel(cards)
    assert CONFIDENCE_FLOOR == Decimal("0.70")
    assert POLICY_ID == "PIT_CONFIDENCE_GE70_V1"
    assert out.original == 5
    assert out.match_ids == (1, 3, 4, 5)
    assert (out.retained, out.won, out.lost, out.void, out.pending) == (4, 1, 1, 1, 1)
    assert out.win_rate == 0.5
    assert not out.settled
    assert out.coverage == 4 / 5
    assert out.as_dict()["live_selection_changed"] is False
    assert out.as_dict()["research_only"] is True
    assert tuple(cards) == cards


def test_wide_spread_uses_original_a1_a2_average_not_missing_manager_value() -> None:
    tracking = replace(
        _card(1, None, "WON"),
        classification=WIDE_SPREAD_CLASSIFICATION,
        primary_average_probability=Decimal("0.70"),
        primary_a1_probability=Decimal("0.80"),
        primary_a2_probability=Decimal("0.60"),
    )
    out = build_qualified_parallel((tracking, _card(2, "0.70", "LOST")))
    assert out.match_ids == (1, 2)
    assert out.won == 1 and out.lost == 1


def test_veto_blocked_manager_pick_remains_in_original_shadow_population() -> None:
    blocked = replace(_card(1, "0.71", "LOST"), kind=BLOCKED, classification="VETOED")
    out = build_qualified_parallel((blocked,))
    assert out.original == 1 and out.retained == 1 and out.lost == 1


def test_invalid_probability_and_duplicate_match_fail_closed() -> None:
    invalid = replace(_card(1, None, "WON"), win_probability=Decimal("NaN"))
    overflow = _card(2, "1.01", "WON")
    duplicate = _card(3, "0.98", "WON")
    out = build_qualified_parallel((invalid, overflow, duplicate, replace(duplicate, result_status="LOST")))
    assert out.original == 4
    assert out.retained == 1
    assert out.won == 1 and out.lost == 0
    assert out.match_ids == (3,)


def test_ungraded_and_unknown_states_do_not_imply_a_win() -> None:
    out = build_qualified_parallel((_card(1, "0.70", None), _card(2, "0.72", "UNSUPPORTED")))
    assert out.pending == 2 and out.graded == 0 and out.win_rate is None
    assert not out.settled


def test_dashboard_today_shows_both_groups_and_api_marks_research_only(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    client = TestClient(app)
    page = client.get("/today?tz=philippines")
    assert page.status_code == 200
    assert "Original vs. Qualified" in page.text
    assert "research-only" in page.text
    assert "All original Model Picks" in page.text
    assert "Qualified ≥70%" in page.text
    assert page.text.count('<article class="pick-card') == 3

    api = client.get("/api/dashboard/today?tz=philippines")
    assert api.status_code == 200
    payload = api.json()
    assert payload["qualified_parallel"]["policy"] == POLICY_ID
    assert payload["qualified_parallel"]["research_only"] is True
    assert payload["qualified_parallel"]["live_selection_changed"] is False
    assert payload["pick_counts"]["total"] == 3
