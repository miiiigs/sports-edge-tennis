"""Inactive research pilot for deadline-safe dual-provider acquisition.

This module connects the offline pilot calendar to a real transport boundary
without changing the live cron. Nothing here authorises spend by itself. A
request is issued only when all of the following are true:

* the caller supplied a non-empty owner authorization reference;
* both providers are verified for the requested operation;
* an atomic reservation was granted by the injected store;
* the worker lease was acquired;
* the request still has enough time to finish before ``min(issue, T-60)``.

The transport boundary is injected. The production adapter can wrap the existing
OddsPapi transport/ledger; the rehearsal adapter is deterministic. A timeout,
missing entitlement, missing allowance, duplicate request or late response is a
terminal non-qualifying result, never an authorised request.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Iterable, Mapping, Protocol, Sequence

PILOT_VERSION = "PREISSUE_PILOT_V1"
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_WAIT = timedelta(minutes=2)
DEFAULT_COMPLETION_BUFFER = timedelta(seconds=15)


class PilotStatus(StrEnum):
    BLOCKED_NOT_AUTHORISED = "BLOCKED_NOT_AUTHORISED"
    BLOCKED_ENTITLEMENT = "BLOCKED_ENTITLEMENT"
    WORKER_BUSY = "WORKER_BUSY"
    DEADLINE_MISSED = "DEADLINE_MISSED"
    WAIT_BUDGET_EXHAUSTED = "WAIT_BUDGET_EXHAUSTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    RESERVED = "RESERVED"
    CAPTURED = "CAPTURED"
    LATE_RESPONSE = "LATE_RESPONSE"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    ACKNOWLEDGEMENT_LOST = "ACKNOWLEDGEMENT_LOST"
    PERSIST_FAILED = "PERSIST_FAILED"
    NO_TWO_SIDED_PRICE = "NO_TWO_SIDED_PRICE"


class ReservationError(RuntimeError):
    """Base class for reservation failures."""


class BudgetExhausted(ReservationError):
    """Atomic allowance reservation refused the request."""


class DuplicateReservation(ReservationError):
    """The same request/retry already has a durable reservation."""


@dataclass(frozen=True)
class PilotPolicy:
    request_timeout_seconds: float = DEFAULT_TIMEOUT
    max_wait: timedelta = DEFAULT_MAX_WAIT
    completion_buffer: timedelta = DEFAULT_COMPLETION_BUFFER
    max_attempts: int = 1
    worker_lock_key: str = "preissue_pilot_worker_v1"
    qualification_freshness: timedelta = timedelta(hours=6)

    def __post_init__(self) -> None:
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.max_wait < timedelta(0):
            raise ValueError("max_wait must be non-negative")
        if self.completion_buffer < timedelta(0):
            raise ValueError("completion_buffer must be non-negative")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if not self.worker_lock_key.strip():
            raise ValueError("worker_lock_key must be non-empty")


@dataclass(frozen=True)
class PilotRequest:
    provider_code: str
    endpoint: str
    purpose: str
    identifiers: tuple[str, ...]
    match_ids: tuple[int, ...]
    deadline: datetime
    units: int = 1
    bookmaker: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_code.strip():
            raise ValueError("provider_code must be non-empty")
        if not self.endpoint.strip():
            raise ValueError("endpoint must be non-empty")
        if self.deadline.tzinfo is None:
            raise ValueError("deadline must be timezone-aware")
        if self.units <= 0:
            raise ValueError("units must be positive")

    @property
    def dedupe_key(self) -> str:
        payload = {
            "provider": self.provider_code,
            "endpoint": self.endpoint,
            "purpose": self.purpose,
            "identifiers": sorted(self.identifiers),
            "match_ids": sorted(self.match_ids),
            "deadline": self.deadline.astimezone(UTC).isoformat(),
            "bookmaker": self.bookmaker,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return f"pilot:{digest}"


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    provider_code: str
    dedupe_key: str
    retry_sequence: int
    units: int
    reserved_at: datetime
    status: str = "RESERVED"


@dataclass(frozen=True)
class TransportResult:
    received_at: datetime
    observed_at: datetime
    payload: Any
    provider_request_id: str | None = None
    payload_hash: str | None = None

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is None or self.observed_at.tzinfo is None:
            raise ValueError("transport timestamps must be timezone-aware")


@dataclass(frozen=True)
class PilotObservation:
    provider_code: str
    endpoint: str
    dedupe_key: str
    match_ids: tuple[int, ...]
    received_at: datetime
    observed_at: datetime
    persisted_at: datetime | None
    payload_hash: str
    qualification: str
    qualification_reasons: tuple[str, ...] = ()
    provider_request_id: str | None = None
    payload: Any = field(default=None, compare=False, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_code": self.provider_code,
            "endpoint": self.endpoint,
            "dedupe_key": self.dedupe_key,
            "match_ids": list(self.match_ids),
            "received_at": self.received_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "persisted_at": self.persisted_at.isoformat() if self.persisted_at else None,
            "payload_hash": self.payload_hash,
            "qualification": self.qualification,
            "qualification_reasons": list(self.qualification_reasons),
            "provider_request_id": self.provider_request_id,
        }


@dataclass
class PilotRequestResult:
    dedupe_key: str
    provider_code: str
    endpoint: str
    match_ids: tuple[int, ...]
    status: str
    reason: str
    reservations: list[Reservation] = field(default_factory=list)
    attempts: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    observation: PilotObservation | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dedupe_key": self.dedupe_key,
            "provider_code": self.provider_code,
            "endpoint": self.endpoint,
            "match_ids": list(self.match_ids),
            "status": self.status,
            "reason": self.reason,
            "attempts": self.attempts,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "reservations": [
                {
                    "reservation_id": item.reservation_id,
                    "provider_code": item.provider_code,
                    "dedupe_key": item.dedupe_key,
                    "retry_sequence": item.retry_sequence,
                    "units": item.units,
                    "reserved_at": item.reserved_at.isoformat(),
                    "status": item.status,
                }
                for item in self.reservations
            ],
            "observation": self.observation.as_dict() if self.observation else None,
        }


class ReservationStore(Protocol):
    def reserve(
        self, request: PilotRequest, *, now: datetime, retry_sequence: int,
    ) -> Reservation: ...

    def record(
        self, reservation: Reservation, *, success: bool, now: datetime,
        http_status: int | None = None, error_kind: str | None = None,
    ) -> None: ...

    def attempts(self, dedupe_key: str) -> int: ...


class WorkerLease(Protocol):
    def acquire(self, *, now: datetime) -> bool: ...

    def release(self) -> None: ...


class PilotTransport(Protocol):
    def fetch(
        self, request: PilotRequest, *, timeout_seconds: float,
        clock: Any,
    ) -> TransportResult: ...


class ObservationSink(Protocol):
    def persist(self, observation: PilotObservation) -> datetime: ...


class InMemoryReservationStore:
    """Thread-safe store used by unit tests and the offline rehearsal."""

    def __init__(self, *, allowances: Mapping[str, int] | None = None) -> None:
        self.allowances = dict(allowances or {})
        self._rows: dict[tuple[str, int], Reservation] = {}
        self._lock = threading.RLock()

    def reserve(
        self, request: PilotRequest, *, now: datetime, retry_sequence: int,
    ) -> Reservation:
        key = (request.dedupe_key, int(retry_sequence))
        with self._lock:
            if key in self._rows:
                raise DuplicateReservation(
                    f"already reserved dedupe_key={request.dedupe_key} "
                    f"retry={retry_sequence}"
                )
            if request.provider_code in self.allowances:
                if self.allowances[request.provider_code] < request.units:
                    raise BudgetExhausted(
                        f"allowance exhausted provider={request.provider_code}"
                    )
                self.allowances[request.provider_code] -= request.units
            reservation = Reservation(
                reservation_id=f"mem:{request.dedupe_key}:{retry_sequence}",
                provider_code=request.provider_code,
                dedupe_key=request.dedupe_key,
                retry_sequence=retry_sequence,
                units=request.units,
                reserved_at=now,
            )
            self._rows[key] = reservation
            return reservation

    def record(
        self, reservation: Reservation, *, success: bool, now: datetime,
        http_status: int | None = None, error_kind: str | None = None,
    ) -> None:
        with self._lock:
            current = self._rows.get(
                (reservation.dedupe_key, reservation.retry_sequence))
            if current is None:
                raise ReservationError("reservation not found")
            self._rows[(reservation.dedupe_key, reservation.retry_sequence)] = Reservation(
                **{**current.__dict__, "status": "SUCCESS" if success else "FAILURE"}
            )

    def attempts(self, dedupe_key: str) -> int:
        with self._lock:
            return sum(
                1 for (key, _retry) in self._rows
                if key == dedupe_key
            )

    def all_reservations(self) -> list[Reservation]:
        with self._lock:
            return list(self._rows.values())


class InMemoryLease:
    def __init__(self) -> None:
        self._held = False
        self._lock = threading.Lock()

    def acquire(self, *, now: datetime) -> bool:
        with self._lock:
            if self._held:
                return False
            self._held = True
            return True

    def release(self) -> None:
        with self._lock:
            self._held = False


class RehearsalTransport:
    """Deterministic transport. Responses may advance the clock or raise."""

    def __init__(
        self,
        *,
        responses: Mapping[str, Any],
        advance_clock: Any | None = None,
        payload_hash: str | None = None,
    ) -> None:
        self.responses = dict(responses)
        self.advance_clock = advance_clock
        self._payload_hash = payload_hash
        self.calls: list[str] = []

    def fetch(
        self, request: PilotRequest, *, timeout_seconds: float,
        clock: Any,
    ) -> TransportResult:
        self.calls.append(request.dedupe_key)
        if request.dedupe_key not in self.responses:
            raise TimeoutError("rehearsal response missing")
        response = self.responses[request.dedupe_key]
        if isinstance(response, Exception):
            raise response
        received_at = clock()
        if callable(self.advance_clock):
            self.advance_clock()
        observed_at = clock()
        if isinstance(response, Mapping) and "payload" in response:
            payload = response["payload"]
            provider_request_id = response.get("provider_request_id")
        else:
            payload = response
            provider_request_id = None
        payload_hash = self._payload_hash or "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        return TransportResult(
            received_at=received_at,
            observed_at=observed_at,
            payload=payload,
            provider_request_id=provider_request_id,
            payload_hash=payload_hash,
        )


class RecordingSink:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.records: list[PilotObservation] = []

    def persist(self, observation: PilotObservation) -> datetime:
        if self.failure is not None:
            raise self.failure
        persisted_at = observation.observed_at
        self.records.append(PilotObservation(
            **{**observation.__dict__, "persisted_at": persisted_at}
        ))
        return persisted_at


class JsonObservationSink:
    """Append-only JSONL sink with an fsync, used by the isolated rehearsal."""

    def __init__(self, path: str) -> None:
        self.path = path

    def persist(self, observation: PilotObservation) -> datetime:
        import os

        persisted_at = observation.observed_at
        record = PilotObservation(
            **{**observation.__dict__, "persisted_at": persisted_at}
        ).as_dict()
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return persisted_at


class SqliteReservationStore:
    """Durable, process-safe isolated store for the controlled rehearsal.

    It is deliberately separate from the production OddsPapi ledger. A real
    pilot must use ``PostgresOddspapiReservationStore`` for OddsPapi and a
    separately approved API-Tennis allowance mechanism.
    """

    def __init__(self, path: str, *, allowances: Mapping[str, int] | None = None) -> None:
        import sqlite3

        self._sqlite = sqlite3
        self.path = path
        self.conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pilot_allowances (
                provider_code TEXT PRIMARY KEY,
                remaining INTEGER NOT NULL CHECK (remaining >= 0)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pilot_reservations (
                dedupe_key TEXT NOT NULL,
                retry_sequence INTEGER NOT NULL,
                reservation_id TEXT NOT NULL,
                provider_code TEXT NOT NULL,
                units INTEGER NOT NULL,
                reserved_at TEXT NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (dedupe_key, retry_sequence)
            )
            """
        )
        for provider, remaining in (allowances or {}).items():
            self.conn.execute(
                "INSERT OR IGNORE INTO pilot_allowances(provider_code, remaining) "
                "VALUES (?, ?)",
                (provider, int(remaining)),
            )

    def reserve(
        self, request: PilotRequest, *, now: datetime, retry_sequence: int,
    ) -> Reservation:
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT reservation_id FROM pilot_reservations "
                "WHERE dedupe_key = ? AND retry_sequence = ?",
                (request.dedupe_key, int(retry_sequence)),
            ).fetchone()
            if existing is not None:
                raise DuplicateReservation(existing[0])
            row = conn.execute(
                "SELECT remaining FROM pilot_allowances WHERE provider_code = ?",
                (request.provider_code,),
            ).fetchone()
            if row is not None:
                if int(row[0]) < request.units:
                    raise BudgetExhausted(
                        f"allowance exhausted provider={request.provider_code}")
                conn.execute(
                    "UPDATE pilot_allowances SET remaining = remaining - ? "
                    "WHERE provider_code = ?",
                    (request.units, request.provider_code),
                )
            reservation = Reservation(
                reservation_id=f"sqlite:{request.dedupe_key}:{retry_sequence}",
                provider_code=request.provider_code,
                dedupe_key=request.dedupe_key,
                retry_sequence=int(retry_sequence),
                units=request.units,
                reserved_at=now,
            )
            conn.execute(
                "INSERT INTO pilot_reservations("
                "dedupe_key, retry_sequence, reservation_id, provider_code, units, "
                "reserved_at, status) VALUES (?, ?, ?, ?, ?, ?, 'RESERVED')",
                (
                    reservation.dedupe_key, reservation.retry_sequence,
                    reservation.reservation_id, reservation.provider_code,
                    reservation.units, reservation.reserved_at.isoformat(),
                ),
            )
            conn.execute("COMMIT")
            return reservation
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def record(
        self, reservation: Reservation, *, success: bool, now: datetime,
        http_status: int | None = None, error_kind: str | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE pilot_reservations SET status = ? "
            "WHERE dedupe_key = ? AND retry_sequence = ?",
            (
                "SUCCESS" if success else "FAILURE",
                reservation.dedupe_key, reservation.retry_sequence,
            ),
        )

    def attempts(self, dedupe_key: str) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM pilot_reservations WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        return int(row[0]) if row else 0

    def all_reservations(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT dedupe_key, retry_sequence, reservation_id, provider_code, "
            "units, reserved_at, status FROM pilot_reservations ORDER BY rowid"
        ).fetchall()
        return [
            {
                "dedupe_key": row[0], "retry_sequence": row[1],
                "reservation_id": row[2], "provider_code": row[3],
                "units": row[4], "reserved_at": row[5], "status": row[6],
            }
            for row in rows
        ]

    def close(self) -> None:
        self.conn.close()



class AcknowledgementLost(RuntimeError):
    """The provider response arrived but the durable acknowledgement did not."""


class PersistFailed(RuntimeError):
    pass


def _utc(value: datetime, name: str = "timestamp") -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _dedupe_requests(requests: Iterable[PilotRequest]) -> list[PilotRequest]:
    seen: set[str] = set()
    out: list[PilotRequest] = []
    for request in requests:
        if request.dedupe_key in seen:
            continue
        seen.add(request.dedupe_key)
        out.append(request)
    return out


def execute_pilot(
    requests: Sequence[PilotRequest],
    *,
    store: ReservationStore,
    lease: WorkerLease,
    transport: PilotTransport,
    sink: ObservationSink,
    clock: Any,
    policy: PilotPolicy = PilotPolicy(),
    entitlement_verified: bool = False,
    authorization_reference: str | None = None,
    qualification: Any | None = None,
) -> dict[str, Any]:
    """Execute a bounded pilot cycle. Every outcome is terminal and explicit."""
    now = _utc(clock(), "clock")
    report: dict[str, Any] = {
        "version": PILOT_VERSION,
        "started_at": now.isoformat(),
        "requested": len(requests),
        "unique_requests": len(_dedupe_requests(requests)),
        "provider_calls": 0,
        "reservations": 0,
        "observations": 0,
        "qualified": 0,
        "results": [],
        "status_counts": {},
        "authorization_reference": authorization_reference,
        "entitlement_verified": bool(entitlement_verified),
    }
    if not entitlement_verified:
        report["status"] = PilotStatus.BLOCKED_ENTITLEMENT.value
        report["reason"] = "PROVIDER_ENTITLEMENT_OR_QUOTA_NOT_VERIFIED"
        return report
    if not isinstance(authorization_reference, str) or not authorization_reference.strip():
        report["status"] = PilotStatus.BLOCKED_NOT_AUTHORISED.value
        report["reason"] = "OWNER_AUTHORIZATION_REFERENCE_REQUIRED"
        return report
    if not lease.acquire(now=now):
        report["status"] = PilotStatus.WORKER_BUSY.value
        report["reason"] = "CONCURRENT_PILOT_WORKER_ACTIVE"
        return report

    started = now
    try:
        for request in _dedupe_requests(requests):
            result = _execute_one(
                request, store=store, transport=transport, sink=sink,
                clock=clock, policy=policy, started=started,
                qualification=qualification,
            )
            report["results"].append(result.as_dict())
            report["status_counts"][result.status] = (
                report["status_counts"].get(result.status, 0) + 1
            )
            report["provider_calls"] += result.attempts
            report["reservations"] += len(result.reservations)
            if result.observation is not None:
                report["observations"] += 1
                if (
                    result.status == PilotStatus.CAPTURED.value
                    and result.observation.qualification == "ORIGINAL_TIME_QUALIFIED"
                ):
                    report["qualified"] += 1
    finally:
        lease.release()
        report["completed_at"] = _utc(clock(), "clock").isoformat()
    report["status"] = (
        "COMPLETED_WITH_FAILURES"
        if any(item["status"] not in (PilotStatus.CAPTURED.value,) for item in report["results"])
        else "COMPLETED"
    )
    return report


def _execute_one(
    request: PilotRequest,
    *,
    store: ReservationStore,
    transport: PilotTransport,
    sink: ObservationSink,
    clock: Any,
    policy: PilotPolicy,
    started: datetime,
    qualification: Any | None,
) -> PilotRequestResult:
    result = PilotRequestResult(
        dedupe_key=request.dedupe_key,
        provider_code=request.provider_code,
        endpoint=request.endpoint,
        match_ids=request.match_ids,
        status=PilotStatus.RESERVED.value,
        reason="PENDING",
    )
    now = _utc(clock(), "clock")
    if now + timedelta(seconds=policy.request_timeout_seconds) + policy.completion_buffer >= request.deadline:
        result.status = PilotStatus.DEADLINE_MISSED.value
        result.reason = "INSUFFICIENT_TIME_BEFORE_ISSUE_OR_T60"
        return result
    if now - started >= policy.max_wait:
        result.status = PilotStatus.WAIT_BUDGET_EXHAUSTED.value
        result.reason = "ACQUISITION_WAIT_BUDGET_EXHAUSTED"
        return result
    result.started_at = now
    last_error = "NO_ATTEMPT"
    for attempt in range(policy.max_attempts):
        if attempt > 0:
            now = _utc(clock(), "clock")
            if now + timedelta(seconds=policy.request_timeout_seconds) + policy.completion_buffer >= request.deadline:
                result.status = PilotStatus.DEADLINE_MISSED.value
                result.reason = "DEADLINE_PASSED_BEFORE_RETRY"
                return result
        try:
            reservation = store.reserve(
                request, now=_utc(clock(), "clock"), retry_sequence=attempt)
        except DuplicateReservation:
            result.status = PilotStatus.DUPLICATE_REQUEST.value
            result.reason = "DURABLE_RESERVATION_ALREADY_EXISTS"
            return result
        except BudgetExhausted:
            result.status = PilotStatus.BUDGET_EXHAUSTED.value
            result.reason = "ATOMIC_ALLOWANCE_EXHAUSTED"
            return result
        result.reservations.append(reservation)
        result.attempts += 1
        try:
            response = transport.fetch(
                request,
                timeout_seconds=policy.request_timeout_seconds,
                clock=clock,
            )
        except TimeoutError as exc:
            last_error = str(exc) or "PROVIDER_TIMEOUT"
            store.record(reservation, success=False, now=_utc(clock(), "clock"),
                         error_kind="TIMEOUT")
            if attempt + 1 >= policy.max_attempts:
                result.status = PilotStatus.TIMEOUT.value
                result.reason = "PROVIDER_TIMEOUT"
                return result
            continue
        except Exception as exc:  # transport failure must stay terminal
            last_error = f"{type(exc).__name__}:{exc}"
            store.record(reservation, success=False, now=_utc(clock(), "clock"),
                         error_kind="TRANSPORT_ERROR")
            if attempt + 1 >= policy.max_attempts:
                result.status = PilotStatus.PROVIDER_ERROR.value
                result.reason = last_error[:200]
                return result
            continue

        completed_at = _utc(clock(), "clock")
        store.record(reservation, success=True, now=completed_at)
        result.completed_at = completed_at
        observation = PilotObservation(
            provider_code=request.provider_code,
            endpoint=request.endpoint,
            dedupe_key=request.dedupe_key,
            match_ids=request.match_ids,
            received_at=_utc(response.received_at, "received_at"),
            observed_at=_utc(response.observed_at, "observed_at"),
            persisted_at=None,
            payload_hash=response.payload_hash or "sha256:missing",
            qualification="NOT_QUALIFIED",
            qualification_reasons=(),
            provider_request_id=response.provider_request_id,
            payload=response.payload,
        )
        if completed_at >= request.deadline:
            observation = PilotObservation(
                **{**observation.__dict__,
                   "qualification": "LATE_RESPONSE",
                   "qualification_reasons": ("RESPONSE_COMPLETED_AFTER_DEADLINE",)}
            )
            result.status = PilotStatus.LATE_RESPONSE.value
            result.reason = "RESPONSE_COMPLETED_AFTER_DEADLINE"
        else:
            try:
                qualification_result = (
                    qualification(observation=observation, request=request)
                    if qualification is not None else None
                )
            except Exception as exc:
                result.status = PilotStatus.PERSIST_FAILED.value
                result.reason = f"QUALIFICATION_ERROR:{type(exc).__name__}"
                result.observation = observation
                return result
            if qualification_result is not None:
                observation = PilotObservation(
                    **{**observation.__dict__,
                       "qualification": str(qualification_result.get("status", "NOT_QUALIFIED")),
                       "qualification_reasons": tuple(
                           str(value) for value in qualification_result.get("reasons", ())
                       )}
                )
            result.status = (
                PilotStatus.CAPTURED.value
                if observation.qualification == "ORIGINAL_TIME_QUALIFIED"
                else PilotStatus.NO_TWO_SIDED_PRICE.value
            )
            result.reason = (
                "ORIGINAL_TIME_QUALIFIED"
                if observation.qualification == "ORIGINAL_TIME_QUALIFIED"
                else "NOT_QUALIFIED"
            )
        try:
            persisted_at = sink.persist(observation)
        except AcknowledgementLost as exc:
            result.status = PilotStatus.ACKNOWLEDGEMENT_LOST.value
            result.reason = f"ACK_LOST:{exc}"[:200]
            result.observation = observation
            return result
        except Exception as exc:
            result.status = PilotStatus.PERSIST_FAILED.value
            result.reason = f"PERSIST_FAILED:{type(exc).__name__}"[:200]
            result.observation = observation
            return result
        observation = PilotObservation(
            **{**observation.__dict__, "persisted_at": _utc(persisted_at, "persisted_at")}
        )
        result.observation = observation
        return result
    result.status = PilotStatus.TIMEOUT.value
    result.reason = last_error
    return result


def requests_from_plan(
    plan: Mapping[str, Any],
    *,
    deadlines_by_match: Mapping[int, datetime],
    request_timeout_seconds: float,
) -> list[PilotRequest]:
    """Convert executed calendar cycles into bounded pilot requests.

    The deadline for each request is the earliest member deadline minus the
    transport timeout and completion buffer, so a successful response still
    finishes before issue/T-60.
    """
    requests: list[PilotRequest] = []
    for cycle in plan.get("cycles", []):
        match_ids = tuple(int(value) for value in cycle.get("match_ids", ()))
        if not match_ids:
            continue
        deadlines = [deadlines_by_match[mid] for mid in match_ids if mid in deadlines_by_match]
        if not deadlines:
            continue
        deadline = min(deadlines) - timedelta(seconds=request_timeout_seconds)
        for request in cycle.get("request_plan", ()):
            requests.append(PilotRequest(
                provider_code=str(request["provider_code"]),
                endpoint=str(request["endpoint"]),
                purpose=str(request["purpose"]),
                identifiers=tuple(str(value) for value in request.get("identifiers", ())),
                match_ids=match_ids,
                deadline=deadline,
                bookmaker=request.get("bookmaker"),
            ))
    return _dedupe_requests(requests)


class PostgresOddspapiReservationStore:
    """Atomic reservations through the existing OddsPapi ledger.

    A dedicated advisory lock makes check-and-reserve idempotent per
    ``(dedupe_key, retry_sequence)``. The existing ledger's own advisory lock
    still enforces the monthly allowance.
    """

    def __init__(self, conn: Any, *, config: Any, correlation_prefix: str) -> None:
        self.conn = conn
        self.config = config
        self.correlation_prefix = correlation_prefix

    def reserve(
        self, request: PilotRequest, *, now: datetime, retry_sequence: int,
    ) -> Reservation:
        from sports_edge_tennis.live.oddspapi_ledger import (
            OddspapiBudgetExceeded,
            reserve_attempt,
        )

        correlation_id = f"{self.correlation_prefix}:{request.dedupe_key}:{retry_sequence}"
        with self.conn.transaction():
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"preissue_pilot:{request.dedupe_key}:{retry_sequence}",),
            )
            existing = self.conn.execute(
                "SELECT oddspapi_request_ledger_id FROM oddspapi_request_ledger "
                "WHERE correlation_id = %s",
                (correlation_id,),
            ).fetchone()
            if existing is not None:
                raise DuplicateReservation(correlation_id)
            try:
                ledger_id = reserve_attempt(
                    self.conn,
                    config=self.config,
                    endpoint=request.endpoint,
                    request_purpose="DIAGNOSTIC",
                    classification="DIAGNOSTIC",
                    now=now,
                    bookmaker=request.bookmaker,
                    tournament_chunk_size=(
                        len(request.identifiers) if request.identifiers else None
                    ),
                    correlation_id=correlation_id,
                    retry_sequence=retry_sequence,
                )
            except OddspapiBudgetExceeded as exc:
                raise BudgetExhausted(str(exc)) from exc
        return Reservation(
            reservation_id=f"pg:{ledger_id}",
            provider_code=request.provider_code,
            dedupe_key=request.dedupe_key,
            retry_sequence=retry_sequence,
            units=request.units,
            reserved_at=now,
        )

    def record(
        self, reservation: Reservation, *, success: bool, now: datetime,
        http_status: int | None = None, error_kind: str | None = None,
    ) -> None:
        from sports_edge_tennis.live.oddspapi_ledger import record_outcome

        record_outcome(
            self.conn,
            ledger_id=int(reservation.reservation_id.split(":", 1)[1]),
            success=success,
            http_status=http_status,
            error_kind=error_kind,
            retry_sequence=reservation.retry_sequence,
        )

    def attempts(self, dedupe_key: str) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM oddspapi_request_ledger WHERE correlation_id LIKE %s",
            (f"{self.correlation_prefix}:{dedupe_key}:%",),
        ).fetchone()
        return int(row[0]) if row else 0


class PostgresWorkerLease:
    def __init__(self, conn: Any, *, lock_key: str) -> None:
        self.conn = conn
        self.lock_key = lock_key
        self._held = False

    def acquire(self, *, now: datetime) -> bool:
        row = self.conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))", (self.lock_key,)
        ).fetchone()
        self._held = bool(row and row[0])
        return self._held

    def release(self) -> None:
        if self._held:
            self.conn.execute(
                "SELECT pg_advisory_unlock(hashtext(%s))", (self.lock_key,)
            )
            self._held = False


__all__ = [
    "AcknowledgementLost",
    "BudgetExhausted",
    "DuplicateReservation",
    "InMemoryLease",
    "InMemoryReservationStore",
    "JsonObservationSink",
    "ObservationSink",
    "PilotObservation",
    "PilotPolicy",
    "PilotRequest",
    "PilotRequestResult",
    "PilotStatus",
    "PilotTransport",
    "PostgresOddspapiReservationStore",
    "PostgresWorkerLease",
    "RecordingSink",
    "RehearsalTransport",
    "Reservation",
    "ReservationError",
    "ReservationStore",
    "SqliteReservationStore",
    "TransportResult",
    "WorkerLease",
    "execute_pilot",
    "requests_from_plan",
]
