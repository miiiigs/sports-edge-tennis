from __future__ import annotations

"""Sanitized Sportradar live probe for the failover window.

Uses the same credentials and adapter as production to fetch the current
three-day slate. Never prints the API key or the credential-bearing URL.
"""

import json
import os
from datetime import date

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.live.sportradar_fixtures import (
    SportradarFixtureError,
    parse_summary,
)
from sports_edge_tennis.live.target_fixtures import fetch_sportradar_fixture_rows

DATES = (date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25))


def main() -> int:
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    if not api_key:
        print("SPORTRADAR_PROBE_BLOCKED missing_key")
        return 2

    report = {"requests": 0, "dates": []}
    for target in DATES:
        entry: dict[str, object] = {"date": target.isoformat()}
        try:
            received_at, summaries = fetch_sportradar_fixture_rows(api_key, target)
        except TransportError as exc:
            entry["transport_error"] = exc.kind
            entry["transport_message"] = str(exc)[:200]
            report["dates"].append(entry)
            continue
        except Exception as exc:  # defensive, sanitized
            entry["transport_error"] = type(exc).__name__
            report["dates"].append(entry)
            continue
        report["requests"] += 1
        entry["received_at"] = received_at.isoformat()
        entry["summaries"] = len(summaries)
        parsed = 0
        rejected = 0
        singles = 0
        sample = []
        for summary in summaries:
            try:
                fixture = parse_summary(summary, received_at=received_at)
            except SportradarFixtureError as exc:
                rejected += 1
                continue
            parsed += 1
            if fixture.tour_scope in ("ATP_MAIN", "CHALLENGER"):
                singles += 1
            if len(sample) < 40:
                sample.append(
                    {
                        "event": fixture.source_match_id,
                        "status": fixture.match_status,
                        "start": fixture.match_start_at.isoformat(),
                        "p1": fixture.player_1_source_id,
                        "p2": fixture.player_2_source_id,
                        "tour": fixture.tour_scope,
                        "tier": fixture.competition_tier,
                        "surface": fixture.surface,
                        "best_of": fixture.best_of_sets,
                        "qualifying": fixture.qualifying_flag,
                        "competition": fixture.competition_source_id,
                    }
                )
        entry["parsed"] = parsed
        entry["rejected"] = rejected
        entry["in_scope_singles"] = singles
        entry["sample"] = sample
        report["dates"].append(entry)

    print("SPORTRADAR_PROBE " + json.dumps(report, sort_keys=True)[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
