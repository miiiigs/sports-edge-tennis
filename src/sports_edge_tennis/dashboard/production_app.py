"""Production dashboard composition with read-only client evidence routes."""

from fastapi.responses import HTMLResponse

from sports_edge_tennis.dashboard.app import app
from sports_edge_tennis.dashboard.daily_history import api_bet_history, bet_history
from sports_edge_tennis.dashboard.model_evidence_routes import api_model_evidence
from sports_edge_tennis.dashboard.opportunity_evidence_routes import api_opportunity_evidence
from sports_edge_tennis.dashboard.paper_bankroll_routes import api_paper_bankroll
from sports_edge_tennis.dashboard.pick_lifecycle_routes import api_pick_lifecycle
from sports_edge_tennis.dashboard.sharp_blocker_routes import (
    api_sharp_blockers,
    log_recent_sharp_blockers_on_startup,
)

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
app.get(
    "/api/dashboard/paper-bankroll",
    name="api_paper_bankroll",
)(api_paper_bankroll)
app.get(
    "/api/dashboard/model-evidence",
    name="api_model_evidence",
)(api_model_evidence)
app.get(
    "/api/dashboard/opportunity-evidence",
    name="api_opportunity_evidence",
)(api_opportunity_evidence)
app.get(
    "/api/dashboard/sharp-blockers",
    name="api_sharp_blockers",
)(api_sharp_blockers)

# Bounded and fail-isolated. This exists so historical missing-sharp blockers can be
# inspected in Railway runtime logs even after their prediction runs were already
# consumed by the prospective shadow ledger.
app.add_event_handler("startup", log_recent_sharp_blockers_on_startup)

_required_paths = {
    "/bet-history",
    "/api/dashboard/bet-history",
    "/api/dashboard/pick-lifecycle",
    "/api/dashboard/paper-bankroll",
    "/api/dashboard/model-evidence",
    "/api/dashboard/opportunity-evidence",
    "/api/dashboard/sharp-blockers",
}
_registered_paths = {getattr(route, "path", None) for route in app.routes}
_missing_paths = _required_paths - _registered_paths
if _missing_paths:
    raise RuntimeError(f"production dashboard missing required routes: {sorted(_missing_paths)}")

__all__ = ["app"]
