from datetime import datetime, timezone
from decimal import Decimal

from sports_edge_tennis.dashboard.pick_lifecycle import PickLifecycleView
from sports_edge_tennis.dashboard.production_app import app


def _view(**overrides):
    values = {
        "match_id": 130361,
        "player_1": "B. Shick",
        "player_2": "E. Arutiunian",
        "tournament": "Tiburon",
        "competition_tier": "CHALLENGER",
        "scheduled_start": datetime(2026, 9, 15, 18, 30, tzinfo=timezone.utc),
        "first_pick_at": datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc),
        "first_selected_side": 2,
        "first_probability": Decimal("0.75"),
        "latest_model_at": datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        "latest_manager_status": "CONSENSUS",
        "latest_selected_side": 2,
        "latest_probability": Decimal("0.74"),
        "latest_active_agent_count": 2,
        "latest_abstaining_agent_count": 1,
        "latest_market_status": "NO_BET",
        "latest_market_reason": "EDGE_BELOW_THRESHOLD",
        "latest_market_price": Decimal("1.45"),
        "latest_bookmaker": "PINNACLE",
        "latest_edge_pct": Decimal("0.01"),
    }
    values.update(overrides)
    return PickLifecycleView(**values)


def test_lifecycle_keeps_first_pick_when_price_no_longer_qualifies():
    row = _view()
    assert row.first_selected_player == "E. Arutiunian"
    assert row.model_pick_still_active is True
    assert row.model_pick_changed is False
    assert row.latest_lifecycle_state == "PICK_ONLY_NO_VALUE_NOW"


def test_lifecycle_marks_model_change_without_rewriting_first_pick():
    row = _view(latest_selected_side=1, latest_probability=Decimal("0.61"))
    assert row.first_selected_player == "E. Arutiunian"
    assert row.latest_selected_player == "B. Shick"
    assert row.model_pick_changed is True
    assert row.latest_lifecycle_state == "MODEL_CHANGED"


def test_lifecycle_marks_later_model_filter_without_erasing_first_pick():
    row = _view(
        latest_manager_status="NO_BET",
        latest_selected_side=None,
        latest_probability=None,
        latest_market_status=None,
        latest_market_reason=None,
        latest_market_price=None,
        latest_bookmaker=None,
        latest_edge_pct=None,
    )
    assert row.first_selected_player == "E. Arutiunian"
    assert row.latest_selected_player is None
    assert row.latest_lifecycle_state == "MODEL_NO_LONGER_QUALIFIES"


def test_production_dashboard_registers_pick_lifecycle_route():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/dashboard/pick-lifecycle" in paths
