from __future__ import annotations

"""Bounded read-only historical Agent 2 replay for the stats rehearsal.

Loads point-in-time Agent 2 history from the canonical database (now including
the linked-only Sportradar result/stat observations), replays the production
Agent 2 configuration, and reports coverage/accuracy over the 2026 evaluation
window. This writes nothing and does not touch production authority.
"""

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone

import psycopg

from sports_edge_tennis.data.agent2_history import (
    load_agent2_history,
    replay_historical_predictions,
)
from sports_edge_tennis.validation.agent2_recency import score_agent2_replay


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")

    cutoff = datetime.now(timezone.utc)
    with psycopg.connect(dsn, autocommit=True) as conn:
        observations = load_agent2_history(
            conn,
            cutoff=cutoff,
            tour_scopes=("ATP_MAIN", "CHALLENGER"),
            year_start=2016,
            year_end=None,
        )

    replay = replay_historical_predictions(observations)
    score = score_agent2_replay(
        replay,
        window_days=None,
        evaluation_year_start=2026,
    )

    print(
        "AGENT2_HISTORICAL_REPLAY "
        + json.dumps(
            {
                "status": "OK",
                "cutoff": cutoff.isoformat(),
                "observation_count": len(observations),
                "replayed_matches": len(replay.records),
                "production_changed": False,
                "writes": 0,
                "score": asdict(score),
            },
            sort_keys=True,
            default=str,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
