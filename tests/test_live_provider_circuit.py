"""End-to-end simulations of automatic provider failover in the live batch.

These tests drive ``batch.main`` with injected providers and a durable,
append-only fake health table so the circuit persists across simulated Railway
cron process restarts. No network or database is used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from sports_edge_tennis.live import batch
from sports_edge_tennis.live import provider_circuit as pc
from sports_edge_tennis.live.orchestrator import (
    LivePilotApiTennisFailed,
    LivePilotSportradarFailed,
)
from sports_edge_tennis.live.provider_authority import (
    ProviderAuthority,
    TARGET_DATA_MODE,
    TARGET_ODDS_MODE,
)

PRIMARY = ProviderAuthority(data_mode=TARGET_DATA_MODE, odds_mode=TARGET_ODDS_MODE)
SECRETS = {
    "SPORTRADAR_TENNIS_API_KEY": "sr-key",
    "ODDSPAPI_API_KEY": "od-key",
    "API_TENNIS_API_KEY": "at-key",
    "PINNAPI_KEY": "pin-key",
}


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


class _SharedConn:
    """Serves only the append-only circuit health queries."""

    def __init__(self, table: _HealthTable) -> None:
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
    monkeypatch.setattr(batch, "load_provider_authority", lambda env: PRIMARY)
    monkeypatch.setattr(batch, "assert_cutover_ready", lambda conn, env: PRIMARY)
    monkeypatch.setattr(batch, "load_secret", lambda name: SECRETS.get(name))
    monkeypatch.setattr(batch, "run_live_mvp", run_fn)
    monkeypatch.setattr(batch, "ensure_pinnapi_bookmaker_mapping", lambda conn: None)
    monkeypatch.setattr(
        batch, "load_price_scanner_candidate_match_ids", lambda conn, **kwargs: ()
    )
    monkeypatch.setattr(batch, "activation_from_env", lambda: None)
    monkeypatch.setattr(batch.psycopg, "connect", lambda *a, **k: _SharedConn(table))
    monkeypatch.setattr(
        pc, "sportradar_health_probe", lambda env, **kwargs: (lambda: pc.ProbeResult(ok=False))
    )


def _run(capsys):
    return batch.main(
        ["--start-date", "2026-09-27", "--days", "1", "--dsn", "postgresql://example"]
    )


def test_primary_healthy_uses_primary_pair(monkeypatch, capsys):
    table = _HealthTable()
    calls: list[dict] = []

    def run_fn(**kwargs):
        calls.append(kwargs)
        return _success_result(kwargs["slate_date"].isoformat())

    _install(monkeypatch, table, run_fn)
    assert _run(capsys) == 0
    out = capsys.readouterr().out
    assert "PROVIDER_CIRCUIT state=CLOSED" in out
    assert "PROVIDER_FAILOVER" not in out
    assert calls[0]["api_tennis_key"] == "sr-key"
    assert calls[0]["fetch_fixtures"] is batch.fetch_sportradar_fixture_rows
    assert calls[0]["fixture_provider_code"] == "SPORTRADAR_V3"
    assert calls[0]["pinnapi_key"] is None


def test_rate_limit_opens_circuit_then_next_cycle_falls_back(monkeypatch, capsys):
    table = _HealthTable()
    calls: list[dict] = []

    def failing_run(**kwargs):
        raise LivePilotSportradarFailed("429", failure_kind="HTTP_429")

    _install(monkeypatch, table, failing_run)
    assert _run(capsys) == 2
    out = capsys.readouterr().out
    assert "LIVE_BATCH_FAILED_NO_SUCCESSFUL_SLATES" in out
    assert "PROVIDER_FAILOVER state=OPEN" in out
    assert "reason=RATE_LIMIT" in out

    # Next simulated cron invocation: circuit is OPEN, fallback pair runs.
    def recovering_run(**kwargs):
        calls.append(kwargs)
        return _success_result(kwargs["slate_date"].isoformat())

    monkeypatch.setattr(batch, "run_live_mvp", recovering_run)
    assert _run(capsys) == 0
    out2 = capsys.readouterr().out
    assert "PROVIDER_FAILOVER state=OPEN" in out2
    assert calls[0]["api_tennis_key"] == "at-key"
    assert calls[0]["pinnapi_key"] == "pin-key"
    assert "fetch_fixtures" not in calls[0]
    assert calls[0]["provider_lineage"].find("circuit=OPEN") >= 0


def test_auth_failure_fails_closed_keeps_primary_next_cycle(monkeypatch, capsys):
    table = _HealthTable()
    calls: list[dict] = []

    def failing_run(**kwargs):
        raise LivePilotSportradarFailed("401", failure_kind="HTTP_401")

    _install(monkeypatch, table, failing_run)
    assert _run(capsys) == 2
    out = capsys.readouterr().out
    assert "PROVIDER_CIRCUIT_FAIL_CLOSED" in out
    assert "PROVIDER_FAILOVER state=OPEN" not in out

    def ok_run(**kwargs):
        calls.append(kwargs)
        return _success_result(kwargs["slate_date"].isoformat())

    monkeypatch.setattr(batch, "run_live_mvp", ok_run)
    assert _run(capsys) == 0
    assert calls[0]["fetch_fixtures"] is batch.fetch_sportradar_fixture_rows
    assert calls[0]["fixture_provider_code"] == "SPORTRADAR_V3"


def test_malformed_payload_fails_closed(monkeypatch, capsys):
    table = _HealthTable()

    def failing_run(**kwargs):
        raise LivePilotSportradarFailed("parse", failure_kind="PARSE")

    _install(monkeypatch, table, failing_run)
    assert _run(capsys) == 2
    out = capsys.readouterr().out
    assert "PROVIDER_CIRCUIT_FAIL_CLOSED" in out
    assert "class=CONTRACT" in out
    assert "PROVIDER_FAILOVER state=OPEN" not in out


def test_fallback_failure_reports_unavailable_and_stays_open(monkeypatch, capsys):
    table = _HealthTable()
    # Prime the circuit as OPEN from a prior 500-failure cycle.
    pc.record_primary_outcome(
        store=pc.PostgresCircuitStateStore(_SharedConn(table)),
        configured=PRIMARY,
        now=datetime.now(timezone.utc),
        success=False,
        failure_kind="HTTP_500",
    )

    def failing_run(**kwargs):
        raise LivePilotApiTennisFailed("fallback down", failure_kind="HTTP_503")

    _install(monkeypatch, table, failing_run)
    assert _run(capsys) == 2
    out = capsys.readouterr().out
    assert "LIVE_BATCH_FAILED_NO_SUCCESSFUL_SLATES" in out
    assert "PROVIDER_FAILOVER state=OPEN" in out
    # No fabricated success and the circuit is still open for the next cycle.
    state = pc.PostgresCircuitStateStore(_SharedConn(table)).load("production")
    assert state is not None and state.state == pc.STATE_OPEN
