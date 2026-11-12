"""One bounded, manual, provider-backed dry-run for a >3-day Tennis fixture.

Example (trusted Railway terminal with readonly TENNIS_DATABASE_URL):
  PYTHONPATH=src:. python ops/brian_aged_provider_probe.py \
    --utc-match-date 2026-11-08 --max-targets 10

This NEVER persists fixtures or results, never infers winner, and never
modifies the production 3-day recovery writer. Operator must log API quota
and not run it repeatedly during the same UTC date. Automated invocation
requires a persisted daily lease to guarantee a hard one-request/day cap.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

from sports_edge_tennis.live.credentials import MissingCredentialError, load_secret
from sports_edge_tennis.live.pending_result_refresh import (
    refresh_pending_results,
)


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--utc-match-date",type=date.fromisoformat,required=True)
    p.add_argument("--max-targets",type=int,default=10)
    p.add_argument("--out",type=Path)
    args=p.parse_args(argv)
    if not 1<=args.max_targets<=10:
        p.error("historical dry-run probe maximum is ten targets")
    if args.out is not None and args.out.exists():
        p.error("will not overwrite prior provider evidence")
    dsn=os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        p.error("trusted read-only Railway database credential unavailable")
    try:
        api_key=load_secret("API_TENNIS_API_KEY")
    except MissingCredentialError:
        p.error("API-Tennis access is unavailable; OddsPapi is not required")
    now=datetime.now(UTC)
    # Explicitly fail if older date is not between 3 and 90 UTC days ago.
    age=(now.date()-args.utc_match_date).days
    if not 3<=age<=90:
        p.error("select an older observed fixture date aged 3-90 days")
    import psycopg
    with psycopg.connect(
        dsn, options="-c default_transaction_read_only=on"
    ) as conn:
        summary=refresh_pending_results(
            conn,api_key=api_key,as_of=now,
            max_targets=args.max_targets,dry_run=True,
            explicit_window=(args.utc_match_date,args.utc_match_date),
        )
    result={
        "run_at_utc":now.isoformat(),
        "match_date_utc":args.utc_match_date.isoformat(),
        "dry_run":True,
        "database_writes":False,
        "api_request_ceiling":1,
        "provider":"API_TENNIS",
        "checked":summary.checked,
        "provider_requests":summary.provider_requests,
        "provider_errors":summary.provider_errors,
        "terminal_found":summary.terminal_found,
        "same_match":summary.same_match,
        "reused":summary.reused,
        "unresolved":summary.unresolved,
        "unsafe_identity":summary.unsafe,
        "would_won":summary.expected_won,
        "would_lost":summary.expected_lost,
        "would_void":summary.expected_void,
        "persisted":summary.persisted,
        "status":(
            "REQUIRES_PROVIDER_OR_MAPPING_REVIEW"
            if summary.provider_errors or summary.unsafe
            else "READ_ONLY_PROVIDER_CHECK_COMPLETED"
        ),
    }
    if summary.persisted != 0 or summary.provider_requests>1:
        raise ValueError("strict read-only or API budget violated")
    line=json.dumps(result,sort_keys=True)
    if args.out is not None:
        fd=os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            stream.write(line+"\n")
    print(line,flush=True)
    return 0 if not summary.provider_errors else 2


if __name__=="__main__":
    raise SystemExit(main())
