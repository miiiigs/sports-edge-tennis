"""Authoritative single-owner lock for hourly production batches.

Exactly one production batch may be active per environment. Both the scheduled
cron entrypoint (``python -m sports_edge_tennis.live.batch``) and the manual
production wrapper (``python -m sports_edge_tennis.live.production_batch``) must
acquire the same PostgreSQL session-level advisory lock before any schema work,
provider request, prediction run creation, or persistence.

Advisory locks are database-scoped, so separate databases are already isolated.
``TENNIS_ENVIRONMENT`` is additionally hashed into the lock key so environments
that share one database (for example a local stack) still do not block each
other. Session-level locks are released when the owning connection closes, so a
crashed or disconnected batch cannot leave the environment permanently locked.

This module is the only ownership mechanism. Re-entrant acquisition inside the
same process (``production_batch`` calling ``live.batch``) is reference counted
so the outer owner holds the lock for the whole batch.
"""

from __future__ import annotations

import os
import socket
import threading
import zlib
from collections.abc import Callable
from dataclasses import dataclass, replace

import psycopg

# Fixed namespace ("TENN" in ASCII) keeps this lock space disjoint from the
# single-key history-refresh lock and the OddsPapi ledger lock.
PRODUCTION_BATCH_LOCK_NAMESPACE = 0x54454E4E
SKIPPED_ALREADY_RUNNING = "SKIPPED_ALREADY_RUNNING"
DEFAULT_ENVIRONMENT = "production"


def default_environment() -> str:
    value = (os.environ.get("TENNIS_ENVIRONMENT") or "").strip()
    return value or DEFAULT_ENVIRONMENT


def default_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _lock_object_id(environment: str) -> int:
    unsigned = zlib.crc32(environment.encode("utf-8")) & 0xFFFFFFFF
    return unsigned - 0x100000000 if unsigned >= 0x80000000 else unsigned


@dataclass(frozen=True)
class ProductionLockInfo:
    environment: str
    owner: str
    reentrant: bool
    lock_namespace: int
    lock_object: int


class ProductionBatchLock:
    def __init__(
        self,
        *,
        dsn: str,
        environment: str,
        owner: str,
        connect: Callable[..., psycopg.Connection] = psycopg.connect,
    ) -> None:
        self._dsn = dsn
        self._connect = connect
        self.info = ProductionLockInfo(
            environment=environment,
            owner=owner,
            reentrant=False,
            lock_namespace=PRODUCTION_BATCH_LOCK_NAMESPACE,
            lock_object=_lock_object_id(environment),
        )
        self._conn: psycopg.Connection | None = None

    def try_acquire(self) -> bool:
        conn = self._connect(self._dsn, autocommit=True)
        try:
            row = conn.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                (self.info.lock_namespace, self.info.lock_object),
            ).fetchone()
        except Exception:
            conn.close()
            raise
        acquired = bool(row and row[0])
        if not acquired:
            conn.close()
            return False
        self._conn = conn
        return True

    def release(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is None:
            return
        try:
            conn.execute(
                "SELECT pg_advisory_unlock(%s, %s)",
                (self.info.lock_namespace, self.info.lock_object),
            )
        finally:
            conn.close()


class _HeldLock:
    __slots__ = ("info", "lock", "refcount")

    def __init__(self, lock: ProductionBatchLock) -> None:
        self.lock = lock
        self.info = lock.info
        self.refcount = 1


_holder_guard = threading.Lock()
_holder: _HeldLock | None = None


def acquire_production_batch_lock(
    dsn: str,
    *,
    environment: str | None = None,
    owner: str | None = None,
    connect: Callable[..., psycopg.Connection] = psycopg.connect,
) -> ProductionLockInfo | None:
    """Acquire the environment-wide production batch lock or return ``None``.

    ``None`` means another invocation already owns the environment; the caller
    must exit before any provider or persistence work.
    """
    global _holder
    environment = environment or default_environment()
    owner = owner or default_owner()
    with _holder_guard:
        if _holder is not None:
            _holder.refcount += 1
            return replace(_holder.info, reentrant=True)
        lock = ProductionBatchLock(
            dsn=dsn,
            environment=environment,
            owner=owner,
            connect=connect,
        )
        if not lock.try_acquire():
            return None
        _holder = _HeldLock(lock)
        return _holder.info


def release_production_batch_lock(info: ProductionLockInfo | None) -> None:
    """Release one reference to the lock, unlocking when the last owner exits."""
    global _holder
    with _holder_guard:
        if _holder is None:
            return
        _holder.refcount -= 1
        if _holder.refcount > 0:
            return
        lock = _holder.lock
        _holder = None
    lock.release()


def reset_process_lock_state() -> None:
    """Test helper: drop process-local ownership state without touching the DB."""
    global _holder
    with _holder_guard:
        _holder = None


__all__ = [
    "DEFAULT_ENVIRONMENT",
    "PRODUCTION_BATCH_LOCK_NAMESPACE",
    "SKIPPED_ALREADY_RUNNING",
    "ProductionBatchLock",
    "ProductionLockInfo",
    "acquire_production_batch_lock",
    "default_environment",
    "default_owner",
    "release_production_batch_lock",
    "reset_process_lock_state",
]
