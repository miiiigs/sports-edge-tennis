"""Export only canonical ALIGNED Model Pick research fields from PostgreSQL.

Uses existing ops/calibration_dataset.sql verbatim; runs read-only. Do not
commit exported records. Never access provider APIs or write to results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from research.tennis_aligned_oct_replay_20261028.replay import parse_ledger

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SQL = REPO_ROOT / "ops" / "calibration_dataset.sql"
COLUMNS = (
    "match_id", "manila_date", "aligned_probability", "aligned_pick_type",
    "picked_at", "surface", "tier", "result_status",
)


def _serialize_value(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def sanitized_ledger(rows) -> dict:
    """Whitelisted research columns only, canonical match identity intact."""
    selected = []
    for row in rows:
        selected.append({
            key: _serialize_value(row[key]) for key in COLUMNS
        })
    # Enforce canonical single sporting-match identity and known W/L states.
    parse_ledger({"data": selected})
    raw = json.dumps(selected, sort_keys=True, separators=(",", ":"))
    return {
        "population": "ORIGINAL_ALIGNED_MODEL_PICKS",
        "source_query": "ops/calibration_dataset.sql",
        "row_count": len(selected),
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "data": selected,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Read-only Tennis aligned Model Picks export for replay")
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        print("EXPORT_BLOCKED reason=NO_AUTHORIZED_DATABASE_URL")
        return 2
    # Perform the verified canonical SQL query once; no extra provider calls.
    # Treat the database as read-only even if the supplied DSN has privileges.
    with psycopg.connect(dsn, row_factory=dict_row,
                         options="-c default_transaction_read_only=on") as conn:
        sql = SOURCE_SQL.read_text(encoding="utf-8")
        rows = conn.execute(sql).fetchall()
    ledger = sanitized_ledger(rows)
    # Output is a local private analysis input, not a production artifact.
    path = args.output
    if path.exists():
        print("EXPORT_BLOCKED reason=OUTPUT_ALREADY_EXISTS")
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(ledger, stream, indent=2)
        stream.write("\n")
    print(f"EXPORT_READY aligned_rows={ledger['row_count']} sha256={ledger['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
