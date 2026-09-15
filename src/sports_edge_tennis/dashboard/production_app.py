"""Production dashboard composition with read-only client evidence routes."""

from fastapi.responses import HTMLResponse

from sports_edge_tennis.dashboard.app import app
from sports_edge_tennis.dashboard.daily_history import api_bet_history, bet_history
from sports_edge_tennis.dashboard.pick_lifecycle_routes import api_pick_lifecycle

app.get(
    "/bet-history",
    response_class=HTMLResponse,
    name="bet_history",
)(bet_history)
app.get(
    "/api/dashboard/bet-history",
    name="api_bet_history",
)(api_bet_history)
app.get(
    "/api/dashboard/pick-lifecycle",
    name="api_pick_lifecycle",
)(api_pick_lifecycle)

_required_paths = {
    "/bet-history",
    "/api/dashboard/bet-history",
    "/api/dashboard/pick-lifecycle",
}
_registered_paths = {getattr(route, "path", None) for route in app.routes}
_missing_paths = _required_paths - _registered_paths
if _missing_paths:
    raise RuntimeError(f"production dashboard missing required routes: {sorted(_missing_paths)}")

__all__ = ["app"]
