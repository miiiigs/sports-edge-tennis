"""Run the Agent 2 cumulative-vs-recency comparison against the canonical database."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone

import psycopg

from sports_edge_tennis.data.agent2_history import (
    load_agent2_history,
    replay_historical_predictions,
)
from sports_edge_tennis.validation.agent2_recency import (
    replay_historical_predictions_windowed,
    score_agent2_replay,
)


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _parse_windows(raw: str) -> tuple[int, ...]:
    values = tuple(int(value.strip()) for value in raw.split(",") if value.strip())
    if not values or any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError("windows must be a comma-separated list of positive days")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Shadow-only Agent 2 rolling-history walk-forward experiment"
    )
    parser.add_argument("--windows", type=_parse_windows, default=(365, 730, 1460))
    parser.add_argument("--evaluation-year-start", type=int, default=2019)
    parser.add_argument("--year-start", type=int, default=2016)
    parser.add_argument("--year-end", type=int, default=None)
    args = parser.parse_args()

    dsn = _dsn()
    if not dsn:
        print("AGENT2_RECENCY_BLOCKED no_database_dsn", flush=True)
        return 2

    cutoff = datetime.now(timezone.utc)
    with psycopg.connect(dsn, autocommit=True) as conn:
        observations = load_agent2_history(
            conn,
            cutoff=cutoff,
            tour_scopes=("ATP_MAIN", "CHALLENGER"),
            year_start=args.year_start,
            year_end=args.year_end,
        )

    baseline = replay_historical_predictions(observations)
    results = [
        score_agent2_replay(
            baseline,
            window_days=None,
            evaluation_year_start=args.evaluation_year_start,
        )
    ]
    for window_days in args.windows:
        replay = replay_historical_predictions_windowed(
            observations,
            window_days=window_days,
        )
        results.append(
            score_agent2_replay(
                replay,
                window_days=window_days,
                evaluation_year_start=args.evaluation_year_start,
            )
        )

    print(
        json.dumps(
            {
                "cutoff": cutoff.isoformat(),
                "observation_count": len(observations),
                "evaluation_year_start": args.evaluation_year_start,
                "variants": [asdict(result) for result in results],
                "production_changed": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
