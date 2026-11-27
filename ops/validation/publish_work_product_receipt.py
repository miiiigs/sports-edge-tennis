"""Publish a database-verified work-product receipt for a research rehearsal.

Read-only against PostgreSQL. The only write is the durable receipt file, which
the independent monitor reads. Exit 0 only when every required stage is verified
with matching persisted rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import psycopg

from sports_edge_tennis.ops.monitoring import REQUIRED_STAGES, evaluate_run
from sports_edge_tennis.ops.work_product_receipts import (
    ReceiptScope,
    WorkProductReceiptPublisher,
)


def _instant(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"timezone-aware timestamp required: {value!r}")
    return moment


def _rules(values: list[str]) -> dict[tuple[str, str], str]:
    rules: dict[tuple[str, str], str] = {}
    for value in values:
        parts = value.split(":", 2)
        if len(parts) != 3 or not all(part.strip() for part in parts):
            raise ValueError("--bookmaker-rule must be bookmaker:rule:reference")
        rules[(parts[0], parts[1])] = parts[2]
    return rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slot", required=True)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--prediction-run-id", type=int)
    parser.add_argument("--experiment-id")
    parser.add_argument("--include-qualifying", action="store_true")
    parser.add_argument("--cutoff")
    parser.add_argument(
        "--as-of",
        help="publisher clock for a historical rehearsal; defaults to now",
    )
    parser.add_argument("--receipt-path", required=True)
    parser.add_argument("--archive-manifest")
    parser.add_argument("--candidate-ledger")
    parser.add_argument(
        "--bookmaker-rule",
        action="append",
        default=[],
        help="bookmaker:rule:reference; only explicitly approved rules",
    )
    parser.add_argument("--no-archive", action="store_true")
    parser.add_argument("--no-prospective-candidate", action="store_true")
    parser.add_argument(
        "--acknowledge-empty-discovery",
        action="store_true",
        help="explicitly acknowledge a verified zero-fixture discovery window",
    )
    parser.add_argument("--out-json")
    args = parser.parse_args(argv)

    scope = ReceiptScope(
        slot=_instant(args.slot),
        window_start=_instant(args.window_start),
        window_end=_instant(args.window_end),
        prediction_run_id=args.prediction_run_id,
        experiment_id=args.experiment_id,
        include_qualifying=args.include_qualifying,
        cutoff=_instant(args.cutoff) if args.cutoff else None,
        require_archive=not args.no_archive,
        require_prospective_candidate=not args.no_prospective_candidate,
        approved_bookmaker_rules=_rules(args.bookmaker_rule),
        archive_manifest_path=args.archive_manifest,
        candidate_ledger_path=args.candidate_ledger,
        acknowledge_empty_discovery=args.acknowledge_empty_discovery,
    )
    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL or DATABASE_URL required")

    with psycopg.connect(dsn, autocommit=True, connect_timeout=20) as conn:
        conn.execute("SET default_transaction_read_only=on")
        conn.execute("SET statement_timeout='120s'")
        publisher = WorkProductReceiptPublisher(
            conn,
            clock=(lambda: _instant(args.as_of)) if args.as_of else None,
        )
        result = publisher.publish(scope, receipt_path=args.receipt_path)
        assessment = evaluate_run(
            result["record_object"],
            slot=scope.slot,
            now=result["record_object"].finished_at + timedelta(seconds=1),
            run_timeout=max(
                scope.window_end - scope.window_start,
                timedelta(minutes=1),
            ),
        )

    payload = {
        "mode": "RESEARCH_REHEARSAL",
        "read_only_database": True,
        "required_stages": list(REQUIRED_STAGES),
        "assessment": assessment.to_payload(),
        "receipt_path": result["receipt_path"],
        "receipt_sha256": result["receipt_sha256"],
        "success": result["success"],
        "missing_stages": list(result["missing_stages"]),
        "checks": result["checks"],
    }
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    print(text)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
