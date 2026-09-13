"""Owner-only dashboard entrypoint including aggregate evidence routes."""

from sports_edge_tennis.dashboard.evidence_app import app
from sports_edge_tennis.dashboard import owner_access as _owner_access  # noqa: F401

__all__ = ["app"]
