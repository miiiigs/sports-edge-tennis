"""Operator-only extension of the central A1 source census; private aggregate output."""
import argparse
import csv
import json
import os
from pathlib import Path
import psycopg
from sports_edge_tennis.validation.a1_selected_source_provenance import audit_selected_history_sources
from sports_edge_tennis.validation.a1_commercial_source_summary import (
    E3_CUTOFF, LEDGER_COUNT, LEDGER_SHA256, verify_frozen_ledger, summarize, reference_inventory,
)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--authorized-read-only", action="store_true")
    a = p.parse_args()
    if not a.authorized_read_only or a.output.exists():
        p.error("explicit authorization and new output path required")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        p.error("authorized DATABASE_URL required")
    raw = a.ledger.read_bytes()
    verify_frozen_ledger(raw)
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    if len(rows) != LEDGER_COUNT:
        raise ValueError("ledger count mismatch")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '120s'")
        conn.execute("SET LOCAL lock_timeout = '5s'")
        report = audit_selected_history_sources(conn, ledger_rows=rows, cutoff=E3_CUTOFF)
        aggregate = summarize(report)
        aggregate.update(reference_inventory(conn, report, E3_CUTOFF))
    aggregate.update(ledger_sha256=LEDGER_SHA256, cutoff=E3_CUTOFF.isoformat(),
                     cutoff_kind="E3 reconstruction, not exact later ledger extraction")
    fd = os.open(a.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(aggregate, f, indent=2, sort_keys=True)
        f.write("\n")

if __name__ == "__main__":
    main()
