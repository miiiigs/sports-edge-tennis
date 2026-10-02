"""Production dashboard composition with read-only client evidence routes."""

from fastapi.responses import HTMLResponse

from sports_edge_tennis.dashboard.app import app
from sports_edge_tennis.dashboard.daily_history import api_bet_history, bet_history
from sports_edge_tennis.dashboard.model_evidence_routes import api_model_evidence
from sports_edge_tennis.dashboard.model_pick_history import (
    api_model_pick_results,
    model_pick_results,
)
from sports_edge_tennis.dashboard.observability_routes import (
    api_agent_abstention,
    api_result_coverage,
)
from sports_edge_tennis.dashboard.opportunity_evidence_routes import api_opportunity_evidence
from sports_edge_tennis.dashboard.paper_bankroll_routes import api_paper_bankroll
from sports_edge_tennis.dashboard.pick_lifecycle_routes import api_pick_lifecycle
from sports_edge_tennis.dashboard.sharp_blocker_routes import api_sharp_blockers

app.get(
    "/bet-history",
    response_class=HTMLResponse,
    name="bet_history",
)(bet_history)
app.get(
    "/value-bet-results",
    response_class=HTMLResponse,
    name="value_bet_results",
)(bet_history)
app.get(
    "/model-pick-results",
    response_class=HTMLResponse,
    name="model_pick_results",
)(model_pick_results)
app.get(
    "/api/dashboard/bet-history",
    name="api_bet_history",
)(api_bet_history)
app.get(
    "/api/dashboard/value-bet-results",
    name="api_value_bet_results",
)(api_bet_history)
app.get(
    "/api/dashboard/model-pick-results",
    name="api_model_pick_results",
)(api_model_pick_results)
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
app.get(
    "/api/dashboard/agent-abstention",
    name="api_agent_abstention",
)(api_agent_abstention)
app.get(
    "/api/dashboard/result-coverage",
    name="api_result_coverage",
)(api_result_coverage)

_required_paths = {
    "/bet-history",
    "/value-bet-results",
    "/model-pick-results",
    "/api/dashboard/bet-history",
    "/api/dashboard/value-bet-results",
    "/api/dashboard/model-pick-results",
    "/api/dashboard/pick-lifecycle",
    "/api/dashboard/paper-bankroll",
    "/api/dashboard/model-evidence",
    "/api/dashboard/opportunity-evidence",
    "/api/dashboard/sharp-blockers",
    "/api/dashboard/agent-abstention",
    "/api/dashboard/result-coverage",
}
_registered_paths = {getattr(route, "path", None) for route in app.routes}
_missing_paths = _required_paths - _registered_paths
if _missing_paths:
    raise RuntimeError(f"production dashboard missing required routes: {sorted(_missing_paths)}")

__all__ = ["app"]
