"""Read-only audit of the historical result recency available to Agent 3."""

from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import psycopg

from sports_edge_tennis.data.agent3_history import (
    historical_match_is_eligible_for_update,
    load_agent3_history,
)


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def main() -> int:
    dsn = _dsn()
    if not dsn:
        print("A3_HISTORY_RECENCY_BLOCKED no_database_dsn", flush=True)
        return 2

    cutoff = datetime.now(timezone.utc)
    with psycopg.connect(dsn, autocommit=True) as conn:
        history = load_agent3_history(
            conn,
            cutoff=cutoff,
            tour_scopes=("ATP_MAIN", "CHALLENGER"),
        )

    eligible = [row for row in history if historical_match_is_eligible_for_update(row)]
    if not eligible:
        print("A3_HISTORY_RECENCY_BLOCKED no_eligible_history", flush=True)
        return 2

    newest_visibility = max(row.visibility_cohort for row in eligible)
    newest_match_start = max(
        (row.match_start_at for row in eligible if row.match_start_at is not None),
        default=None,
    )
    windows = {}
    for days in (30, 90, 180, 365, 730, 1460):
        boundary = cutoff - timedelta(days=days)
        windows[days] = sum(row.visibility_cohort >= boundary for row in eligible)

    start_years = Counter(
        row.match_start_at.year for row in eligible if row.match_start_at is not None
    )
    recent_years = " ".join(
        f"y{year}={start_years.get(year, 0)}"
        for year in range(cutoff.year - 4, cutoff.year + 1)
    )

    print(
        "A3_HISTORY_RECENCY "
        f"cutoff={cutoff.isoformat()} eligible={len(eligible)} "
        f"newest_visibility={newest_visibility.isoformat()} "
        f"newest_match_start={None if newest_match_start is None else newest_match_start.isoformat()} "
        + " ".join(f"last{days}d={windows[days]}" for days in (30, 90, 180, 365, 730, 1460)),
        flush=True,
    )
    print(f"A3_HISTORY_RECENCY_YEARS {recent_years}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
