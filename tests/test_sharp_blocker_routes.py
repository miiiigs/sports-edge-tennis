from __future__ import annotations

from sports_edge_tennis.dashboard.sharp_blocker_routes import (
    load_recent_live_missing_sharp_run_ids,
)


def test_empty_database_has_no_recent_live_sharp_blocker_runs(db):
    assert load_recent_live_missing_sharp_run_ids(db, limit=5) == ()
