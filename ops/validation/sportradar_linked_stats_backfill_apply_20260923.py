from __future__ import annotations

import json
import os
from datetime import date

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.validation.sportradar_linked_stats_backfill import (
    apply_linked_stats_backfill,
)


def _date_env(name: str, default: str) -> date:
    return date.fromisoformat(os.environ.get(name, default))


def main() -> int:
    dsn = load_secret("TENNIS_DATABASE_URL") or load_secret("DATABASE_URL")
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not dsn:
        raise RuntimeError("database URL unavailable")
    if not api_key:
        raise RuntimeError("SPORTRADAR_TENNIS_API_KEY unavailable")

    result = apply_linked_stats_backfill(
        dsn=dsn,
        api_key=api_key,
        start_date=_date_env("SPORTRADAR_STATS_START_DATE", "2026-09-10"),
        end_date=_date_env("SPORTRADAR_STATS_END_DATE", "2026-09-21"),
    )
    print(
        "SPORTRADAR_LINKED_STATS_APPLY "
        + json.dumps(result, sort_keys=True),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
