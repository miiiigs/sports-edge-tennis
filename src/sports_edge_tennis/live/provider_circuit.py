"""Bounded provider-pair circuit breaker for the scheduled live batch.

The live batch runs from a fresh process on every Railway cron invocation. The
circuit therefore persists its state in PostgreSQL (the existing append-only
``data_health_checks`` operational-health table) instead of process memory, so a
temporary primary-provider outage is remembered across invocations.

Model
-----
CLOSED
    The primary pair (SPORTRADAR_V3 + ODDSPAPI_TENNIS) is active.
OPEN
    The fallback pair (API_TENNIS + LEGACY_SPLIT) is active. A bounded cooldown
    prevents the primary from being probed on every cycle.
HALF_OPEN
    A single lightweight recovery probe already succeeded. The primary becomes
    eligible for the *next* atomic production cycle; the current cycle stays on
    the fallback so a provider pair is never switched mid-cycle.

The provider pair is resolved exactly once at batch start and threaded through
the whole cycle. Incompatible combinations (API_TENNIS data with ODDSPAPI odds,
or the reverse odds-only cutover) are rejected outright.

The module is pure with respect to the network except for the injected recovery
probe, and never logs credentials.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.provider_authority import (
    DATA_MODE_ENV,
    LEGACY_DATA_MODE,
    LEGACY_ODDS_MODE,
    ODDS_MODE_ENV,
    ProviderAuthority,
    TARGET_DATA_MODE,
    TARGET_ODDS_MODE,
    evaluate_cutover_readiness,
)
from sports_edge_tennis.live.provider_failures import classify_provider_failure
from sports_edge_tennis.live.sportradar import (
    SportradarTransportError,
    fetch_competitions,
)


PRIMARY_ROLE = "PRIMARY"
FALLBACK_ROLE = "FALLBACK"
CONFIGURED_ROLE = "CONFIGURED"

STATE_DISABLED = "DISABLED"
STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"

SOURCE_SYSTEM = "SPORTS_EDGE_TENNIS"
DATASET_NAME = "PROVIDER_CIRCUIT"
COOLDOWN_ENV = "TENNIS_PROVIDER_CIRCUIT_COOLDOWN_SECONDS"
DEFAULT_COOLDOWN_SECONDS = 3600
PROBE_TIMEOUT_SECONDS = 5.0
_ENVIRONMENT_ENV = "TENNIS_ENVIRONMENT"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _log(message: str) -> None:
    print(message, flush=True)


class CircuitStoreUnavailable(RuntimeError):
    """Raised when the durable circuit state store cannot be used."""


@dataclass(frozen=True)
class ProviderPair:
    data_mode: str
    odds_mode: str
    role: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.data_mode, self.odds_mode)

    @property
    def label(self) -> str:
        return f"{self.data_mode}+{self.odds_mode}"


PRIMARY_PAIR = ProviderPair(TARGET_DATA_MODE, TARGET_ODDS_MODE, PRIMARY_ROLE)
FALLBACK_PAIR = ProviderPair(LEGACY_DATA_MODE, LEGACY_ODDS_MODE, FALLBACK_ROLE)
SUPPORTED_PAIR_KEYS = frozenset({PRIMARY_PAIR.key, FALLBACK_PAIR.key})


def assert_compatible_pair(data_mode: str, odds_mode: str) -> ProviderPair:
    """Return the supported pair for these modes or raise.

    Only two pairs are ever allowed to run: the primary pair and the compatible
    fallback pair. Mixing API-Tennis data with OddsPapi odds (or an odds-only
    cutover) is a hard error.
    """
    data = str(data_mode or "").strip().upper()
    odds = str(odds_mode or "").strip().upper()
    pair = None
    for candidate in (PRIMARY_PAIR, FALLBACK_PAIR):
        if candidate.data_mode == data and candidate.odds_mode == odds:
            pair = candidate
            break
    if pair is None:
        raise ValueError(
            "incompatible provider pair rejected: "
            f"data={data!r} odds={odds!r}; allowed pairs are "
            f"{PRIMARY_PAIR.label} and {FALLBACK_PAIR.label}"
        )
    return pair


def pair_for_role(role: str) -> ProviderPair:
    if role == PRIMARY_ROLE:
        return PRIMARY_PAIR
    if role == FALLBACK_ROLE:
        return FALLBACK_PAIR
    raise ValueError(f"unsupported provider pair role {role!r}")


def circuit_is_active(authority: Any) -> bool:
    """The circuit only protects the configured primary pair."""
    return (
        getattr(authority, "data_mode", None) == TARGET_DATA_MODE
        and getattr(authority, "odds_mode", None) == TARGET_ODDS_MODE
    )


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    failure_kind: str | None = None


@dataclass(frozen=True)
class CircuitState:
    scope: str
    state: str
    failure_class: str | None
    failure_provider: str | None
    failure_kind: str | None
    opened_at: datetime | None
    next_probe_at: datetime | None
    last_probe_at: datetime | None
    last_probe_status: str | None
    last_probe_kind: str | None
    opened_count: int
    updated_at: datetime


@dataclass(frozen=True)
class CircuitDecision:
    pair: ProviderPair
    state: str
    reason: str
    active: bool
    scope: str
    failure_class: str | None = None
    failure_kind: str | None = None
    opened_at: datetime | None = None
    probe_status: str | None = None

    @property
    def authority(self) -> ProviderAuthority:
        return ProviderAuthority(
            data_mode=self.pair.data_mode,
            odds_mode=self.pair.odds_mode,
        )

    def apply_env(self, env: Mapping[str, str]) -> dict[str, str]:
        effective = dict(env)
        effective[DATA_MODE_ENV] = self.pair.data_mode
        effective[ODDS_MODE_ENV] = self.pair.odds_mode
        return effective

    def lineage(self) -> str:
        if not self.active:
            return (
                f"data={self.pair.data_mode} odds={self.pair.odds_mode} "
                "circuit=DISABLED reason=NOT_PRIMARY_AUTHORITY"
            )
        return (
            f"data={self.pair.data_mode} odds={self.pair.odds_mode} "
            f"circuit={self.state} reason={self.reason}"
            + (f" failure={self.failure_class}" if self.failure_class else "")
            + (
                f" opened_at={self.opened_at.isoformat()}"
                if self.opened_at is not None
                else ""
            )
        )

    def telemetry(self) -> str:
        if not self.active:
            return (
                "PROVIDER_CIRCUIT state=DISABLED "
                f"data={self.pair.data_mode} odds={self.pair.odds_mode} "
                "reason=NOT_PRIMARY_AUTHORITY"
            )
        if self.state == STATE_CLOSED:
            return (
                "PROVIDER_CIRCUIT state=CLOSED "
                f"primary={TARGET_DATA_MODE} odds={TARGET_ODDS_MODE}"
            )
        if self.state == STATE_HALF_OPEN and self.pair.key == PRIMARY_PAIR.key:
            return (
                "PROVIDER_CIRCUIT state=HALF_OPEN "
                f"primary={TARGET_DATA_MODE} odds={TARGET_ODDS_MODE} "
                "reason=PRIMARY_ELIGIBLE"
            )
        if self.state == STATE_HALF_OPEN:
            return (
                "PROVIDER_RECOVERY state=HALF_OPEN probe=SUCCESS "
                "primary_eligible_next_cycle=true "
                f"fallback={self.pair.data_mode}"
            )
        return (
            "PROVIDER_FAILOVER state=OPEN "
            f"data={self.pair.data_mode} odds={self.pair.odds_mode} "
            f"reason={self.reason}"
            + (
                f" opened_at={self.opened_at.isoformat()}"
                if self.opened_at is not None
                else ""
            )
        )


class CircuitStateStore(Protocol):
    def load(self, scope: str) -> CircuitState | None: ...

    def save(self, state: CircuitState) -> None: ...


class InMemoryCircuitStateStore:
    """Deterministic store used by tests and simulations."""

    def __init__(self) -> None:
        self._states: dict[str, CircuitState] = {}

    def load(self, scope: str) -> CircuitState | None:
        return self._states.get(scope)

    def save(self, state: CircuitState) -> None:
        self._states[state.scope] = state


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _state_to_notes(state: CircuitState) -> str:
    return json.dumps(
        {
            "v": 1,
            "scope": state.scope,
            "state": state.state,
            "failure_class": state.failure_class,
            "failure_provider": state.failure_provider,
            "failure_kind": state.failure_kind,
            "opened_at": state.opened_at.isoformat() if state.opened_at else None,
            "next_probe_at": state.next_probe_at.isoformat() if state.next_probe_at else None,
            "last_probe_at": state.last_probe_at.isoformat() if state.last_probe_at else None,
            "last_probe_status": state.last_probe_status,
            "last_probe_kind": state.last_probe_kind,
            "opened_count": state.opened_count,
            "updated_at": state.updated_at.isoformat(),
        },
        sort_keys=True,
    )


def _notes_to_state(
    scope: str,
    notes: str | None,
    checked_at: datetime,
) -> CircuitState | None:
    if not notes:
        return None
    try:
        payload = json.loads(notes)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("state") not in {
        STATE_CLOSED,
        STATE_OPEN,
        STATE_HALF_OPEN,
    }:
        return None
    return CircuitState(
        scope=scope,
        state=str(payload["state"]),
        failure_class=payload.get("failure_class"),
        failure_provider=payload.get("failure_provider"),
        failure_kind=payload.get("failure_kind"),
        opened_at=_parse_dt(payload.get("opened_at")),
        next_probe_at=_parse_dt(payload.get("next_probe_at")),
        last_probe_at=_parse_dt(payload.get("last_probe_at")),
        last_probe_status=payload.get("last_probe_status"),
        last_probe_kind=payload.get("last_probe_kind"),
        opened_count=int(payload.get("opened_count") or 0),
        updated_at=_parse_dt(payload.get("updated_at")) or checked_at,
    )


class PostgresCircuitStateStore:
    """Durable circuit state stored as append-only health records.

    ``data_health_checks`` is append-only, so each transition is one immutable
    record and the latest record by ``checked_at`` (tie-break by id) is the
    current state. This keeps state changes idempotent and fully auditable while
    requiring no schema migration.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @staticmethod
    def dataset_for_scope(scope: str) -> str:
        return f"{DATASET_NAME}::{scope}"

    def _require_usable(self) -> None:
        if not hasattr(self._conn, "execute"):
            raise CircuitStoreUnavailable("connection has no execute()")

    def load(self, scope: str) -> CircuitState | None:
        self._require_usable()
        row = self._conn.execute(
            """
            SELECT status::text, notes, checked_at
            FROM data_health_checks
            WHERE source_system = %s
              AND dataset_name = %s
            ORDER BY checked_at DESC, data_health_check_id DESC
            LIMIT 1
            """,
            (SOURCE_SYSTEM, self.dataset_for_scope(scope)),
        ).fetchone()
        if row is None:
            return None
        return _notes_to_state(scope, row[1], row[2])

    def save(self, state: CircuitState) -> None:
        self._require_usable()
        freshness = "FRESH" if state.state == STATE_CLOSED else "UNKNOWN"
        status = {
            STATE_CLOSED: "HEALTHY",
            STATE_HALF_OPEN: "WARNING",
            STATE_OPEN: "ERROR",
        }[state.state]
        self._conn.execute(
            """
            INSERT INTO data_health_checks (
                source_system,
                dataset_name,
                checked_at,
                latest_observation_at,
                freshness_state,
                coverage_ratio,
                unmatched_count,
                error_count,
                status,
                notes
            )
            VALUES (%s, %s, %s, %s, %s, NULL, 0, %s, %s, %s)
            """,
            (
                SOURCE_SYSTEM,
                self.dataset_for_scope(state.scope),
                state.updated_at,
                state.opened_at,
                freshness,
                int(state.state == STATE_OPEN),
                status,
                _state_to_notes(state),
            ),
        )


def read_cooldown_seconds(env: Mapping[str, str]) -> int:
    raw = env.get(COOLDOWN_ENV)
    if raw in (None, ""):
        return DEFAULT_COOLDOWN_SECONDS
    try:
        value = int(str(raw).strip())
    except ValueError:
        return DEFAULT_COOLDOWN_SECONDS
    return value if value > 0 else DEFAULT_COOLDOWN_SECONDS


def scope_for_environment(env: Mapping[str, str]) -> str:
    value = str(env.get(_ENVIRONMENT_ENV) or "").strip()
    return value or "production"


@dataclass(frozen=True)
class PairReadiness:
    pair: ProviderPair
    ready: bool
    blockers: tuple[str, ...]


def _has_secret(env: Mapping[str, str], name: str) -> bool:
    if str(env.get(name) or "").strip():
        return True
    return bool(load_secret(name))


def _fallback_credential_blockers(env: Mapping[str, str]) -> list[str]:
    """Fallback readiness: API-Tennis fixtures/offered odds + Pinnapi sharp."""
    blockers: list[str] = []
    for name in ("API_TENNIS_API_KEY", "PINNAPI_KEY"):
        if not _has_secret(env, name):
            blockers.append(f"MISSING_ENV:{name}")
    return blockers


def evaluate_decision_readiness(
    conn: Any,
    *,
    decision: CircuitDecision,
    env: Mapping[str, str],
) -> PairReadiness:
    """Validate readiness for exactly the pair the circuit selected."""
    if not decision.active:
        readiness = evaluate_cutover_readiness(conn, env=env)
        return PairReadiness(
            pair=decision.pair,
            ready=readiness.ready,
            blockers=tuple(readiness.blockers),
        )

    pair_env = decision.apply_env(env)
    readiness = evaluate_cutover_readiness(conn, env=pair_env)
    blockers = list(readiness.blockers)
    if decision.pair.key == FALLBACK_PAIR.key:
        blockers.extend(_fallback_credential_blockers(env))
    return PairReadiness(
        pair=decision.pair,
        ready=not blockers,
        blockers=tuple(blockers),
    )


def assert_decision_ready(
    conn: Any,
    *,
    decision: CircuitDecision,
    env: Mapping[str, str],
) -> PairReadiness:
    readiness = evaluate_decision_readiness(conn, decision=decision, env=env)
    if not readiness.ready:
        raise RuntimeError(
            "TENNIS_PROVIDER_PAIR_BLOCKED " + ",".join(readiness.blockers)
        )
    return readiness


def disabled_decision(configured: Any) -> CircuitDecision:
    return CircuitDecision(
        pair=ProviderPair(
            data_mode=str(getattr(configured, "data_mode", "")),
            odds_mode=str(getattr(configured, "odds_mode", "")),
            role=CONFIGURED_ROLE,
        ),
        state=STATE_DISABLED,
        reason="NOT_PRIMARY_AUTHORITY",
        active=False,
        scope=scope_for_environment(os.environ),
    )


def _decision(
    *,
    pair: ProviderPair,
    state: str,
    reason: str,
    scope: str,
    state_row: CircuitState | None = None,
    probe_status: str | None = None,
) -> CircuitDecision:
    return CircuitDecision(
        pair=pair,
        state=state,
        reason=reason,
        active=True,
        scope=scope,
        failure_class=state_row.failure_class if state_row else None,
        failure_kind=state_row.failure_kind if state_row else None,
        opened_at=state_row.opened_at if state_row else None,
        probe_status=probe_status,
    )


def resolve_provider_pair(
    *,
    store: CircuitStateStore,
    configured: Any,
    env: Mapping[str, str],
    now: datetime | None = None,
    probe: Callable[[], ProbeResult] | None = None,
) -> CircuitDecision:
    """Resolve the single provider pair for one atomic production cycle."""
    if not circuit_is_active(configured):
        return disabled_decision(configured)

    now = now or _utc_now()
    scope = scope_for_environment(env)
    cooldown = read_cooldown_seconds(env)
    state = store.load(scope)

    if state is None or state.state == STATE_CLOSED:
        decision = _decision(
            pair=PRIMARY_PAIR,
            state=STATE_CLOSED,
            reason="PRIMARY_ACTIVE",
            scope=scope,
            state_row=state,
        )
        _log(decision.telemetry())
        return decision

    if state.state == STATE_HALF_OPEN:
        # A prior cycle's probe succeeded: the primary is eligible now.
        decision = _decision(
            pair=PRIMARY_PAIR,
            state=STATE_HALF_OPEN,
            reason="PRIMARY_ELIGIBLE",
            scope=scope,
            state_row=state,
        )
        _log(decision.telemetry())
        return decision

    # Open, and still inside the cooldown: fall back without probing.
    if state.next_probe_at is not None and now < state.next_probe_at:
        decision = _decision(
            pair=FALLBACK_PAIR,
            state=STATE_OPEN,
            reason=state.failure_class or "PRIMARY_UNAVAILABLE",
            scope=scope,
            state_row=state,
            probe_status=state.last_probe_status,
        )
        _log(decision.telemetry())
        return decision

    # Cooldown elapsed: exactly one bounded recovery probe for this cycle.
    result = probe() if probe is not None else ProbeResult(ok=False, failure_kind="NO_PROBE")
    if result.ok:
        recovered = replace(
            state,
            state=STATE_HALF_OPEN,
            last_probe_at=now,
            last_probe_status="SUCCESS",
            last_probe_kind=None,
            updated_at=now,
            next_probe_at=None,
        )
        store.save(recovered)
        decision = _decision(
            pair=FALLBACK_PAIR,
            state=STATE_HALF_OPEN,
            reason="PROBE_SUCCESS_PRIMARY_NEXT_CYCLE",
            scope=scope,
            state_row=recovered,
            probe_status="SUCCESS",
        )
        _log(decision.telemetry())
        return decision

    refreshed = replace(
        state,
        state=STATE_OPEN,
        next_probe_at=now + timedelta(seconds=cooldown),
        last_probe_at=now,
        last_probe_status="FAILED",
        last_probe_kind=str(result.failure_kind or "UNKNOWN"),
        updated_at=now,
    )
    store.save(refreshed)
    decision = _decision(
        pair=FALLBACK_PAIR,
        state=STATE_OPEN,
        reason=state.failure_class or "PRIMARY_UNAVAILABLE",
        scope=scope,
        state_row=refreshed,
        probe_status="FAILED",
    )
    _log(decision.telemetry())
    return decision


def record_primary_outcome(
    *,
    store: CircuitStateStore,
    configured: Any,
    now: datetime | None = None,
    success: bool,
    failure_kind: str | None = None,
    env: Mapping[str, str] | None = None,
    failure_provider: str = TARGET_DATA_MODE,
) -> None:
    """Persist the primary provider's outcome for the just-finished cycle."""
    if not circuit_is_active(configured):
        return

    now = now or _utc_now()
    env = env if env is not None else os.environ
    scope = scope_for_environment(env)
    cooldown = read_cooldown_seconds(env)
    state = store.load(scope)

    if success:
        if state is not None and state.state == STATE_HALF_OPEN:
            recovered = CircuitState(
                scope=scope,
                state=STATE_CLOSED,
                failure_class=None,
                failure_provider=None,
                failure_kind=None,
                opened_at=None,
                next_probe_at=None,
                last_probe_at=state.last_probe_at,
                last_probe_status="SUCCESS",
                last_probe_kind=None,
                opened_count=state.opened_count,
                updated_at=now,
            )
            store.save(recovered)
            _log(
                "PROVIDER_RECOVERY state=CLOSED probe=SUCCESS "
                f"primary={TARGET_DATA_MODE} odds={TARGET_ODDS_MODE}"
            )
        return

    classified = classify_provider_failure(failure_kind)
    if not classified.failover_eligible:
        # Fail closed: an auth/contract/unknown failure is never masked as
        # provider downtime and never opens the circuit.
        _log(
            "PROVIDER_CIRCUIT_FAIL_CLOSED "
            f"class={classified.failure_class.value} kind={classified.kind or 'UNKNOWN'}"
        )
        return

    if state is not None and state.state == STATE_OPEN:
        # Already open; keep the current cooldown/opened_at.
        return

    opened = CircuitState(
        scope=scope,
        state=STATE_OPEN,
        failure_class=classified.failure_class.value,
        failure_provider=failure_provider,
        failure_kind=classified.kind or None,
        opened_at=now,
        next_probe_at=now + timedelta(seconds=cooldown),
        last_probe_at=state.last_probe_at if state else None,
        last_probe_status=state.last_probe_status if state else None,
        last_probe_kind=state.last_probe_kind if state else None,
        opened_count=(state.opened_count if state else 0) + 1,
        updated_at=now,
    )
    store.save(opened)
    _log(
        "PROVIDER_FAILOVER state=OPEN "
        f"data={LEGACY_DATA_MODE} odds={LEGACY_ODDS_MODE} "
        f"reason={classified.failure_class.value} provider={failure_provider} "
        f"opened_at={now.isoformat()}"
    )


def sportradar_health_probe(
    env: Mapping[str, str],
    *,
    fetch: Callable[..., Any] | None = None,
) -> Callable[[], ProbeResult]:
    """Build the lowest-cost primary recovery probe.

    ``fetch_competitions`` is the smallest existing Sportradar resource and is
    date-independent, so a probe consumes one bounded request and never touches
    an active slate. The cooldown sequence bounds probes to one per cycle.
    """
    fetcher = fetch or fetch_competitions
    access_level = str(env.get("SPORTRADAR_TENNIS_ACCESS_LEVEL") or "trial").strip() or "trial"
    language = str(env.get("SPORTRADAR_TENNIS_LANGUAGE") or "en").strip() or "en"

    def probe() -> ProbeResult:
        key = str(env.get("SPORTRADAR_TENNIS_API_KEY") or "").strip()
        if not key:
            # Never spend a request when no credential is configured.
            key = str(load_secret("SPORTRADAR_TENNIS_API_KEY") or "").strip()
        if not key:
            return ProbeResult(ok=False, failure_kind="MISSING_KEY")
        try:
            fetcher(
                key,
                access_level=access_level,
                language=language,
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except SportradarTransportError as exc:
            return ProbeResult(ok=False, failure_kind=exc.kind)
        except Exception:  # noqa: BLE001 - probe must never crash the batch
            return ProbeResult(ok=False, failure_kind="PROBE_ERROR")
        return ProbeResult(ok=True)

    return probe


__all__ = [
    "CONFIGURED_ROLE",
    "COOLDOWN_ENV",
    "CircuitDecision",
    "CircuitState",
    "CircuitStateStore",
    "CircuitStoreUnavailable",
    "DATASET_NAME",
    "DEFAULT_COOLDOWN_SECONDS",
    "FALLBACK_PAIR",
    "FALLBACK_ROLE",
    "InMemoryCircuitStateStore",
    "PRIMARY_PAIR",
    "PRIMARY_ROLE",
    "PairReadiness",
    "PostgresCircuitStateStore",
    "ProbeResult",
    "ProviderPair",
    "SOURCE_SYSTEM",
    "STATE_CLOSED",
    "STATE_DISABLED",
    "STATE_HALF_OPEN",
    "STATE_OPEN",
    "SUPPORTED_PAIR_KEYS",
    "assert_compatible_pair",
    "assert_decision_ready",
    "circuit_is_active",
    "disabled_decision",
    "evaluate_decision_readiness",
    "pair_for_role",
    "read_cooldown_seconds",
    "record_primary_outcome",
    "resolve_provider_pair",
    "scope_for_environment",
    "sportradar_health_probe",
]
