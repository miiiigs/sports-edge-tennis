"""Read-only CLI for pre-registered forward Model Picks filters.

Usage (operator terminal, existing TENNIS_DATABASE_URL):
  python -m sports_edge_tennis.performance.forward_filter_cli

No calls to any provider, production writes, new services, or credential output.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import psycopg

from sports_edge_tennis.performance.calibration_monitor import fetch_observations
from sports_edge_tennis.performance.forward_filter_research import (
    build_forward_filter_report,
)


def main() -> int:
    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        print("FORWARD_FILTER_BLOCKED reason=NO_DATABASE")
        return 2
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.read_only = True
        observations = fetch_observations(conn)
    report_date = datetime.now(timezone.utc).date()
    report = build_forward_filter_report(observations, report_date_utc=report_date)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
