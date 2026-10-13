from __future__ import annotations

from types import SimpleNamespace

from datetime import datetime, timedelta, timezone

import pytest

from sports_edge_tennis.live import provider_circuit as pc
from sports_edge_tennis.live.provider_authority import (
    LEGACY_DATA_MODE,
    LEGACY_ODDS_MODE,
    ProviderAuthority,
    TARGET_DATA_MODE,
    TARGET_ODDS_MODE,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
PRIMARY = ProviderAuthority(data_mode=TARGET_DATA_MODE, odds_mode=TARGET_ODDS_MODE)
LEGACY = ProviderAuthority(data_mode=LEGACY_DATA_MODE, odds_mode=LEGACY_ODDS_MODE)
DATA_ONLY = ProviderAuthority(data_mode=TARGET_DATA_MODE, odds_mode=LEGACY_ODDS_MODE)


def _resolve(store, *, configured=PRIMARY, now=NOW, probe=None, env=None):
    return pc.resolve_provider_pair(
        store=store,
        configured=configured,
        env=env if env is not None else {},
        now=now,
        probe=probe,
    )


def _record(store, *, success, failure_kind=None, now=NOW, configured=PRIMARY, env=None):
    pc.record_primary_outcome(
        store=store,
        configured=configured,
        now=now,
        success=success,
        failure_kind=failure_kind,
        env=env if env is not None else {},
    )


def test_no_state_is_closed_primary() -> None:
    decision = _resolve(pc.InMemoryCircuitStateStore())
    assert decision.state == pc.STATE_CLOSED
    assert decision.pair.key == pc.PRIMARY_PAIR.key
    assert decision.authority.data_mode == TARGET_DATA_MODE
    assert decision.authority.odds_mode == TARGET_ODDS_MODE


@pytest.mark.parametrize("kind", ["HTTP_429", "TIMEOUT", "NETWORK", "HTTP_500"])
def test_eligible_failure_opens_and_next_cycle_falls_back(kind: str) -> None:
    store = pc.InMemoryCircuitStateStore()
    _record(store, success=False, failure_kind=kind)

    # Same instant, well inside the cooldown: fallback, no probe required.
    def forbidden_probe():
        raise AssertionError("primary must not be probed during cooldown")

    decision = _resolve(store, now=NOW + timedelta(seconds=30), probe=forbidden_probe)
    assert decision.state == pc.STATE_OPEN
    assert decision.pair.key == pc.FALLBACK_PAIR.key
    assert decision.authority.data_mode == LEGACY_DATA_MODE
    assert decision.authority.odds_mode == LEGACY_ODDS_MODE


def test_auth_failure_fails_closed_and_never_opens() -> None:
    store = pc.InMemoryCircuitStateStore()
    _record(store, success=False, failure_kind="HTTP_401")
    assert store.load("production") is None
    decision = _resolve(store)
    assert decision.state == pc.STATE_CLOSED
    assert decision.pair.key == pc.PRIMARY_PAIR.key


def test_contract_failure_fails_closed_and_never_opens() -> None:
    store = pc.InMemoryCircuitStateStore()
    _record(store, success=False, failure_kind="PARSE")
    assert store.load("production") is None


def test_probe_success_marks_half_open_without_mid_cycle_switch() -> None:
    store = pc.InMemoryCircuitStateStore()
    opened_at = NOW
    _record(store, success=False, failure_kind="HTTP_429", now=opened_at)

    # Cooldown elapsed: exactly one bounded probe.
    probe_calls: list[int] = []

    def probe():
        probe_calls.append(1)
        return pc.ProbeResult(ok=True)

    recovery = _resolve(store, now=opened_at + timedelta(seconds=3600), probe=probe)
    assert probe_calls == [1]
    # The current cycle stays on the fallback pair.
    assert recovery.state == pc.STATE_HALF_OPEN
    assert recovery.pair.key == pc.FALLBACK_PAIR.key
    assert recovery.probe_status == "SUCCESS"

    state = store.load("production")
    assert state is not None and state.state == pc.STATE_HALF_OPEN

    # The next atomic cycle selects the primary again.
    next_cycle = _resolve(store, now=opened_at + timedelta(seconds=3660))
    assert next_cycle.state == pc.STATE_HALF_OPEN
    assert next_cycle.pair.key == pc.PRIMARY_PAIR.key

    # A successful primary cycle closes the circuit.
    _record(store, success=True, now=opened_at + timedelta(seconds=3660))
    final_state = store.load("production")
    assert final_state is not None and final_state.state == pc.STATE_CLOSED


def test_probe_failure_keeps_open_and_bounds_further_probes() -> None:
    store = pc.InMemoryCircuitStateStore()
    opened_at = NOW
    _record(store, success=False, failure_kind="HTTP_429", now=opened_at)

    probe_calls: list[int] = []

    def probe():
        probe_calls.append(1)
        return pc.ProbeResult(ok=False, failure_kind="HTTP_429")

    after_cooldown = opened_at + timedelta(seconds=3600)
    first = _resolve(store, now=after_cooldown, probe=probe)
    assert first.pair.key == pc.FALLBACK_PAIR.key
    assert first.probe_status == "FAILED"
    assert probe_calls == [1]

    # A second resolution moments later must not probe again.
    second = _resolve(store, now=after_cooldown + timedelta(seconds=5), probe=probe)
    assert second.pair.key == pc.FALLBACK_PAIR.key
    assert probe_calls == [1]


def test_record_failure_is_idempotent_while_open() -> None:
    store = pc.InMemoryCircuitStateStore()
    _record(store, success=False, failure_kind="HTTP_429")
    state = store.load("production")
    assert state is not None
    _record(store, success=False, failure_kind="HTTP_429")
    again = store.load("production")
    assert again is not None
    assert again.opened_count == state.opened_count == 1


def test_success_without_half_open_does_not_change_state() -> None:
    store = pc.InMemoryCircuitStateStore()
    _record(store, success=True)
    assert store.load("production") is None


def test_fallback_success_does_not_close_an_open_circuit() -> None:
    store = pc.InMemoryCircuitStateStore()
    _record(store, success=False, failure_kind="HTTP_503")
    # Fallback cycles never record a primary success.
    decision = _resolve(store, now=NOW + timedelta(seconds=60))
    assert decision.pair.key == pc.FALLBACK_PAIR.key
    state = store.load("production")
    assert state is not None and state.state == pc.STATE_OPEN


def test_configured_legacy_pair_disables_circuit() -> None:
    decision = _resolve(pc.InMemoryCircuitStateStore(), configured=LEGACY)
    assert decision.active is False
    assert decision.state == pc.STATE_DISABLED
    assert decision.authority.data_mode == LEGACY_DATA_MODE


def test_data_only_authority_disables_circuit() -> None:
    decision = _resolve(pc.InMemoryCircuitStateStore(), configured=DATA_ONLY)
    assert decision.active is False
    assert decision.authority.data_mode == TARGET_DATA_MODE
    assert decision.authority.odds_mode == LEGACY_ODDS_MODE


def test_cooldown_seconds_env_override() -> None:
    assert pc.read_cooldown_seconds({}) == pc.DEFAULT_COOLDOWN_SECONDS
    assert pc.read_cooldown_seconds({pc.COOLDOWN_ENV: "120"}) == 120
    assert pc.read_cooldown_seconds({pc.COOLDOWN_ENV: "0"}) == pc.DEFAULT_COOLDOWN_SECONDS
    assert pc.read_cooldown_seconds({pc.COOLDOWN_ENV: "nope"}) == pc.DEFAULT_COOLDOWN_SECONDS


def test_supported_pairs_and_incompatible_pairs_rejected() -> None:
    assert pc.assert_compatible_pair(TARGET_DATA_MODE, TARGET_ODDS_MODE).role == "PRIMARY"
    assert pc.assert_compatible_pair(LEGACY_DATA_MODE, LEGACY_ODDS_MODE).role == "FALLBACK"
    # API-Tennis + OddsPapi is a configured pair (authorization is enforced by
    # provider_authority); Sportradar fixtures + legacy odds stays rejected here.
    assert pc.assert_compatible_pair(LEGACY_DATA_MODE, TARGET_ODDS_MODE).role == "CONFIGURED"
    with pytest.raises(ValueError, match="incompatible provider pair"):
        pc.assert_compatible_pair(TARGET_DATA_MODE, LEGACY_ODDS_MODE)
    with pytest.raises(ValueError, match="incompatible provider pair"):
        pc.assert_compatible_pair("GOALSERVE", TARGET_ODDS_MODE)


def test_api_tennis_oddspapi_pair_never_activates_circuit_or_failover() -> None:
    configured = SimpleNamespace(data_mode=LEGACY_DATA_MODE, odds_mode=TARGET_ODDS_MODE)
    assert pc.circuit_is_active(configured) is False
    decision = pc.disabled_decision(configured)
    assert decision.active is False
    assert decision.reason == pc.ODDS_ONLY_NO_FAILOVER
    assert decision.authority.odds_mode == TARGET_ODDS_MODE
    assert "reason=ODDS_ONLY_NO_FAILOVER" in decision.lineage()
    assert "reason=ODDS_ONLY_NO_FAILOVER" in decision.telemetry()
    legacy = pc.disabled_decision(
        SimpleNamespace(data_mode=LEGACY_DATA_MODE, odds_mode=LEGACY_ODDS_MODE)
    )
    assert legacy.reason == "NOT_PRIMARY_AUTHORITY"


def test_lineage_and_telemetry_never_contain_secrets() -> None:
    decision = _resolve(pc.InMemoryCircuitStateStore())
    secret = "sr-live-super-secret-key"
    assert secret not in decision.lineage()
    assert secret not in decision.telemetry()
    assert "SPORTRADAR_V3" in decision.telemetry()
    assert "ODDSPAPI_TENNIS" in decision.telemetry()


def test_lineage_reports_failover_provenance() -> None:
    store = pc.InMemoryCircuitStateStore()
    _record(store, success=False, failure_kind="HTTP_429")
    decision = _resolve(store, now=NOW + timedelta(seconds=10))
    lineage = decision.lineage()
    assert "circuit=OPEN" in lineage
    assert "reason=RATE_LIMIT" in lineage
    assert "data=API_TENNIS" in lineage
    assert "odds=LEGACY_SPLIT" in lineage


def test_primary_failure_after_probe_recovery_reopens_circuit() -> None:
    store = pc.InMemoryCircuitStateStore()
    opened_at = NOW
    _record(store, success=False, failure_kind="HTTP_429", now=opened_at)
    _resolve(
        store,
        now=opened_at + timedelta(seconds=3600),
        probe=lambda: pc.ProbeResult(ok=True),
    )
    # Primary is eligible and active on the next cycle...
    assert _resolve(store, now=opened_at + timedelta(seconds=3660)).pair.key == pc.PRIMARY_PAIR.key
    # ...but it fails again, so the circuit re-opens instead of flipping back.
    _record(store, success=False, failure_kind="HTTP_429", now=opened_at + timedelta(seconds=3660))
    state = store.load("production")
    assert state is not None and state.state == pc.STATE_OPEN


def test_summary_artifact_carries_provider_lineage() -> None:
    from sports_edge_tennis.live.summary import build_live_summary

    summary = build_live_summary(
        run_id="r",
        slate_date="2026-09-27",
        prediction_as_of=NOW,
        fixture_count=1,
        fixture_reconciliation={},
        target_count=1,
        manager_consensus_count=0,
        pinnapi_requests=0,
        matches=[],
        provider_lineage="data=API_TENNIS odds=LEGACY_SPLIT circuit=OPEN reason=RATE_LIMIT",
    )
    assert "circuit=OPEN" in summary["provider_lineage"]


def test_fallback_readiness_requires_fallback_credentials(monkeypatch) -> None:
    monkeypatch.setattr(pc, "load_secret", lambda name: None)
    decision = pc.CircuitDecision(
        pair=pc.FALLBACK_PAIR,
        state=pc.STATE_OPEN,
        reason="RATE_LIMIT",
        active=True,
        scope="production",
    )
    readiness = pc.evaluate_decision_readiness(None, decision=decision, env={})
    assert readiness.ready is False
    assert "MISSING_ENV:API_TENNIS_API_KEY" in readiness.blockers
    assert "MISSING_ENV:PINNAPI_KEY" in readiness.blockers
    with pytest.raises(RuntimeError, match="TENNIS_PROVIDER_PAIR_BLOCKED"):
        pc.assert_decision_ready(None, decision=decision, env={})


def test_fallback_readiness_ready_with_credentials(monkeypatch) -> None:
    monkeypatch.setattr(pc, "load_secret", lambda name: None)
    decision = pc.CircuitDecision(
        pair=pc.FALLBACK_PAIR,
        state=pc.STATE_OPEN,
        reason="RATE_LIMIT",
        active=True,
        scope="production",
    )
    readiness = pc.evaluate_decision_readiness(
        None,
        decision=decision,
        env={"API_TENNIS_API_KEY": "a", "PINNAPI_KEY": "p"},
    )
    assert readiness.ready is True
    assert readiness.blockers == ()


def test_disabled_decision_readiness_uses_configured_env() -> None:
    decision = pc.disabled_decision(LEGACY)
    readiness = pc.evaluate_decision_readiness(None, decision=decision, env={})
    assert readiness.ready is True


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeHealthTable:
    """Append-only table shim shared across store instances."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}

    def insert(self, dataset: str, checked_at, status, notes) -> None:
        self.rows.setdefault(dataset, []).append(
            {"checked_at": checked_at, "status": status, "notes": notes}
        )

    def latest(self, dataset: str):
        rows = self.rows.get(dataset, [])
        if not rows:
            return None
        ordered = sorted(enumerate(rows), key=lambda item: (item[1]["checked_at"], item[0]))
        row = ordered[-1][1]
        return (row["status"], row["notes"], row["checked_at"])


class _FakeHealthConn:
    def __init__(self, table: _FakeHealthTable) -> None:
        self._table = table

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("insert into data_health_checks"):
            (
                _source,
                dataset,
                checked_at,
                _latest_obs,
                _freshness,
                _error_count,
                status,
                notes,
            ) = params
            self._table.insert(dataset, checked_at, status, notes)
            return _Cursor(None)
        if normalized.startswith("select status::text, notes, checked_at"):
            _source, dataset = params
            return _Cursor(self._table.latest(dataset))
        raise AssertionError(f"unexpected SQL: {normalized}")


def test_state_survives_process_restart_via_durable_store() -> None:
    table = _FakeHealthTable()

    # First process: primary fails with a 429 and opens the circuit.
    first = pc.PostgresCircuitStateStore(_FakeHealthConn(table))
    pc.record_primary_outcome(
        store=first, configured=PRIMARY, now=NOW, success=False, failure_kind="HTTP_429"
    )
    assert table.latest(pc.PostgresCircuitStateStore.dataset_for_scope("production"))

    # Second process (fresh store object, same durable table): still OPEN.
    second = pc.PostgresCircuitStateStore(_FakeHealthConn(table))
    decision = pc.resolve_provider_pair(
        store=second,
        configured=PRIMARY,
        env={},
        now=NOW + timedelta(seconds=60),
    )
    assert decision.state == pc.STATE_OPEN
    assert decision.pair.key == pc.FALLBACK_PAIR.key


def test_source_scope_isolated_by_environment() -> None:
    table = _FakeHealthTable()
    store = pc.PostgresCircuitStateStore(_FakeHealthConn(table))
    pc.record_primary_outcome(
        store=store,
        configured=PRIMARY,
        now=NOW,
        success=False,
        failure_kind="HTTP_429",
        env={"TENNIS_ENVIRONMENT": "production"},
    )
    prod = pc.resolve_provider_pair(
        store=store, configured=PRIMARY, env={"TENNIS_ENVIRONMENT": "production"}, now=NOW
    )
    staging = pc.resolve_provider_pair(
        store=store, configured=PRIMARY, env={"TENNIS_ENVIRONMENT": "staging"}, now=NOW
    )
    assert prod.state == pc.STATE_OPEN
    assert staging.state == pc.STATE_CLOSED


def test_postgres_store_requires_usable_connection() -> None:
    class _NoExecute:
        pass

    with pytest.raises(pc.CircuitStoreUnavailable):
        pc.PostgresCircuitStateStore(_NoExecute()).load("production")


def test_probe_spends_no_request_without_key(monkeypatch) -> None:
    calls: list[int] = []

    def fake_fetch(*args, **kwargs):
        calls.append(1)
        raise AssertionError("no request should be spent without a key")

    monkeypatch.setattr(pc, "load_secret", lambda name: None)
    probe = pc.sportradar_health_probe({}, fetch=fake_fetch)
    result = probe()
    assert result.ok is False
    assert result.failure_kind == "MISSING_KEY"
    assert calls == []


def test_probe_classifies_transport_error(monkeypatch) -> None:
    from sports_edge_tennis.live.sportradar import SportradarTransportError

    def fake_fetch(*args, **kwargs):
        raise SportradarTransportError("HTTP_429", "rate limited")

    probe = pc.sportradar_health_probe({"SPORTRADAR_TENNIS_API_KEY": "key"}, fetch=fake_fetch)
    result = probe()
    assert result.ok is False
    assert result.failure_kind == "HTTP_429"


def test_probe_success(monkeypatch) -> None:
    def fake_fetch(*args, **kwargs):
        return datetime.now(timezone.utc), {"competitions": []}

    probe = pc.sportradar_health_probe({"SPORTRADAR_TENNIS_API_KEY": "key"}, fetch=fake_fetch)
    assert probe().ok is True
