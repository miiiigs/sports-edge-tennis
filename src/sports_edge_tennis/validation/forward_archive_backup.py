"""Local SQLite online backup and restoration for the original collector.

No external archive provisioning, encryption or independent control is implied.
The returned manifest must be retained separately by an authorized operator.
Never copy a live WAL database using filesystem copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from sports_edge_tennis.validation.forward_original_collector import _decode, _sha
from sports_edge_tennis.validation.forward_receipt_store import _instant


def _readonly(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("existing absolute archive path required")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)


def _inventory(db):
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("archive integrity check failed")
    triggers = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='original_receipts'"
    )}
    if not {"original_receipts_no_update", "original_receipts_no_delete"} <= triggers:
        raise ValueError("archive append-only triggers missing")
    sha = hashlib.sha256()
    count = 0
    for version, match, run, payload, digest, received, timing in db.execute(
        """SELECT experiment_version,match_id,run_id,payload,sha256,received_at,timing
           FROM original_receipts ORDER BY experiment_version,match_id"""
    ):
        v, m, r, issued, deadline = _decode(payload)
        received_at = _instant(received)
        if ((v, m, r) != (version, match, run) or _sha(payload) != digest
                or received_at < issued
                or timing != ("TIMELY_LOCAL_CLOCK_ONLY" if received_at <= deadline else "LATE_RECEIPT")):
            raise ValueError("archive record lineage or digest mismatch")
        # The payload digest binds exact bytes; the metadata binds the original
        # receipt clock and identifiers, which must survive restoration.
        entry = json.dumps([version, match, run, digest, received, timing],
                           ensure_ascii=True, separators=(",", ":")).encode()
        sha.update(entry + b"\n")
        count += 1
    return {"schema": "original_archive_backup_manifest_v1",
            "count": count, "inventory_sha256": sha.hexdigest(),
            "independent_clock_verified": False}


def verify_backup(path):
    with closing(_readonly(path)) as db, db:
        db.execute("BEGIN")
        return _inventory(db)


def backup_archive(source, destination, *, expected_manifest=None):
    """Create a consistent snapshot at a *new* absolute destination.

    For restore, supply the separately preserved backup manifest. On any
    failure remove only the newly created destination; never overwrite source
    or an existing destination. Run under an operator-controlled directory.
    """
    source, destination = Path(source), Path(destination)
    if not destination.is_absolute():
        raise ValueError("absolute backup destination required")
    with closing(_readonly(source)) as src:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(destination, timeout=5)) as dst:
                dst.execute("PRAGMA synchronous=FULL")
                src.backup(dst)
                # Leave a self-contained database, not a live WAL pair.
                dst.execute("PRAGMA journal_mode=DELETE")
            manifest = verify_backup(destination)
            if expected_manifest is not None and manifest != expected_manifest:
                raise ValueError("backup manifest mismatch")
            with destination.open("rb") as file:
                os.fsync(file.fileno())
            return manifest
        except BaseException:
            destination.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(destination) + suffix).unlink(missing_ok=True)
            raise
