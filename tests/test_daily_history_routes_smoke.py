from sports_edge_tennis.dashboard.production_app import app


def test_production_dashboard_registers_daily_bet_history_routes():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/bet-history" in paths
    assert "/api/dashboard/bet-history" in paths
