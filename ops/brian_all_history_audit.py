"""One-command immutable ALL-DAYS Model Picks W/L/VOID/PENDING audit.

Unlike the historical 52-to-43 or Nov9 one-off tools, this examines
every first-issued Original aligned Model Pick, every old pending,
and every reporting day, with no selection-date or lookback truncation.

Run only inside an authorized read-only Railway PostgreSQL environment:
  PYTHONPATH=src:. python ops/brian_all_history_audit.py \
    --output /secure/sports-edge/all_history_20261112T1600Z.json

Produces a private JSON with all IDs, player identities, source timestamps,
per-day outcome totals and per-pending reason/next action. Fails visibly
on any disagreement with the previous daily report. NEVER voids a
match merely because it is old; no writes, no provider calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from sports_edge_tennis.dashboard.lifetime_reconciliation import (
    compare_all_history_with_existing_daily,
    load_lifetime_reconciliation,
    pending_dashboard_audit,
)
from sports_edge_tennis.dashboard.qualified_history import (
    load_qualified_history,
)


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output.exists():
        parser.error("refusing to overwrite prior immutable audit")
    dsn=os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        parser.error("trusted readonly Railway PostgreSQL URL required")
    import psycopg
    from zoneinfo import ZoneInfo
    with psycopg.connect(
        dsn, options="-c default_transaction_read_only=on"
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        as_of=conn.execute("SELECT transaction_timestamp()").fetchone()[0]
        report=load_lifetime_reconciliation(conn,as_of=as_of)
        earlier=load_qualified_history(
            conn,as_of_date=as_of.astimezone(ZoneInfo("Asia/Manila")).date(),
        )
        report=compare_all_history_with_existing_daily(
            report,earlier,as_of=as_of,
        )
        pending=pending_dashboard_audit(report)
    if report["original"]["selections"]!=len(report["all_match_records"]):
        raise ValueError("missing original match records in lifetime export")
    if report["original"]["pending"]!=len(report["unresolved_matches"]):
        raise ValueError("missing unresolved cases in lifetime export")
    report["pending_dashboard_view"]=pending
    report["audit_export_status"]=(
        "COMPLETED_WITH_DAY_MISMATCH_NEEDS_REVIEW"
        if report["legacy_daily_reconciliation"]["discrepancies"]
        else "READ_ONLY_RECONCILIATION_SUCCEEDED_PROVIDER_VALIDATION_PENDING"
    )
    payload=json.dumps(
        report,indent=2,sort_keys=True,ensure_ascii=False
    )+"\n"
    sha=hashlib.sha256(payload.encode("utf-8")).hexdigest()
    fd=os.open(
        args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600
    )
    with os.fdopen(fd,"w",encoding="utf-8") as dest:
        dest.write(payload)
    print(
        f"ALL_HISTORY_AUDIT={args.output} SHA256={sha} "
        f"FROM={report['starts_at']} TO={report['ends_at']} "
        f"SELECTIONS={report['original']['selections']} "
        f"WON={report['original']['won']} LOST={report['original']['lost']} "
        f"VOID={report['original']['void']} "
        f"PENDING={report['original']['pending']} "
        f"OVERDUE_REQUIRES_ACTION={report['pending_needing_operator_attention']} "
        f"DAILY_MISMATCHES={len(report['legacy_daily_reconciliation']['discrepancies'])}"
    )
    return 2 if report["legacy_daily_reconciliation"]["discrepancies"] else 0


if __name__=="__main__":
    raise SystemExit(main())
