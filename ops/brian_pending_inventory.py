"""Private, read-only exhaustive pending case inventory, including names and ages.

Uses the SAME canonical diagnostic query as the live runner, not a
separate event-mapping algorithm. Produces all outstanding match IDs,
recorded fixture statuses, first issue times, PIT and current starts,
provider observation times, age, exact reason and next operator action.

Stored fixture observation != independent fresh provider confirmation.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sports_edge_tennis.live.pending_result_diagnostics import (
    load_pending_result_diagnostics, report_json,
)

ACTIONS = {
    "NOT_STARTED": "VERIFY_UPCOMING_STATUS_WITH_PROVIDER; RECHECK_AT_OR_AFTER_KICKOFF",
    "AWAITING_RESULT": "RETRY_TERMINAL_FEED_WITH_CANONICAL_ID; ESCALATE_IF_24H_OVERDUE",
    "TERMINAL_FIXTURE_WITHOUT_RESULT": "OBTAIN_AUTHORITATIVE_WINNER_OR_VOID; DO_NOT_INFER",
    "PROVIDER_FIXTURE_NOT_REOBSERVED": "FETCH_ALTERNATE_OFFICIAL_HISTORY; VERIFY_EVENT_ID",
    "NO_API_TENNIS_LINK": "RECONCILE_CANONICAL_EVENT_ID_BEFORE_RETRY",
    "FIXTURE_POSTPONED": "VERIFY_NEW_SCHEDULE; NEVER_SETTLE_BEFORE_AUTHORITY",
    "RESULT_DELAYED": "RECHECK_SOURCE_INGESTION_AND_SETTLEMENT_LOGS",
}


def detailed_inventory(report) -> dict:
    export = report_json(report)
    for case in export["items"]:
        case["recommended_action"] = ACTIONS.get(
            case["reason"], "MANUAL_REVIEW_REQUIRED"
        )
        case["provider_independently_rechecked_now"] = False
        case["classification_basis"] = "STORED_FIXTURE_VERSIONS_ONLY"
        started = case.get("hours_since_start")
        case["overdue_alert"] = (
            "OVER_72_HOURS" if started is not None and started >= 72
            else "OVER_24_HOURS" if started is not None and started >= 24
            else "NOT_OVERDUE_OR_FUTURE"
        )
        obs = case.get("hours_since_last_observed")
        case["provider_observation_stale_24h"] = (
            obs is not None and obs >= 24
        )
    export["unverified_future_count"] = sum(
        x["reason"] == "NOT_STARTED" for x in export["items"]
    )
    export["older_than_24h"] = sum(
        x["overdue_alert"] in ("OVER_24_HOURS", "OVER_72_HOURS")
        for x in export["items"]
    )
    export["older_than_72h"] = sum(
        x["overdue_alert"] == "OVER_72_HOURS" for x in export["items"]
    )
    export["status"] = "STORED_EVIDENCE_NOT_PROVIDER_LIVE_RECONFIRMED"
    if export["total"] != len(export["items"]):
        raise ValueError("aggregate pending total differs from exported item count")
    return export


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lookback-days", type=int, default=90)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--expected-total", type=int)
    args = p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite a previous audit")
    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        p.error("Railway authenticated read-only TENNIS_DATABASE_URL required")
    import psycopg
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as conn:
        stamp = conn.execute("SELECT transaction_timestamp()").fetchone()[0]
        evidence = load_pending_result_diagnostics(
            conn, now=stamp, lookback_days=args.lookback_days, limit=2000
        )
    result = detailed_inventory(evidence)
    result["database_snapshot_time"] = stamp.isoformat()
    if args.expected_total is not None and result["total"] != args.expected_total:
        raise ValueError(
            f"changed pending population: {result['total']} vs {args.expected_total}"
        )
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(f"PENDING_CASES={result['total']} SAVED={args.out} AS_OF={stamp.isoformat()}")


if __name__ == "__main__":
    main()
