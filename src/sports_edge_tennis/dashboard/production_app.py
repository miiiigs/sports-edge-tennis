"""Production dashboard composition with read-only daily bet history."""

from fastapi.responses import HTMLResponse

from sports_edge_tennis.dashboard.app import app
from sports_edge_tennis.dashboard.daily_history import api_bet_history, bet_history

app.add_api_route(
    "/bet-history",
    bet_history,
    methods=["GET"],
    response_class=HTMLResponse,
    name="bet_history",
)
app.add_api_route(
    "/api/dashboard/bet-history",
    api_bet_history,
    methods=["GET"],
    name="api_bet_history",
)

__all__ = ["app"]
