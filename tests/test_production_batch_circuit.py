"""End-to-end circuit-breaker simulations through the REAL production entrypoint.

Railway executes::

    python -m sports_edge_tennis.live.production_batch --days 3

These tests drive ``production_batch.main`` (not ``batch.main`` directly) with a
durable append-only fake health table so the circuit state persists across
simulated cron process restarts. No network or database is used.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sports_edge_tennis.live import batch, production_batch
from sports_edge_tennis.live import provider_circuit as pc
from sports_edge_tennis.live.orchestrator import LivePilotSportradarFailed
from sports_edge_tennis.live.provider_authority import (
    ProviderAuthority,
    TARGET_DATA_MODE,
    TARGET_ODDS_MODE,
)

PRIMARY = ProviderAuthority(data_mode=TARGET_DATA_MODE, odds_mode=TARGET_ODDS_MODE)
DATASET = pc.PostgresCircuitStateStore.dataset_for_scope("production")


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _HealthTable:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}

    def insert(self, dataset, checked_at, status, notes):
        self.rows.setdefault(dataset, []).append(
            {"checked_at": checked_at, "status": status, "notes": notes}
        )

    def latest(self, dataset):
        rows = self.rows.get(dataset, [])
        if not rows:
            return None
        ordered = sorted(enumerate(rows), key=lambda kv: (kv[1]["checked_at"], kv[0]))
        row = ordered[-1][1]
        return (row["status"], row["notes"], row["checked_at"])

    def force_next_probe_past(self, dataset=DATASET):
        notes = json.loads(self.rows[dataset][-1]["notes"])
        notes["next_probe_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        self.rows[dataset][-1]["notes"] = json.dumps(notes)


class _CircuitConn:
    """Serves the circuit health table plus provider-readiness probe queries."""

    def __init__(self, table: _HealthTable) -> None:
        self._table = table

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split()).lower()
        if norm.startswith("insert into data_health_checks"):
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
        if norm.startswith("select status::text, notes, checked_at"):
            _source, dataset = params
            return _Cursor(self._table.latest(dataset))
        # --- provider readiness probes (provider_authority.evaluate_cutover_readiness)
        if (
            "oddspapi_request_ledger" in norm
            and "oddspapi_event_identity_cache" in norm
        ):
            return _Cursor((True, True))
        if "with latest as" in norm:
            return _Cursor((True, True))
        if "select to_regclass(%s) is not null" in norm:
            return _Cursor((True,))
        if (
            "from match_result_observations" in norm
            and "match_stat_observations" in norm
        ):
            return _Cursor((10, 10))
        if "from provider_bookmaker_mappings" in norm:
            return _Cursor((True, True))
        raise AssertionError(f"unexpected SQL: {norm}")


def _success_result(slate_date):
    return SimpleNamespace(
        slate_date=slate_date,
        status="LIVE_MVP_SUCCESS",
        fixture_count=5,
        fixture_rows_seen=5,
        fixture_rows_ingested=5,
        fixture_rows_rejected=0,
        fixture_rejection_reasons=(),
        target_count=2,
        manager_consensus_count=1,
        pinnapi_requests=0,
        prediction_run_id=None,
    )


def _install(monkeypatch, table, run_fn):
    for name, value in {
        "TENNIS_DATA_PROVIDER_MODE": TARGET_DATA_MODE,
        "TENNIS_ODDS_PROVIDER_MODE": TARGET_ODDS_MODE,
        "TENNIS_DATA_FAILOVER_AUTHORIZATION": "SPORTRADAR_DATA_ONLY_V1",
        "TENNIS_ODDS_FAILOVER_AUTHORIZATION": "ODDSPAPI_ODDS_V1",
        "SPORTRADAR_TENNIS_API_KEY": "sr-key",
        "SPORTRADAR_TENNIS_ACCESS_LEVEL": "production",
        "SPORTRADAR_TENNIS_LANGUAGE": "en",
        "ODDSPAPI_API_KEY": "od-key",
        "API_TENNIS_API_KEY": "at-key",
        "PINNAPI_KEY": "pin-key",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TENNIS_PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "3600")

    monkeypatch.setattr(production_batch, "_db_dsn", lambda: "postgresql://example")
    monkeypatch.setattr(
        production_batch, "load_provider_authority", lambda env: PRIMARY
    )
    monkeypatch.setattr(production_batch.psycopg, "connect", lambda *a, **k: _CircuitConn(table))
    monkeypatch.setattr(production_batch, "_ensure_quarter_kelly_schema", lambda conn: "EXISTING")
    monkeypatch.setattr(production_batch, "_ensure_publication_control_schema", lambda conn: "EXISTING")
    monkeypatch.setattr(production_batch, "ensure_model_identity_bridge_schema", lambda conn: "EXISTING")
    monkeypatch.setattr(production_batch, "_ensure_strict_manager_v2_schema", lambda conn: "EXISTING")
    monkeypatch.setattr(
        production_batch, "_ensure_prediction_abstention_reason_schema", lambda conn: "EXISTING"
    )
    monkeypatch.setattr(
        production_batch,
        "_ensure_tournament_identity_corrections_schema",
        lambda conn: "EXISTING",
    )
    monkeypatch.setattr(production_batch, "_ensure_oddspapi_ledger_schema", lambda conn: "READY")
    monkeypatch.setattr(production_batch, "_live_batch_gap_cycle", lambda *a, **k: None)
    monkeypatch.setattr(production_batch, "_live_operational_health_cycle", lambda *a, **k: None)
    monkeypatch.setattr(production_batch, "validation_mode_enabled", lambda: True)
    monkeypatch.setattr(
        pc, "sportradar_health_probe", lambda env, **kwargs: (lambda: pc.ProbeResult(ok=True))
    )

    # Batch internals (the real batch.main is invoked via production_batch).
    monkeypatch.setattr(batch, "_db_dsn", lambda: "postgresql://example")
    monkeypatch.setattr(batch, "load_provider_authority", lambda env: PRIMARY)
    monkeypatch.setattr(batch, "assert_cutover_ready", lambda conn, env: PRIMARY)
    monkeypatch.setattr(batch, "load_secret", lambda name: {
        "SPORTRADAR_TENNIS_API_KEY": "sr-key",
        "ODDSPAPI_API_KEY": "od-key",
        "API_TENNIS_API_KEY": "at-key",
        "PINNAPI_KEY": "pin-key",
    }.get(name))
    monkeypatch.setattr(batch, "run_live_mvp", run_fn)
    monkeypatch.setattr(batch, "ensure_pinnapi_bookmaker_mapping", lambda conn: None)
    monkeypatch.setattr(batch, "load_price_scanner_candidate_match_ids", lambda conn, **k: ())
    monkeypatch.setattr(batch, "activation_from_env", lambda: None)


def test_exact_429_incident_falls_over_on_next_cron_cycle(monkeypatch, capsys):
    table = _HealthTable()
    fallback_calls: list[dict] = []

    def failing_run(**kwargs):
        raise LivePilotSportradarFailed("429", failure_kind="HTTP_429")

    _install(monkeypatch, table, failing_run)

    # --- Cycle 1: primary configured, Sportradar 429 for the whole horizon.
    assert production_batch.main([]) == 2
    out = capsys.readouterr().out
    assert "PROVIDER_AUTHORITY_BLOCKED" not in out  # did not die at the pre-gate
    assert "LIVE_PROVIDER_AUTHORITY" in out
    assert "PROVIDER_FAILOVER state=OPEN" in out
    assert "reason=RATE_LIMIT" in out
    state = pc.PostgresCircuitStateStore(_CircuitConn(table)).load("production")
    assert state is not None and state.state == pc.STATE_OPEN

    # --- Cycle 2: fresh process invocation reads durable state, selects fallback.
    def recovering_run(**kwargs):
        fallback_calls.append(kwargs)
        return _success_result(kwargs["slate_date"].isoformat())

    monkeypatch.setattr(batch, "run_live_mvp", recovering_run)
    assert production_batch.main([]) == 0
    out2 = capsys.readouterr().out
    assert "PROVIDER_FAILOVER state=OPEN" in out2
    assert fallback_calls, "fallback batch did not run"
    assert fallback_calls[0]["api_tennis_key"] == "at-key"
    assert "fetch_fixtures" not in fallback_calls[0]
    assert fallback_calls[0].get("fixture_provider_code", "API_TENNIS") == "API_TENNIS"
    assert "circuit=OPEN" in fallback_calls[0]["provider_lineage"]


def test_recovery_probe_then_primary_on_next_process(monkeypatch, capsys):
    table = _HealthTable()
    calls: list[dict] = []

    def failing_run(**kwargs):
        raise LivePilotSportradarFailed("429", failure_kind="HTTP_429")

    _install(monkeypatch, table, failing_run)
    assert production_batch.main([]) == 2
    capsys.readouterr()

    # Cooldown elapsed with a successful lightweight probe: current cycle stays
    # on the fallback pair, state becomes HALF_OPEN.
    table.force_next_probe_past()

    def ok_run(**kwargs):
        calls.append(kwargs)
        return _success_result(kwargs["slate_date"].isoformat())

    monkeypatch.setattr(batch, "run_live_mvp", ok_run)
    assert production_batch.main([]) == 0
    out = capsys.readouterr().out
    assert "PROVIDER_RECOVERY state=HALF_OPEN" in out
    assert calls[-1]["api_tennis_key"] == "at-key"
    state = pc.PostgresCircuitStateStore(_CircuitConn(table)).load("production")
    assert state is not None and state.state == pc.STATE_HALF_OPEN

    # Next process: primary eligible again and healthy -> circuit closes.
    monkeypatch.setattr(
        pc, "sportradar_health_probe", lambda env, **kwargs: (lambda: pc.ProbeResult(ok=False))
    )
    assert production_batch.main([]) == 0
    out2 = capsys.readouterr().out
    assert "PROVIDER_CIRCUIT state=CLOSED" in out2 or "PROVIDER_RECOVERY state=CLOSED" in out2
    assert calls[-1]["api_tennis_key"] == "sr-key"
    assert calls[-1]["fixture_provider_code"] == "SPORTRADAR_V3"
    final = pc.PostgresCircuitStateStore(_CircuitConn(table)).load("production")
    assert final is not None and final.state == pc.STATE_CLOSED


def test_partial_rate_limit_opens_without_mid_cycle_switch(monkeypatch, capsys):
    table = _HealthTable()
    calls: list[dict] = []

    def partial_run(**kwargs):
        calls.append(kwargs)
        # Today's real horizon has 3 slates; the first succeeds, then 429s.
        day = kwargs["slate_date"].day
        if day == calls[0]["slate_date"].day:
            return _success_result(kwargs["slate_date"].isoformat())
        raise LivePilotSportradarFailed("429", failure_kind="HTTP_429")

    _install(monkeypatch, table, partial_run)
    rc = production_batch.main([])
    out = capsys.readouterr().out

    # The current cycle stays entirely on the primary pair; no fallback request.
    assert all("fetch_fixtures" in kwargs for kwargs in calls)
    assert all(kwargs["api_tennis_key"] == "sr-key" for kwargs in calls)
    assert "PROVIDER_FAILOVER state=OPEN" in out
    state = pc.PostgresCircuitStateStore(_CircuitConn(table)).load("production")
    assert state is not None and state.state == pc.STATE_OPEN
    assert rc in (0, 2)


def test_isolated_server_error_does_not_open_circuit(monkeypatch, capsys):
    table = _HealthTable()
    calls: list[dict] = []

    def mixed_run(**kwargs):
        calls.append(kwargs)
        day = kwargs["slate_date"].day
        if day == calls[0]["slate_date"].day:
            raise LivePilotSportradarFailed("500", failure_kind="HTTP_500")
        return _success_result(kwargs["slate_date"].isoformat())

    _install(monkeypatch, table, mixed_run)
    assert production_batch.main([]) == 0
    out = capsys.readouterr().out
    assert "PROVIDER_FAILOVER state=OPEN" not in out
    assert pc.PostgresCircuitStateStore(_CircuitConn(table)).load("production") is None
