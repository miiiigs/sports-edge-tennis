"""Production dashboard composition with read-only daily bet history."""

from sports_edge_tennis.dashboard.app import app
from sports_edge_tennis.dashboard.daily_history import router as daily_history_router

app.include_router(daily_history_router)

__all__ = ["app"]
