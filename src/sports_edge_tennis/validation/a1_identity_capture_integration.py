"""Integrate a first-issued identity-bridge shadow capture into migration 0025.

The forward shadow writes an append-only JSONL archive. This operator command verifies
that archive and, with ``--apply``, writes the same facts to
``identity_resolution_provenance``. It never touches predictions, decisions, Manager
results, odds, publication or settlement rows.

Default is a dry run. ``--apply`` requires an explicitly supplied database DSN through
the usual environment variables. The command is idempotent: replaying the same archive
reports existing rows instead of duplicating them, and a conflicting digest fails
closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Any

import psycopg

from sports_edge_tennis.data.identity_runtime import (
    CAPTURE_SOURCE_SHADOW,
    IdentityProvenanceError,
    observations_from_forward_capture,
    persist_identity_resolutions,
    read_capture_archive,
)

VERSION = "A1_IDENTITY_CAPTURE_INTEGRATION_V1"


def dsn_from_env() -> str:
    direct = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if direct:
        return direct
    if os.environ.get("PGHOST") and os.environ.get("PGPASSWORD"):
        return psycopg.conninfo.make_conninfo(
            host=os.environ["PGHOST"],
            port=os.environ.get("PGPORT", "5432"),
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
            dbname=os.environ["PGDATABASE"],
            sslmode="require",
            connect_timeout=30,
        )
    raise SystemExit("TENNIS_DATABASE_URL, DATABASE_URL or PG* variables are required")


def _record_set_sha256(records: list[dict[str, Any]]) -> str:
    hashes = [str(record["record_sha256"]) for record in sorted(
        records,
        key=lambda r: (int(r["prediction_run_id"]), int(r["match_id"]), int(r["prediction_id"])),
    )]
    return hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()


def integrate(
    conn: psycopg.Connection,
    *,
    archive_path: str,
    apply: bool,
) -> dict[str, Any]:
    records = read_capture_archive(archive_path)
    observations = observations_from_forward_capture(
        records, capture_source=CAPTURE_SOURCE_SHADOW
    )
    summary: dict[str, Any] = {
        "version": VERSION,
        "archive_path": archive_path,
        "capture_records": len(records),
        "observations": len(observations),
        "record_set_sha256": _record_set_sha256(records),
        "apply": apply,
        "modified_production": False,
        "outcome_included": False,
        "can_promote": False,
    }
    if not apply:
        summary["persistence"] = {
            "inserted": 0,
            "existing": 0,
            "total": 0,
            "applied": False,
        }
        return summary
    try:
        persistence = persist_identity_resolutions(conn, observations)
    except IdentityProvenanceError as exc:
        raise SystemExit(f"identity capture integration failed closed: {exc}") from exc
    summary["persistence"] = {**persistence, "applied": True}
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-jsonl", required=True, help="capture archive path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write provenance rows (default is a dry run)",
    )
    args = parser.parse_args(argv)

    if not args.apply:
        summary = integrate(None, archive_path=args.archive_jsonl, apply=False)  # type: ignore[arg-type]
        print(json.dumps(summary, indent=2))
        return 0

    with psycopg.connect(dsn_from_env(), autocommit=True, connect_timeout=30) as conn:
        summary = integrate(conn, archive_path=args.archive_jsonl, apply=True)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
