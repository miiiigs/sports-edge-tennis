from sports_edge_tennis.dashboard.production_app import app


def test_bet_history_routes_are_get_only():
    methods_by_path = {route.path: getattr(route, "methods", set()) for route in app.routes}
    assert methods_by_path["/bet-history"] == {"GET"}
    assert methods_by_path["/api/dashboard/bet-history"] == {"GET"}
