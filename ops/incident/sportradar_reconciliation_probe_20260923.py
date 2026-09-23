from __future__ import annotations

"""Read-only Sportradar identity/canonical reconciliation probe.

Fetches the current three-day slate and classifies every parsed fixture against
the canonical database using the same overlap resolver as production. Writes
nothing.
"""

import json
import os
from collections import Counter
from datetime import date

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar_fixtures import (
    SportradarFixtureError,
    parse_summary,
)
from sports_edge_tennis.live.sportradar_reconciliation import (
    resolve_sportradar_event_overlap,
)
from sports_edge_tennis.live.target_fixtures import fetch_sportradar_fixture_rows

DATES = (date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25))


def main() -> int:
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not api_key or not dsn:
        print("RECON_PROBE_BLOCKED missing_key_or_dsn")
        return 2

    fetched: list = []
    for target in DATES:
        received_at, summaries = fetch_sportradar_fixture_rows(api_key, target)
        for summary in summaries:
            try:
                fetched.append(parse_summary(summary, received_at=received_at))
            except SportradarFixtureError:
                continue

    counts: Counter[str] = Counter()
    examples: dict[str, list] = {}
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        if str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower() != "on":
            raise RuntimeError("transaction_read_only is not on")
        for fixture in fetched:
            try:
                resolution = resolve_sportradar_event_overlap(conn, fixture=fixture)
            except Exception as exc:  # defensive
                counts["RESOLVER_ERROR"] += 1
                examples.setdefault("RESOLVER_ERROR", []).append(
                    {"event": fixture.source_match_id, "error": type(exc).__name__}
                )
                continue
            counts[resolution.status] += 1
            if resolution.status not in ("SOURCE_ID_RESOLVED", "EXACT_EXISTING_MATCH"):
                bucket = examples.setdefault(resolution.status, [])
                if len(bucket) < 8:
                    bucket.append(
                        {
                            "event": fixture.source_match_id,
                            "start": fixture.match_start_at.isoformat(),
                            "status": fixture.match_status,
                            "tour": fixture.tour_scope,
                            "p1": fixture.player_1_source_id,
                            "p2": fixture.player_2_source_id,
                            "p1_resolution": resolution.player_1.status,
                            "p2_resolution": resolution.player_2.status,
                        }
                    )
        conn.rollback()

    print(
        "SPORTRADAR_RECONCILIATION "
        + json.dumps(
            {
                "fixtures_parsed": len(fetched),
                "counts": dict(sorted(counts.items())),
                "examples": examples,
                "transaction_read_only": "on",
                "writes": 0,
            },
            sort_keys=True,
        )[:12000]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
