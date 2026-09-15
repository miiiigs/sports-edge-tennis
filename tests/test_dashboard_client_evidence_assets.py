from pathlib import Path

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard.production_app import app


def test_production_dashboard_registers_client_evidence_routes():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/dashboard/pick-lifecycle" in paths
    assert "/api/dashboard/paper-bankroll" in paths
    assert "/api/dashboard/model-evidence" in paths


def test_client_script_mounts_pick_timeline_and_paper_bankroll():
    script_path = Path(dashboard_app.__file__).resolve().parent / "static" / "dashboard.js"
    script = script_path.read_text(encoding="utf-8")

    assert "/api/dashboard/pick-lifecycle" in script
    assert "Published Pick Timeline" in script
    assert "instead of making the earlier pick disappear" in script
    assert "/api/dashboard/paper-bankroll" in script
    assert "$1,000 Live Paper Bankroll" in script
    assert "No real wager is placed" in script
