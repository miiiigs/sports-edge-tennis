"""Explicitly authorized, read-only A1 history supplier census."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path

import psycopg

from sports_edge_tennis.validation.a1_selected_source_provenance import (
    audit_selected_history_sources,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--cutoff", required=True, help="ISO timestamp with timezone")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--authorized-read-only", action="store_true")
    args = parser.parse_args()
    if not args.authorized_read_only:
        parser.error("explicit authorization for the source census is required")
    if args.output.exists():
        parser.error("refusing to overwrite existing evidence")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("DATABASE_URL must be supplied by the authorized environment")
    raw = args.ledger.read_bytes()
    ledger = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '120s'")
            report = audit_selected_history_sources(
                conn, ledger_rows=ledger, cutoff=args.cutoff)
    report["ledger_sha256"] = hashlib.sha256(raw).hexdigest()
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
