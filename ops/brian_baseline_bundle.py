"""Single-transaction, private Sports Edge baseline acceptance bundle.

Emits three standardized reporting periods, all Nov9 original/qualified
match IDs, and every pending case in one READ ONLY REPEATABLE READ
PostgreSQL snapshot. No provider calls and no writes or retrograde edits.
Do NOT upload the full bundle to a public repo or WhatsApp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_edge_tennis.dashboard.model_pick_history import (
    load_daily_model_pick_summaries,
)
from sports_edge_tennis.dashboard.qualified_history import (
    load_qualified_history,
)
from sports_edge_tennis.dashboard.three_window_evidence import report_windows
from sports_edge_tennis.live.pending_result_diagnostics import (
    load_pending_result_diagnostics,
)
from ops.brian_pending_inventory import detailed_inventory
from ops.brian_nov09_selection_audit import parse_rows, query as nov09_query

MANILA = ZoneInfo("Asia/Manila")


def assemble_baseline(
    *,
    ledger: dict,
    nov9_matches: dict[int, dict],
    pending: dict,
    last_result_ingestion,
    last_fixture_observation,
) -> dict:
    fixed = ledger["periods"]["fixed_nov05_nov09"]
    day = next(
        (entry for entry in fixed["days"] if entry["day"]=="2026-11-09"),
        None,
    )
    if day is None:
        raise ValueError("Nov9 not present in fixed Nov5-9 report")

    ids = set(nov9_matches)
    filtered = {
        mid:r for mid,r in nov9_matches.items() if r["qualified_ge70"]
    }
    if len(ids) != day["original"]["picks"]:
        raise ValueError(
            f"Nov9 original first-issued row count {len(ids)} != "
            f"daily ledger {day['original']['picks']}"
        )
    if len(filtered) != day["qualified"]["picks"]:
        raise ValueError(
            f"Nov9 >=70 original first-issued row count {len(filtered)} != "
            f"daily ledger {day['qualified']['picks']}"
        )
    for k in ("won","lost","void","pending"):
        matched = sum(
            r["status"] == k.upper() for r in nov9_matches.values()
        )
        if matched != day["original"][k]:
            raise ValueError(
                f"Nov9 original {k} ledger {day['original'][k]} != "
                f"match-level {matched}"
            )
        matched_filter = sum(r["status"]==k.upper() for r in filtered.values())
        if matched_filter != day["qualified"][k]:
            raise ValueError(
                f"Nov9 filtered {k} ledger {day['qualified'][k]} != "
                f"match-level {matched_filter}"
            )
    if pending.get("total") != len(pending.get("items",[])):
        raise ValueError("pending summary does not reconcile to case inventory")

    not_pit_verified = [
        mid for mid,r in nov9_matches.items()
        if r["issued_before_kickoff"] is not True
    ]
    output = {
        "status": (
            "PIT_REVIEW_REQUIRED" if not_pit_verified
            else "PENDING_PROVIDER_VERIFICATION_REQUIRED"
        ),
        "baseline_contract": "SPORTS_EDGE_TENNIS_V1_20261112",
        "dataset_as_of": ledger["data_refreshed_at"],
        "timezone": "Asia/Manila",
        "source": "RAILWAY_POSTGRES_SINGLE_REPEATABLE_READ_TRANSACTION",
        "last_result_ingested_at": (
            last_result_ingestion.isoformat() if last_result_ingestion else None
        ),
        "last_fixture_source_observed_at": (
            last_fixture_observation.isoformat()
            if last_fixture_observation else None
        ),
        "reports": ledger,
        "nov09": {
            "original_count": len(ids),
            "qualified_count": len(filtered),
            "unverified_pit_ids": not_pit_verified,
            "all_original_match_records": [
                nov9_matches[mid] for mid in sorted(ids)
            ],
        },
        "pending_inventory": pending,
        "externally_verified": False,
        "tests_executed": False,
        "dashboard_parity_verified": False,
        "a3_a4_independent_gain": None,
        "next_acceptance": (
            "Verify provider-final states; independently compare every "
            "match ID/W-L-VOID-PENDING and the protected live dashboard; "
            "execute CI and authorized provider retries."
        ),
    }
    return output


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--lookback-days", type=int, default=90)
    args = p.parse_args(argv)
    if args.out.exists():
        p.error("output already exists; never overwrite baseline evidence")
    if not 1 <= args.lookback_days <= 90:
        p.error("lookback must be between 1 and 90 days")
    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        p.error("trusted, authorized READ ONLY Railway Postgres URL required")

    import psycopg
    with psycopg.connect(
        dsn, options="-c default_transaction_read_only=on"
    ) as conn:
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        )
        stamp = conn.execute("SELECT transaction_timestamp()").fetchone()[0]
        as_of_day = stamp.astimezone(MANILA).date()
        shadow = load_qualified_history(conn, as_of_date=as_of_day)
        manager = load_daily_model_pick_summaries(
            conn, timezone_name="Asia/Manila"
        )
        ledger = report_windows(
            list(shadow), list(manager),
            as_of_date=as_of_day, source_refresh_at=stamp,
        )
        raw_nov9 = conn.execute(
            nov09_query(),
            {"as_of": stamp, "report_day": __import__("datetime").date(2026,11,9)},
        ).fetchall()
        nov9 = parse_rows(raw_nov9, stamp)
        case_report = load_pending_result_diagnostics(
            conn, now=stamp, lookback_days=args.lookback_days,
            limit=2000,
        )
        pending = detailed_inventory(case_report)
        last_result = conn.execute(
            "SELECT MAX(ingested_at) FROM match_result_observations"
        ).fetchone()[0]
        last_fixture = conn.execute(
            "SELECT MAX(source_observed_at) FROM match_versions"
        ).fetchone()[0]
    evidence = assemble_baseline(
        ledger=ledger, nov9_matches=nov9, pending=pending,
        last_result_ingestion=last_result,
        last_fixture_observation=last_fixture,
    )
    body = json.dumps(
        evidence, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    fd = os.open(args.out, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        f.write(body)
    print(
        "BASELINE_SNAPSHOT="+str(args.out)+
        " AS_OF="+evidence["dataset_as_of"]+
        " SHA256="+digest+
        " NOV09_ORIGINAL="+str(evidence["nov09"]["original_count"])+
        " NOV09_70="+str(evidence["nov09"]["qualified_count"])+
        " PENDING="+str(pending["total"])+
        " STATUS="+evidence["status"]
    )
    return 0


if __name__=="__main__":
    raise SystemExit(main())
