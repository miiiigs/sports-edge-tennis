"""Configurable independent monitoring for the tennis production pipeline.

The 13-16 November 2026 outage produced no failed rows: the platform scheduler
simply stopped triggering executions. A monitor that reads only rows written by
the job therefore reports "healthy" while the job is not running. This module
implements a dead-man's-switch contract on top of an independently hosted
service (Healthchecks.io or any equivalent HTTP heartbeat service).

Design rules enforced here:

* one monitored stream per pipeline stage class, not one per database table;
* a success signal requires every required stage to be ``COMPLETED`` **and**
  durably persisted with a matching row count; an in-memory or log-only result
  can never signal success;
* an empty eligible slate is successful only when fixture discovery and
  reconciliation both completed;
* a failed monitor delivery is reported as ``MONITOR_UNAVAILABLE`` and never
  advances the incident state, so the next cycle retries instead of silently
  treating an alert as delivered;
* credentials are read from the server environment, are excluded from repr and
  serialisation, and are never written to the durable state file.

No provider is called here and no database is written. The caller supplies the
stage evidence. ``SimulatedScheduler`` drives the same contract from an
independent simulated schedule so scheduler non-execution can be tested without
touching production.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Callable, Iterable, Mapping, Protocol, Sequence

# ---------------------------------------------------------------------------
# Pipeline streams
# ---------------------------------------------------------------------------

STAGE_FIXTURE_DISCOVERY = "FIXTURE_DISCOVERY"
STAGE_FORECAST_GENERATION = "FORECAST_GENERATION"
STAGE_BOOKMAKER_ACQUISITION = "BOOKMAKER_ACQUISITION"
STAGE_INDEPENDENT_ARCHIVAL = "INDEPENDENT_ARCHIVAL"
STAGE_OFFICIAL_SETTLEMENT = "OFFICIAL_SETTLEMENT"
STAGE_BOOKMAKER_SETTLEMENT = "BOOKMAKER_SETTLEMENT"
STAGE_PROSPECTIVE_CANDIDATE = "PROSPECTIVE_CANDIDATE_EXECUTION"


@dataclass(frozen=True)
class StageDefinition:
    """A pipeline stage that must complete durably before a success signal."""

    name: str
    required: bool
    durable_evidence: str
    description: str


STAGE_DEFINITIONS: tuple[StageDefinition, ...] = (
    StageDefinition(
        STAGE_FIXTURE_DISCOVERY,
        True,
        "match_versions / ingest_runs",
        "Provider fixture discovery persisted for the scheduled window.",
    ),
    StageDefinition(
        STAGE_FORECAST_GENERATION,
        True,
        "prediction_runs / predictions",
        "Completed on-time forecasts persisted for every eligible target.",
    ),
    StageDefinition(
        STAGE_BOOKMAKER_ACQUISITION,
        True,
        "provider odds ledger",
        "Bookmaker prices acquired and persisted for the eligible markets.",
    ),
    StageDefinition(
        STAGE_INDEPENDENT_ARCHIVAL,
        True,
        "external archive object",
        "Issued forecast and price archived outside the producing database.",
    ),
    StageDefinition(
        STAGE_OFFICIAL_SETTLEMENT,
        True,
        "official result observations",
        "Official match settlement persisted from a result observation.",
    ),
    StageDefinition(
        STAGE_BOOKMAKER_SETTLEMENT,
        True,
        "settlement ledger",
        "Bookmaker-market settlement persisted separately from the official result.",
    ),
    StageDefinition(
        STAGE_PROSPECTIVE_CANDIDATE,
        True,
        "candidate ledger",
        "Prospective candidate execution recorded before the fixture start.",
    ),
)

REQUIRED_STAGES: tuple[str, ...] = tuple(
    stage.name for stage in STAGE_DEFINITIONS if stage.required
)
ALL_STAGES: tuple[str, ...] = tuple(stage.name for stage in STAGE_DEFINITIONS)

# Terminal outcomes. ``IN_PROGRESS`` is a non-terminal evaluation state; the
# seven operational outcomes the owner asked for are the remaining constants.
OUTCOME_NEVER_TRIGGERED = "JOB_NEVER_TRIGGERED"
OUTCOME_STARTED_FAILED = "JOB_STARTED_FAILED"
OUTCOME_TIMED_OUT = "JOB_TIMED_OUT"
OUTCOME_PARTIAL = "JOB_COMPLETED_PARTIALLY"
OUTCOME_SUCCEEDED = "JOB_COMPLETED_SUCCESSFULLY"
OUTCOME_NO_ELIGIBLE_FIXTURES = "NO_ELIGIBLE_FIXTURES"
OUTCOME_MONITOR_UNAVAILABLE = "MONITORING_SERVICE_UNAVAILABLE"
OUTCOME_IN_PROGRESS = "IN_PROGRESS"

SUCCESS_OUTCOMES = frozenset({OUTCOME_SUCCEEDED, OUTCOME_NO_ELIGIBLE_FIXTURES})
FAILURE_OUTCOMES = frozenset(
    {
        OUTCOME_NEVER_TRIGGERED,
        OUTCOME_STARTED_FAILED,
        OUTCOME_TIMED_OUT,
        OUTCOME_PARTIAL,
    }
)

# ---------------------------------------------------------------------------
# Stage and run evidence
# ---------------------------------------------------------------------------

STAGE_COMPLETED = "COMPLETED"
STAGE_FAILED = "FAILED"
STAGE_SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class StageResult:
    """Evidence that one pipeline stage did the expected durable work."""

    stage: str
    state: str
    expected_rows: int | None
    persisted_rows: int | None
    durable_verified: bool
    verification: str = "NONE"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = None
    acknowledge_empty: bool = False

    def __post_init__(self) -> None:
        if self.stage not in ALL_STAGES:
            raise ValueError(f"unknown stage {self.stage!r}")
        if self.state not in (STAGE_COMPLETED, STAGE_FAILED, STAGE_SKIPPED):
            raise ValueError(f"unknown stage state {self.state!r}")
        if self.expected_rows is not None and self.expected_rows < 0:
            raise ValueError("expected_rows must not be negative")
        if self.persisted_rows is not None and self.persisted_rows < 0:
            raise ValueError("persisted_rows must not be negative")
        if self.durable_verified and self.verification == "NONE":
            raise ValueError("durable_verified requires a verification method")
        if self.acknowledge_empty and self.expected_rows not in (0, None):
            raise ValueError("acknowledge_empty applies only to an empty expectation")

    @property
    def completed_durably(self) -> bool:
        """True only for a completed stage with matching durable row evidence.

        An unknown expected count is not success. A completed stage whose
        expected and persisted counts differ is partial, not success.
        """

        if self.state != STAGE_COMPLETED or not self.durable_verified:
            return False
        if self.expected_rows is None or self.persisted_rows is None:
            return False
        if self.expected_rows != self.persisted_rows:
            return False
        if self.expected_rows == 0 and not self.acknowledge_empty:
            return False
        return True

    def to_payload(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "state": self.state,
            "expected_rows": self.expected_rows,
            "persisted_rows": self.persisted_rows,
            "durable_verified": self.durable_verified,
            "verification": self.verification,
            "completed_durably": self.completed_durably,
            "acknowledge_empty": self.acknowledge_empty,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RunRecord:
    """One scheduler execution and its stage ledger."""

    run_id: str
    slot: datetime
    started_at: datetime
    finished_at: datetime | None
    stages: tuple[StageResult, ...] = ()
    eligible_fixture_count: int | None = None
    discovered_fixture_count: int | None = None
    reconciled_fixture_count: int | None = None
    reconciliation_completed: bool = False
    error: str | None = None
    crashed: bool = False

    def __post_init__(self) -> None:
        if self.slot.tzinfo is None or self.slot.utcoffset() is None:
            raise ValueError("slot must be timezone-aware")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        seen: set[str] = set()
        for stage in self.stages:
            if stage.stage in seen:
                raise ValueError(f"duplicate stage result {stage.stage!r}")
            seen.add(stage.stage)
        if self.eligible_fixture_count is not None and self.eligible_fixture_count < 0:
            raise ValueError("eligible_fixture_count must not be negative")

    @property
    def stages_by_name(self) -> dict[str, StageResult]:
        return {stage.stage: stage for stage in self.stages}

    def to_payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "slot": self.slot.isoformat(),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "stages": [stage.to_payload() for stage in self.stages],
            "eligible_fixture_count": self.eligible_fixture_count,
            "discovered_fixture_count": self.discovered_fixture_count,
            "reconciled_fixture_count": self.reconciled_fixture_count,
            "reconciliation_completed": self.reconciliation_completed,
            "error": self.error,
            "crashed": self.crashed,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "RunRecord":
        stages = tuple(
            StageResult(
                stage=str(item["stage"]),
                state=str(item["state"]),
                expected_rows=_optional_int(item.get("expected_rows")),
                persisted_rows=_optional_int(item.get("persisted_rows")),
                durable_verified=bool(item.get("durable_verified")),
                verification=str(item.get("verification") or "NONE"),
                started_at=_optional_datetime(item.get("started_at")),
                finished_at=_optional_datetime(item.get("finished_at")),
                detail=_optional_str(item.get("detail")),
                acknowledge_empty=bool(item.get("acknowledge_empty", False)),
            )
            for item in (payload.get("stages") or ())  # type: ignore[union-attr]
        )
        return cls(
            run_id=str(payload["run_id"]),
            slot=_required_datetime(payload["slot"]),
            started_at=_required_datetime(payload["started_at"]),
            finished_at=_optional_datetime(payload.get("finished_at")),
            stages=stages,
            eligible_fixture_count=_optional_int(payload.get("eligible_fixture_count")),
            discovered_fixture_count=_optional_int(
                payload.get("discovered_fixture_count")
            ),
            reconciled_fixture_count=_optional_int(
                payload.get("reconciled_fixture_count")
            ),
            reconciliation_completed=bool(payload.get("reconciliation_completed")),
            error=_optional_str(payload.get("error")),
            crashed=bool(payload.get("crashed")),
        )


@dataclass(frozen=True)
class RunAssessment:
    outcome: str
    slot: datetime
    evaluated_at: datetime
    run_id: str | None
    missing_stages: tuple[str, ...]
    detail: str
    success_signal_allowed: bool
    durable_persistence_verified: bool
    late_finish_at: datetime | None = None
    monitor_status: str = "AVAILABLE"

    def to_payload(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "slot": self.slot.isoformat(),
            "evaluated_at": self.evaluated_at.isoformat(),
            "run_id": self.run_id,
            "missing_stages": list(self.missing_stages),
            "detail": self.detail,
            "success_signal_allowed": self.success_signal_allowed,
            "durable_persistence_verified": self.durable_persistence_verified,
            "late_finish_at": (
                self.late_finish_at.isoformat() if self.late_finish_at else None
            ),
            "monitor_status": self.monitor_status,
        }


def evaluate_run(
    record: RunRecord | None,
    *,
    slot: datetime,
    now: datetime,
    run_timeout: timedelta,
    required_stages: Sequence[str] = REQUIRED_STAGES,
) -> RunAssessment:
    """Classify one scheduled execution without ever inventing success.

    ``record is None`` means no start evidence exists for the slot. That is the
    silent scheduler failure mode and is reported as ``JOB_NEVER_TRIGGERED``,
    never as "no eligible fixtures".
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if run_timeout <= timedelta(0):
        raise ValueError("run_timeout must be positive")
    for stage in required_stages:
        if stage not in ALL_STAGES:
            raise ValueError(f"unknown required stage {stage!r}")

    if record is None:
        return RunAssessment(
            outcome=OUTCOME_NEVER_TRIGGERED,
            slot=slot,
            evaluated_at=now,
            run_id=None,
            missing_stages=tuple(required_stages),
            detail="NO_START_EVIDENCE_FOR_SLOT",
            success_signal_allowed=False,
            durable_persistence_verified=False,
        )

    if record.slot != slot:
        raise ValueError("run record does not belong to the evaluated slot")

    deadline = record.started_at + run_timeout
    if record.crashed:
        return _assessment(
            OUTCOME_STARTED_FAILED, record, now, required_stages,
            detail=f"CRASH:{record.error or 'UNSPECIFIED'}",
        )
    if record.error:
        return _assessment(
            OUTCOME_STARTED_FAILED, record, now, required_stages,
            detail=f"ERROR:{record.error}",
        )
    if record.finished_at is None:
        if now < deadline:
            return _assessment(
                OUTCOME_IN_PROGRESS, record, now, required_stages,
                detail="RUNNING_BEFORE_TIMEOUT",
            )
        return _assessment(
            OUTCOME_TIMED_OUT, record, now, required_stages,
            detail=f"NO_FINISH_BEFORE_{deadline.isoformat()}",
        )

    late_finish = record.finished_at if record.finished_at > deadline else None

    stages = record.stages_by_name
    missing = tuple(
        name
        for name in required_stages
        if name not in stages or not stages[name].completed_durably
    )
    all_durable = not missing

    if late_finish is not None:
        return _assessment(
            OUTCOME_TIMED_OUT, record, now, required_stages,
            detail="FINISHED_AFTER_DEADLINE",
            missing=missing,
            late_finish_at=late_finish,
        )
    if missing:
        return _assessment(
            OUTCOME_PARTIAL, record, now, required_stages,
            detail="REQUIRED_STAGE_INCOMPLETE_OR_NOT_DURABLE",
            missing=missing,
        )
    if record.eligible_fixture_count is None:
        return _assessment(
            OUTCOME_PARTIAL, record, now, required_stages,
            detail="ELIGIBLE_SLATE_SIZE_UNKNOWN",
            missing=missing,
        )
    if record.eligible_fixture_count == 0:
        discovery = stages.get(STAGE_FIXTURE_DISCOVERY)
        genuine_empty = (
            discovery is not None
            and discovery.completed_durably
            and record.reconciliation_completed
            and record.reconciled_fixture_count == 0
            and record.discovered_fixture_count == 0
        )
        if not genuine_empty:
            return _assessment(
                OUTCOME_PARTIAL, record, now, required_stages,
                detail="EMPTY_SLATE_WITHOUT_COMPLETED_DISCOVERY_AND_RECONCILIATION",
                missing=missing,
            )
        return _assessment(
            OUTCOME_NO_ELIGIBLE_FIXTURES, record, now, required_stages,
            detail="DISCOVERY_AND_RECONCILIATION_COMPLETED_WITH_EMPTY_SLATE",
            missing=missing,
        )
    if all_durable:
        return _assessment(
            OUTCOME_SUCCEEDED, record, now, required_stages,
            detail="ALL_REQUIRED_STAGES_COMPLETED_DURABLY",
            missing=missing,
        )
    return _assessment(
        OUTCOME_PARTIAL, record, now, required_stages,
        detail="UNREACHABLE",
        missing=missing,
    )


def _assessment(
    outcome: str,
    record: RunRecord,
    now: datetime,
    required_stages: Sequence[str],
    *,
    detail: str,
    missing: tuple[str, ...] = (),
    late_finish_at: datetime | None = None,
) -> RunAssessment:
    stages = record.stages_by_name
    verified = all(
        name in stages and stages[name].completed_durably for name in required_stages
    )
    return RunAssessment(
        outcome=outcome,
        slot=record.slot,
        evaluated_at=now,
        run_id=record.run_id,
        missing_stages=missing,
        detail=detail,
        success_signal_allowed=outcome in SUCCESS_OUTCOMES,
        durable_persistence_verified=verified,
        late_finish_at=late_finish_at,
    )


# ---------------------------------------------------------------------------
# Healthchecks.io / equivalent heartbeat client
# ---------------------------------------------------------------------------

SIGNAL_START = "start"
SIGNAL_SUCCESS = "success"
SIGNAL_FAILURE = "failure"
SIGNAL_LOG = "log"

_TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class MonitoringTransportError(RuntimeError):
    """Raised when the heartbeat service could not accept a signal."""


@dataclass(frozen=True)
class HealthchecksConfig:
    """Server-side heartbeat configuration.

    ``ping_key`` and ``uuid`` are secrets. They are excluded from ``repr`` and
    from :meth:`redacted`, and are loaded from environment variables by
    :meth:`from_environment`.
    """

    uuid: str | None = field(default=None, repr=False)
    ping_key: str | None = field(default=None, repr=False)
    slug: str | None = None
    base_url: str = "https://hc-ping.com"
    timeout_seconds: float = 10.0
    max_attempts: int = 3
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not (1 <= self.max_attempts <= 5):
            raise ValueError("max_attempts must be between 1 and 5")
        if self.uuid and (self.ping_key or self.slug):
            raise ValueError("configure either uuid or ping_key+slug, not both")
        if bool(self.ping_key) != bool(self.slug):
            raise ValueError("ping_key and slug must be configured together")
        if self.enabled and not self.uuid and not (self.ping_key and self.slug):
            raise ValueError("a check uuid or a ping_key+slug pair is required")

    @classmethod
    def from_environment(
        cls,
        *,
        stream: str,
        environ: Mapping[str, str] | None = None,
    ) -> "HealthchecksConfig":
        env = os.environ if environ is None else environ
        prefix = "TENNIS_HEALTHCHECKS"
        base_url = env.get(f"{prefix}_BASE_URL", "https://hc-ping.com").rstrip("/")
        uuid = env.get(f"{prefix}_{stream}_UUID") or env.get(f"{prefix}_UUID")
        ping_key = env.get(f"{prefix}_PING_KEY")
        slug = env.get(f"{prefix}_{stream}_SLUG") or env.get(f"{prefix}_SLUG")
        enabled = env.get(f"{prefix}_ENABLED", "0").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        return cls(
            uuid=uuid or None,
            ping_key=ping_key or None,
            slug=slug or None,
            base_url=base_url,
            enabled=enabled,
        )

    def redacted(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "identifier": (
                "uuid" if self.uuid else "ping_key+slug"
            ),
            "slug": self.slug,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "enabled": self.enabled,
        }

    def url_for(self, signal: str, *, exit_code: int | None = None) -> str:
        if not self.uuid and not (self.ping_key and self.slug):
            raise ValueError("no check identifier configured")
        if self.uuid:
            base = f"{self.base_url.rstrip('/')}/{self.uuid}"
        else:
            base = f"{self.base_url.rstrip('/')}/{self.ping_key}/{self.slug}"
        if signal == SIGNAL_SUCCESS:
            suffix = ""
        elif signal == SIGNAL_START:
            suffix = "/start"
        elif signal == SIGNAL_FAILURE:
            suffix = "/fail"
        elif signal == SIGNAL_LOG:
            suffix = "/log"
        elif signal == "exit_code":
            if exit_code is None or not (0 <= exit_code <= 255):
                raise ValueError("exit_code must be 0..255")
            suffix = f"/{exit_code}"
        else:
            raise ValueError(f"unknown signal {signal!r}")
        return base + suffix


@dataclass(frozen=True)
class PingResult:
    signal: str
    delivered: bool
    attempts: int
    status_code: int | None = None
    detail: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "signal": self.signal,
            "delivered": self.delivered,
            "attempts": self.attempts,
            "status_code": self.status_code,
            "detail": self.detail,
        }


class PingTransport(Protocol):
    def __call__(self, url: str, *, timeout: float, method: str, body: bytes | None) -> tuple[int, str]:
        ...


def urllib_ping_transport(
    url: str, *, timeout: float, method: str, body: bytes | None
) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "text/plain; charset=utf-8"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(256).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # HTTP status is still evidence
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MonitoringTransportError(str(exc)) from exc


class HealthchecksClient:
    def __init__(
        self,
        config: HealthchecksConfig,
        *,
        transport: PingTransport = urllib_ping_transport,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._sleep = sleep or (lambda _seconds: None)

    @property
    def config(self) -> HealthchecksConfig:
        return self._config

    def signal(
        self,
        signal: str,
        *,
        run_id: str | None = None,
        body: str | None = None,
        exit_code: int | None = None,
    ) -> PingResult:
        """Send one signal and report whether the service accepted it.

        A failure is never converted to success. HTTP 429 and 5xx are retried
        with linear backoff; a 404/409 is a configuration defect and fails
        immediately. ``200 OK`` with a non-``OK`` body is the documented
        "check not found" response, so the body is verified too.
        """

        if not self._config.enabled:
            return PingResult(
                signal=signal,
                delivered=False,
                attempts=0,
                detail="MONITORING_DISABLED",
            )
        url = self._config.url_for(signal, exit_code=exit_code)
        if run_id:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}rid={run_id}"
        method = "POST" if signal == SIGNAL_LOG else "GET"
        payload = body.encode("utf-8") if body else None
        last_detail = None
        last_status = None
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                status, response_body = self._transport(
                    url,
                    timeout=self._config.timeout_seconds,
                    method=method,
                    body=payload,
                )
            except MonitoringTransportError as exc:
                last_detail = f"TRANSPORT:{exc}"
                last_status = None
            else:
                last_status = status
                if status in (200, 201):
                    if response_body.strip().upper() == "OK":
                        return PingResult(
                            signal=signal,
                            delivered=True,
                            attempts=attempt,
                            status_code=status,
                        )
                    last_detail = "HTTP_200_WITHOUT_OK_BODY"
                elif status == 404:
                    return PingResult(
                        signal=signal,
                        delivered=False,
                        attempts=attempt,
                        status_code=status,
                        detail="CHECK_NOT_FOUND",
                    )
                elif status == 409:
                    return PingResult(
                        signal=signal,
                        delivered=False,
                        attempts=attempt,
                        status_code=status,
                        detail="AMBIGUOUS_SLUG",
                    )
                elif status in _TRANSIENT_STATUS:
                    last_detail = f"TRANSIENT_HTTP_{status}"
                else:
                    return PingResult(
                        signal=signal,
                        delivered=False,
                        attempts=attempt,
                        status_code=status,
                        detail=f"HTTP_{status}",
                    )
            if attempt < self._config.max_attempts:
                self._sleep(0.5 * attempt)
        return PingResult(
            signal=signal,
            delivered=False,
            attempts=self._config.max_attempts,
            status_code=last_status,
            detail=last_detail or "DELIVERY_FAILED",
        )


# ---------------------------------------------------------------------------
# Durable incident state
# ---------------------------------------------------------------------------

STATE_SCHEMA = "TENNIS_INDEPENDENT_MONITORING_STATE_V1"


@dataclass
class MonitoringState:
    active_incident: str | None = None
    active_since: str | None = None
    last_processed_slot: str | None = None
    last_action: str | None = None
    last_observed_at: str | None = None
    last_delivery_error: str | None = None
    consecutive_delivery_failures: int = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": STATE_SCHEMA,
            "active_incident": self.active_incident,
            "active_since": self.active_since,
            "last_processed_slot": self.last_processed_slot,
            "last_action": self.last_action,
            "last_observed_at": self.last_observed_at,
            "last_delivery_error": self.last_delivery_error,
            "consecutive_delivery_failures": self.consecutive_delivery_failures,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object] | None) -> "MonitoringState":
        if not payload:
            return cls()
        if payload.get("schema") != STATE_SCHEMA:
            raise ValueError("unrecognised monitoring state schema")
        return cls(
            active_incident=_optional_str(payload.get("active_incident")),
            active_since=_optional_str(payload.get("active_since")),
            last_processed_slot=_optional_str(payload.get("last_processed_slot")),
            last_action=_optional_str(payload.get("last_action")),
            last_observed_at=_optional_str(payload.get("last_observed_at")),
            last_delivery_error=_optional_str(payload.get("last_delivery_error")),
            consecutive_delivery_failures=int(
                payload.get("consecutive_delivery_failures") or 0
            ),
        )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[arg-type]


def _required_datetime(value: object) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _required_datetime(value)


RECEIPT_SCHEMA = "TENNIS_MONITORING_RECEIPT_V1"


@dataclass(frozen=True)
class MonitoringReceipt:
    """A durable completion receipt published by the pipeline itself.

    The monitor never derives success from logs or process exit codes. It reads
    this receipt, which the pipeline may only write after verifying the durable
    rows for every required stage.
    """

    slot: datetime
    record: RunRecord
    published_at: datetime
    publisher: str

    def __post_init__(self) -> None:
        if self.slot != self.record.slot:
            raise ValueError("receipt slot and run slot differ")
        if self.published_at < self.record.started_at:
            raise ValueError("receipt cannot predate the run")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": RECEIPT_SCHEMA,
            "slot": self.slot.isoformat(),
            "published_at": self.published_at.isoformat(),
            "publisher": self.publisher,
            "run": self.record.to_payload(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "MonitoringReceipt":
        if payload.get("schema") != RECEIPT_SCHEMA:
            raise ValueError("unrecognised monitoring receipt schema")
        run_payload = payload.get("run")
        if not isinstance(run_payload, Mapping):
            raise ValueError("receipt run must be an object")
        record = RunRecord.from_payload(run_payload)
        receipt = cls(
            slot=_required_datetime(payload["slot"]),
            record=record,
            published_at=_required_datetime(payload["published_at"]),
            publisher=str(payload.get("publisher") or "UNKNOWN"),
        )
        return receipt


def load_receipt(path: str) -> MonitoringReceipt:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("receipt must be a JSON object")
    return MonitoringReceipt.from_payload(payload)


def write_receipt(path: str, receipt: MonitoringReceipt) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(receipt.to_payload(), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_state(path: str | None) -> MonitoringState:
    if not path or not os.path.exists(path):
        return MonitoringState()
    with open(path, encoding="utf-8") as handle:
        return MonitoringState.from_payload(json.load(handle))


def write_state(path: str | None, state: MonitoringState) -> None:
    """Atomically persist state so a crash cannot corrupt duplicate tracking."""

    if not path:
        return
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state.to_payload(), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


ACTION_NONE = "NONE"
ACTION_INCIDENT = "INCIDENT"
ACTION_RECOVERY = "RECOVERY"
ACTION_SUPPRESSED = "DUPLICATE_SUPPRESSED"
ACTION_DELIVERY_FAILED = "DELIVERY_FAILED"


@dataclass(frozen=True)
class NotificationPlan:
    action: str
    fingerprint: str | None
    detail: str


def plan_notification(
    outcome: str,
    *,
    state: MonitoringState,
    detail: str,
) -> NotificationPlan:
    failed = outcome in FAILURE_OUTCOMES
    fingerprint = f"{outcome}:{detail}" if failed else None
    if failed:
        if state.active_incident == fingerprint:
            return NotificationPlan(ACTION_SUPPRESSED, fingerprint, detail)
        return NotificationPlan(ACTION_INCIDENT, fingerprint, detail)
    if state.active_incident:
        return NotificationPlan(ACTION_RECOVERY, state.active_incident, detail)
    return NotificationPlan(ACTION_NONE, None, detail)


def apply_notification(
    state: MonitoringState,
    plan: NotificationPlan,
    *,
    observed_at: datetime,
    delivered: bool | None,
) -> MonitoringState:
    """Advance state only when the notification path actually delivered.

    ``delivered is None`` means no delivery was required. ``delivered is
    False`` must not clear or set the active incident.
    """

    stamp = observed_at.isoformat()
    if delivered is False:
        return replace(
            state,
            last_action=ACTION_DELIVERY_FAILED,
            last_observed_at=stamp,
            last_delivery_error=plan.detail,
            consecutive_delivery_failures=state.consecutive_delivery_failures + 1,
        )
    next_state = replace(
        state,
        last_action=plan.action,
        last_observed_at=stamp,
        last_delivery_error=None,
        consecutive_delivery_failures=0,
    )
    if plan.action == ACTION_INCIDENT:
        next_state.active_incident = plan.fingerprint
        next_state.active_since = stamp
    elif plan.action == ACTION_RECOVERY:
        next_state.active_incident = None
        next_state.active_since = None
    return next_state


# ---------------------------------------------------------------------------
# Independent simulated schedule
# ---------------------------------------------------------------------------

class CronExpression:
    """A bounded five-field cron expression, enough for the deployed schedules.

    Supports ``*``, integers, comma lists, ranges and ``*/n`` steps. It is used
    only to derive *expected* slots independently of observed executions.
    """

    def __init__(self, expression: str) -> None:
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError("cron expression must have five fields")
        self.expression = expression
        self._minute = _cron_field(parts[0], 0, 59)
        self._hour = _cron_field(parts[1], 0, 23)
        self._day = _cron_field(parts[2], 1, 31)
        self._month = _cron_field(parts[3], 1, 12)
        self._weekday = _cron_field(parts[4], 0, 7)
        if 7 in self._weekday:
            self._weekday = frozenset(
                (self._weekday - {7}) | {0}
            )

    def matches(self, moment: datetime) -> bool:
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("moment must be timezone-aware")
        if moment.second or moment.microsecond:
            return False
        cron_weekday = (moment.weekday() + 1) % 7
        return (
            moment.minute in self._minute
            and moment.hour in self._hour
            and moment.day in self._day
            and moment.month in self._month
            and cron_weekday in self._weekday
        )

    def slots(self, start: datetime, end: datetime) -> tuple[datetime, ...]:
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        if end < start:
            raise ValueError("end must not precede start")
        current = start.replace(second=0, microsecond=0)
        if current < start:
            current += timedelta(minutes=1)
        slots: list[datetime] = []
        while current <= end:
            if self.matches(current):
                slots.append(current)
            current += timedelta(minutes=1)
        return tuple(slots)


def _cron_field(value: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in value.split(","):
        if part == "*":
            values.update(range(low, high + 1))
            continue
        step = 1
        if "/" in part:
            base, step_text = part.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError("cron step must be positive")
        else:
            base = part
        if base == "*":
            begin, finish = low, high
        elif "-" in base:
            begin_text, finish_text = base.split("-", 1)
            begin, finish = int(begin_text), int(finish_text)
        else:
            begin = finish = int(base)
        if begin < low or finish > high or begin > finish:
            raise ValueError(f"cron field out of range: {value!r}")
        values.update(range(begin, finish + 1, step))
    if not values:
        raise ValueError(f"empty cron field: {value!r}")
    return frozenset(values)


@dataclass(frozen=True)
class SimulatedTick:
    slot: datetime
    record: RunRecord | None
    assessment: RunAssessment


@dataclass(frozen=True)
class SimulationResult:
    cron: str
    window_start: datetime
    window_end: datetime
    ticks: tuple[SimulatedTick, ...]

    @property
    def outcomes(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for tick in self.ticks:
            counts[tick.assessment.outcome] = counts.get(tick.assessment.outcome, 0) + 1
        return counts

    @property
    def never_triggered_slots(self) -> tuple[datetime, ...]:
        return tuple(
            tick.slot
            for tick in self.ticks
            if tick.assessment.outcome == OUTCOME_NEVER_TRIGGERED
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "cron": self.cron,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "expected_slot_count": len(self.ticks),
            "outcomes": dict(sorted(self.outcomes.items())),
            "never_triggered_count": len(self.never_triggered_slots),
            "never_triggered_slots": [
                slot.isoformat() for slot in self.never_triggered_slots
            ],
            "ticks": [
                {
                    "slot": tick.slot.isoformat(),
                    "run_id": tick.record.run_id if tick.record else None,
                    "assessment": tick.assessment.to_payload(),
                }
                for tick in self.ticks
            ],
        }


def simulate_schedule(
    *,
    cron: str,
    window_start: datetime,
    window_end: datetime,
    behaviour: Callable[[datetime], RunRecord | None],
    run_timeout: timedelta = timedelta(minutes=45),
    required_stages: Sequence[str] = REQUIRED_STAGES,
) -> SimulationResult:
    """Evaluate an independent simulated schedule.

    ``behaviour`` returns the run record for a slot, or ``None`` to model a
    scheduler that never triggered. The evaluation clock is the slot's own
    deadline, so a late completion cannot retroactively satisfy an earlier slot.
    """

    expression = CronExpression(cron)
    slots = expression.slots(window_start, window_end)
    ticks: list[SimulatedTick] = []
    for slot in slots:
        record = behaviour(slot)
        evaluated_at = slot + run_timeout
        ticks.append(
            SimulatedTick(
                slot=slot,
                record=record,
                assessment=evaluate_run(
                    record,
                    slot=slot,
                    now=evaluated_at,
                    run_timeout=run_timeout,
                    required_stages=required_stages,
                ),
            )
        )
    return SimulationResult(
        cron=cron,
        window_start=window_start,
        window_end=window_end,
        ticks=tuple(ticks),
    )


# ---------------------------------------------------------------------------
# Cycle orchestration
# ---------------------------------------------------------------------------

EXIT_HEALTHY = 0
EXIT_RUN_FAILURE = 1
EXIT_MONITOR_UNAVAILABLE = 2


@dataclass(frozen=True)
class CycleResult:
    assessment: RunAssessment
    notification: NotificationPlan
    ping: PingResult | None
    state: MonitoringState
    exit_code: int

    def to_payload(self) -> dict[str, object]:
        return {
            "assessment": self.assessment.to_payload(),
            "notification": {
                "action": self.notification.action,
                "fingerprint": self.notification.fingerprint,
                "detail": self.notification.detail,
            },
            "ping": self.ping.to_payload() if self.ping else None,
            "state": self.state.to_payload(),
            "exit_code": self.exit_code,
        }


def run_monitoring_cycle(
    *,
    slot: datetime,
    record: RunRecord | None,
    now: datetime,
    state: MonitoringState,
    client: HealthchecksClient | None,
    run_timeout: timedelta = timedelta(minutes=45),
    required_stages: Sequence[str] = REQUIRED_STAGES,
    dry_run: bool = False,
) -> CycleResult:
    assessment = evaluate_run(
        record,
        slot=slot,
        now=now,
        run_timeout=run_timeout,
        required_stages=required_stages,
    )
    plan = plan_notification(
        assessment.outcome, state=state, detail=assessment.detail
    )

    # Healthchecks-style monitoring is a dead man's switch: a healthy cycle must
    # still send a success heartbeat, otherwise the service alerts on silence.
    # A new incident sends a failure signal; a duplicate incident is already
    # down, so it neither re-pages nor sends a misleading success.
    signal: str | None = None
    if plan.action == ACTION_RECOVERY:
        signal = SIGNAL_SUCCESS
    elif plan.action == ACTION_INCIDENT:
        signal = SIGNAL_FAILURE
    elif plan.action == ACTION_NONE and assessment.outcome in SUCCESS_OUTCOMES:
        signal = SIGNAL_SUCCESS

    ping: PingResult | None = None
    delivered: bool | None = None
    if signal is not None and not dry_run:
        if client is None:
            delivered = False
        else:
            ping = client.signal(
                signal,
                body=json.dumps(assessment.to_payload(), sort_keys=True),
            )
            delivered = ping.delivered
            if not ping.delivered:
                assessment = replace(
                    assessment, monitor_status=OUTCOME_MONITOR_UNAVAILABLE
                )

    next_state = apply_notification(
        state, plan, observed_at=now, delivered=delivered
    )
    if assessment.outcome == OUTCOME_IN_PROGRESS:
        exit_code = EXIT_HEALTHY
    elif assessment.outcome in SUCCESS_OUTCOMES:
        exit_code = EXIT_HEALTHY
    else:
        exit_code = EXIT_RUN_FAILURE
    if delivered is False:
        exit_code = EXIT_MONITOR_UNAVAILABLE
    return CycleResult(
        assessment=assessment,
        notification=plan,
        ping=ping,
        state=next_state,
        exit_code=exit_code,
    )


def describe_configuration(
    configs: Mapping[str, HealthchecksConfig],
) -> dict[str, object]:
    """Return a credential-free description of the configured streams."""

    return {
        "streams": {name: config.redacted() for name, config in configs.items()},
        "credentials_location": "SERVER_ENVIRONMENT_ONLY",
        "external_account_provisioned": False,
    }
