"""Real-PostgreSQL validation of the durable provider circuit store.

These tests require a migrated test database (``TEST_POSTGRES_DSN`` /
``DATABASE_URL``) and exercise the real ``data_health_checks``-backed store, not
a fake. Each test uses a unique ``TENNIS_ENVIRONMENT`` scope so runs are
isolated and environment scoping is proven. Connection-per-step models a fresh
Railway cron process.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import psycopg

from sports_edge_tennis.live import provider_circuit as pc
from sports_edge_tennis.live.provider_authority import (
    ProviderAuthority,
    TARGET_DATA_MODE,
    TARGET_ODDS_MODE,
)

PRIMARY = ProviderAuthority(data_mode=TARGET_DATA_MODE, odds_mode=TARGET_ODDS_MODE)
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _scope() -> str:
    return f"staging-{uuid.uuid4().hex[:8]}"


def _env(scope: str) -> dict[str, str]:
    return {"TENNIS_ENVIRONMENT": scope}


def _parse_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value)


def _rows(dsn: str, scope: str) -> list[dict]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        raw = conn.execute(
            """
            SELECT data_health_check_id, checked_at, status, notes
            FROM data_health_checks
            WHERE source_system = %s
              AND dataset_name = %s
            ORDER BY data_health_check_id
            """,
            (pc.SOURCE_SYSTEM, pc.PostgresCircuitStateStore.dataset_for_scope(scope)),
        ).fetchall()
    rows: list[dict] = []
    for _row_id, checked_at, status, notes in raw:
        payload = json.loads(notes)
        rows.append(
            {
                "state": payload.get("state"),
                "opened_at": _parse_dt(payload.get("opened_at")),
                "failure_class": payload.get("failure_class"),
                "failure_kind": payload.get("failure_kind"),
                "opened_count": int(payload.get("opened_count") or 0),
                "next_probe_at": _parse_dt(payload.get("next_probe_at")),
                "last_probe_status": payload.get("last_probe_status"),
                "checked_at": checked_at,
                "status": status,
            }
        )
    return rows


def test_429_opens_and_fresh_process_reads_fallback(migrated_postgres) -> None:
    dsn = migrated_postgres
    scope = _scope()
    env = _env(scope)

    # Process 1: primary configured, HTTP 429 for the whole horizon.
    with psycopg.connect(dsn, autocommit=True) as conn:
        pc.record_primary_outcome(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            now=NOW,
            success=False,
            failure_kind="HTTP_429",
            env=env,
        )

    rows = _rows(dsn, scope)
    assert [r["state"] for r in rows] == [pc.STATE_OPEN]
    assert rows[0]["failure_class"] == "RATE_LIMIT"
    assert rows[0]["failure_kind"] == "HTTP_429"
    assert rows[0]["opened_count"] == 1
    assert rows[0]["opened_at"] == NOW
    assert rows[0]["next_probe_at"] == NOW + timedelta(seconds=pc.DEFAULT_COOLDOWN_SECONDS)

    # Process 2: fresh connection, inside cooldown -> fallback, no probe.
    with psycopg.connect(dsn, autocommit=True) as conn:
        decision = pc.resolve_provider_pair(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            env=env,
            now=NOW + timedelta(seconds=30),
            probe=lambda: (_ for _ in ()).throw(
                AssertionError("probe must not run during cooldown")
            ),
        )
    assert decision.state == pc.STATE_OPEN
    assert decision.pair.key == pc.FALLBACK_PAIR.key
    assert decision.authority.data_mode == "API_TENNIS"
    assert decision.authority.odds_mode == "LEGACY_SPLIT"
    assert decision.failure_class == "RATE_LIMIT"
    assert decision.opened_at == NOW
    assert "circuit=OPEN" in decision.lineage()


def test_cooldown_half_open_recovery_closed(migrated_postgres) -> None:
    dsn = migrated_postgres
    scope = _scope()
    env = _env(scope)

    with psycopg.connect(dsn, autocommit=True) as conn:
        pc.record_primary_outcome(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            now=NOW,
            success=False,
            failure_kind="HTTP_429",
            env=env,
        )

    # Cooldown elapsed -> one bounded probe succeeds -> HALF_OPEN, fallback cycle.
    with psycopg.connect(dsn, autocommit=True) as conn:
        decision = pc.resolve_provider_pair(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            env=env,
            now=NOW + timedelta(seconds=pc.DEFAULT_COOLDOWN_SECONDS),
            probe=lambda: pc.ProbeResult(ok=True),
        )
    assert decision.state == pc.STATE_HALF_OPEN
    assert decision.pair.key == pc.FALLBACK_PAIR.key
    assert decision.probe_status == "SUCCESS"
    assert [r["state"] for r in _rows(dsn, scope)][-1] == pc.STATE_HALF_OPEN

    # Fresh process reads HALF_OPEN -> primary eligible for this next cycle.
    with psycopg.connect(dsn, autocommit=True) as conn:
        decision = pc.resolve_provider_pair(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            env=env,
            now=NOW + timedelta(seconds=pc.DEFAULT_COOLDOWN_SECONDS + 60),
        )
    assert decision.state == pc.STATE_HALF_OPEN
    assert decision.pair.key == pc.PRIMARY_PAIR.key

    # Healthy primary cycle closes the circuit; a fresh process reads CLOSED.
    with psycopg.connect(dsn, autocommit=True) as conn:
        pc.record_primary_outcome(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            now=NOW + timedelta(seconds=pc.DEFAULT_COOLDOWN_SECONDS + 60),
            success=True,
            env=env,
        )
    assert [r["state"] for r in _rows(dsn, scope)][-1] == pc.STATE_CLOSED

    with psycopg.connect(dsn, autocommit=True) as conn:
        decision = pc.resolve_provider_pair(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            env=env,
            now=NOW + timedelta(seconds=pc.DEFAULT_COOLDOWN_SECONDS + 120),
        )
    assert decision.state == pc.STATE_CLOSED
    assert decision.pair.key == pc.PRIMARY_PAIR.key


def test_environment_scoping_isolates_state(migrated_postgres) -> None:
    dsn = migrated_postgres
    prod_scope = _scope()
    staging_scope = _scope()

    with psycopg.connect(dsn, autocommit=True) as conn:
        pc.record_primary_outcome(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            now=NOW,
            success=False,
            failure_kind="HTTP_429",
            env=_env(prod_scope),
        )

    with psycopg.connect(dsn, autocommit=True) as conn:
        prod = pc.resolve_provider_pair(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            env=_env(prod_scope),
            now=NOW,
        )
        staging = pc.resolve_provider_pair(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            env=_env(staging_scope),
            now=NOW,
        )
    assert prod.state == pc.STATE_OPEN
    assert staging.state == pc.STATE_CLOSED
    assert staging.pair.key == pc.PRIMARY_PAIR.key
    assert _rows(dsn, staging_scope) == []


def test_repeated_transitions_are_idempotent(migrated_postgres) -> None:
    dsn = migrated_postgres
    scope = _scope()
    env = _env(scope)

    with psycopg.connect(dsn, autocommit=True) as conn:
        store = pc.PostgresCircuitStateStore(conn)
        for _ in range(3):
            pc.record_primary_outcome(
                store=store,
                configured=PRIMARY,
                now=NOW,
                success=False,
                failure_kind="HTTP_503",
                env=env,
            )

    rows = _rows(dsn, scope)
    assert [r["state"] for r in rows] == [pc.STATE_OPEN]
    assert rows[0]["opened_count"] == 1


def test_corrupt_state_row_fails_safe(migrated_postgres) -> None:
    dsn = migrated_postgres
    scope = _scope()
    env = _env(scope)
    dataset = pc.PostgresCircuitStateStore.dataset_for_scope(scope)

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO data_health_checks (
                source_system, dataset_name, checked_at, latest_observation_at,
                freshness_state, coverage_ratio, unmatched_count, error_count,
                status, notes
            )
            VALUES (%s, %s, %s, NULL, 'UNKNOWN', NULL, 0, 1, 'ERROR', %s)
            """,
            (pc.SOURCE_SYSTEM, dataset, NOW, "{not-valid-json"),
        )

    with psycopg.connect(dsn, autocommit=True) as conn:
        store = pc.PostgresCircuitStateStore(conn)
        assert store.load(scope) is None  # corrupt row ignored
        decision = pc.resolve_provider_pair(
            store=store, configured=PRIMARY, env=env, now=NOW
        )
    assert decision.state == pc.STATE_CLOSED
    assert decision.pair.key == pc.PRIMARY_PAIR.key


def test_append_only_store_rows_are_sanitized(migrated_postgres) -> None:
    dsn = migrated_postgres
    scope = _scope()
    env = _env(scope)

    with psycopg.connect(dsn, autocommit=True) as conn:
        pc.record_primary_outcome(
            store=pc.PostgresCircuitStateStore(conn),
            configured=PRIMARY,
            now=NOW,
            success=False,
            failure_kind="HTTP_429",
            env=env,
        )

    with psycopg.connect(dsn, autocommit=True) as conn:
        notes = conn.execute(
            "SELECT notes FROM data_health_checks WHERE dataset_name = %s",
            (pc.PostgresCircuitStateStore.dataset_for_scope(scope),),
        ).fetchone()[0]
    payload = json.loads(notes)
    assert "api_key" not in notes.lower()
    assert set(payload) >= {
        "state",
        "failure_class",
        "failure_kind",
        "opened_at",
        "opened_count",
    }
    assert payload["state"] == pc.STATE_OPEN
