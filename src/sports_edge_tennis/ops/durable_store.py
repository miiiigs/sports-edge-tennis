"""Append-only SQLite receipt store with online backup and restore rehearsal.

This is a local durable-storage primitive for the external monitor, not a
production WORM store. It proves the operational properties the owner must
approve: append-only receipts, a consistent online backup, an inventory
manifest that survives restoration, and a fail-closed restoration check.

Never copy a live WAL database with a filesystem copy; use ``backup_store``.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY,
    slot TEXT NOT NULL,
    payload BLOB NOT NULL,
    sha256 TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS receipts_no_update
BEFORE UPDATE ON receipts
BEGIN SELECT RAISE(ABORT, 'append-only receipt'); END;
CREATE TRIGGER IF NOT EXISTS receipts_no_delete
BEFORE DELETE ON receipts
BEGIN SELECT RAISE(ABORT, 'append-only receipt'); END;
"""


def create_store(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target, timeout=5)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript(SCHEMA)
        conn.commit()
    return target


def append_receipt(
    path: str | Path,
    *,
    receipt_id: str,
    slot: str,
    payload: bytes,
    received_at: str,
) -> str:
    if not receipt_id or not slot or not received_at:
        raise ValueError("receipt identity, slot and received_at are required")
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("receipt payload bytes are required")
    digest = hashlib.sha256(payload).hexdigest()
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(
            "INSERT INTO receipts VALUES (?,?,?,?,?)",
            (receipt_id, slot, payload, digest, received_at),
        )
        conn.commit()
    return digest


def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_absolute() or not path.is_file():
        raise ValueError("existing absolute store path required")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)


def verify_store(path: str | Path) -> dict[str, object]:
    target = Path(path)
    with closing(_open_readonly(target)) as conn, conn:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("store integrity check failed")
        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='receipts'"
            )
        }
        if not {"receipts_no_update", "receipts_no_delete"} <= triggers:
            raise ValueError("append-only triggers missing")
        inventory = hashlib.sha256()
        count = 0
        for receipt_id, slot, payload, digest, received_at in conn.execute(
            "SELECT receipt_id,slot,payload,sha256,received_at "
            "FROM receipts ORDER BY receipt_id"
        ):
            if hashlib.sha256(bytes(payload)).hexdigest() != digest:
                raise ValueError("receipt digest mismatch")
            entry = json.dumps(
                [receipt_id, slot, digest, received_at],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
            inventory.update(entry + b"\n")
            count += 1
    return {
        "schema": "monitoring_receipt_store_manifest_v1",
        "count": count,
        "inventory_sha256": inventory.hexdigest(),
    }


def backup_store(
    source: str | Path,
    destination: str | Path,
    *,
    expected_manifest: dict[str, object] | None = None,
) -> dict[str, object]:
    """Create a consistent snapshot at a new absolute destination.

    On any failure only the newly created destination is removed. An existing
    destination is never overwritten.
    """
    source, destination = Path(source), Path(destination)
    if not destination.is_absolute():
        raise ValueError("absolute backup destination required")
    with closing(_open_readonly(source)) as src:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(destination, timeout=5)) as dst:
                dst.execute("PRAGMA synchronous=FULL")
                src.backup(dst)
                dst.execute("PRAGMA journal_mode=DELETE")
            manifest = verify_store(destination)
            if expected_manifest is not None and manifest != expected_manifest:
                raise ValueError("backup manifest mismatch")
            with destination.open("rb+") as handle:
                os.fsync(handle.fileno())
            return manifest
        except BaseException:
            destination.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(destination) + suffix).unlink(missing_ok=True)
            raise


def restore_store(
    backup: str | Path,
    destination: str | Path,
    *,
    expected_manifest: dict[str, object],
) -> dict[str, object]:
    if Path(destination).exists():
        raise ValueError("restore destination must not exist")
    manifest = backup_store(backup, destination, expected_manifest=expected_manifest)
    return manifest
