"""Real-PostgreSQL staging cycle through the exact production command shape.

Drives ``production_batch.main`` (the real Railway entrypoint) against a migrated
test database with only provider network I/O stubbed. The circuit decision,
selected-pair readiness, durable circuit store, and provider lineage are all
real. A minimal synthetic corpus is seeded because primary readiness (by design)
requires a non-empty historical corpus and OddsPapi mappings.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg

from sports_edge_tennis.live import batch, production_batch
from sports_edge_tennis.live import provider_circuit as pc
from sports_edge_tennis.live.orchestrator import LivePilotSportradarFailed


def _seed(conn) -> None:
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(minutes=5)
    tournament_id = conn.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Staging Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id"
    ).fetchone()[0]
    edition_id = conn.execute(
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, surface) "
        "VALUES (%s, 2026, 'Staging 2026', 'HARD') RETURNING tournament_edition_id",
        (tournament_id,),
    ).fetchone()[0]
    match_id = conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    player_1 = conn.execute(
        "INSERT INTO players (canonical_name, primary_tour_scope, status) "
        "VALUES ('Staging P1', 'ATP_MAIN', 'ACTIVE') RETURNING player_id"
    ).fetchone()[0]
    player_2 = conn.execute(
        "INSERT INTO players (canonical_name, primary_tour_scope, status) "
        "VALUES ('Staging P2', 'ATP_MAIN', 'ACTIVE') RETURNING player_id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO match_result_observations "
        "(match_id, outcome_status, winner_side, source_observed_at, as_of) "
        "VALUES (%s, 'COMPLETED', 1, %s, %s)",
        (match_id, earlier, now),
    )
    conn.execute(
        "INSERT INTO match_stat_observations "
        "(match_id, player_id, source_system, source_record_id, observation_kind, "
        " source_observed_at, as_of) "
        "VALUES (%s, %s, 'API_TENNIS', 'staging-stat-1', 'RAW_SOURCE', %s, %s)",
        (match_id, player_1 if player_1 else player_2, earlier, now),
    )
    conn.execute(
        "INSERT INTO provider_bookmaker_mappings "
        "(provider_code, provider_bookmaker_key, mapping_version, bookmaker_code, "
        " normalization_class, effective_from) "
        "VALUES ('ODDSPAPI', 'pinnacle+30', 1, 'PINNACLE', 'SHARP', %s)",
        (earlier,),
    )
    conn.execute(
        "INSERT INTO provider_bookmaker_mappings "
        "(provider_code, provider_bookmaker_key, mapping_version, bookmaker_code, "
        " normalization_class, effective_from) "
        "VALUES ('ODDSPAPI', 'bet365', 1, 'BET365', 'OFFERED', %s)",
        (earlier,),
    )


def _read_state(dsn: str, scope: str) -> str:
    with psycopg.connect(dsn, autocommit=True) as conn:
        state = pc.PostgresCircuitStateStore(conn).load(scope)
    assert state is not None
    return state.state


def _force_probe_past(dsn: str, scope: str) -> None:
    dataset = pc.PostgresCircuitStateStore.dataset_for_scope(scope)
    with psycopg.connect(dsn, autocommit=True) as conn:
        notes = conn.execute(
            "SELECT notes FROM data_health_checks WHERE dataset_name = %s "
            "ORDER BY checked_at DESC, data_health_check_id DESC LIMIT 1",
            (dataset,),
        ).fetchone()[0]
        payload = json.loads(notes)
        payload["next_probe_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        conn.execute(
            "INSERT INTO data_health_checks "
            "(source_system, dataset_name, checked_at, latest_observation_at, "
            " freshness_state, coverage_ratio, unmatched_count, error_count, status, notes) "
            "VALUES (%s, %s, %s, NULL, 'UNKNOWN', NULL, 0, 1, 'ERROR', %s)",
            (pc.SOURCE_SYSTEM, dataset, datetime.now(timezone.utc), json.dumps(payload)),
        )


def _success(slate_date):
    return SimpleNamespace(
        slate_date=slate_date,
        status="LIVE_MVP_SUCCESS",
        fixture_count=0,
        fixture_rows_seen=0,
        fixture_rows_ingested=0,
        fixture_rows_rejected=0,
        fixture_rejection_reasons=(),
        target_count=0,
        manager_consensus_count=0,
        pinnapi_requests=0,
        prediction_run_id=None,
    )


def _setenv(monkeypatch, values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_staging_production_batch_failover_then_recovery(
    migrated_postgres, monkeypatch, capsys
):
    dsn = migrated_postgres
    scope = f"staging-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("TENNIS_DATABASE_URL", dsn)
    monkeypatch.setenv("TENNIS_ENVIRONMENT", scope)
    monkeypatch.setenv("TENNIS_PROVIDER_CIRCUIT_COOLDOWN_SECONDS", "3600")
    _setenv(
        monkeypatch,
        {
            "TENNIS_DATA_PROVIDER_MODE": "SPORTRADAR_V3",
            "TENNIS_ODDS_PROVIDER_MODE": "ODDSPAPI_TENNIS",
            "TENNIS_DATA_FAILOVER_AUTHORIZATION": "SPORTRADAR_DATA_ONLY_V1",
            "TENNIS_ODDS_FAILOVER_AUTHORIZATION": "ODDSPAPI_ODDS_V1",
            "SPORTRADAR_TENNIS_API_KEY": "sr-key",
            "SPORTRADAR_TENNIS_ACCESS_LEVEL": "production",
            "SPORTRADAR_TENNIS_LANGUAGE": "en",
            "ODDSPAPI_API_KEY": "od-key",
            "API_TENNIS_API_KEY": "at-key",
            "PINNAPI_KEY": "pin-key",
        },
    )

    with psycopg.connect(dsn, autocommit=True) as conn:
        _seed(conn)

    monkeypatch.setattr(production_batch, "_live_batch_gap_cycle", lambda *a, **k: None)
    monkeypatch.setattr(production_batch, "_live_operational_health_cycle", lambda *a, **k: None)
    monkeypatch.setattr(production_batch, "validation_mode_enabled", lambda: True)
    monkeypatch.setattr(batch, "load_price_scanner_candidate_match_ids", lambda conn, **k: ())
    monkeypatch.setattr(batch, "activation_from_env", lambda: None)
    monkeypatch.setattr(batch, "ensure_pinnapi_bookmaker_mapping", lambda conn: None)

    calls: list[dict] = []

    # --- A/B: healthy primary config, Sportradar HTTP_429 for the horizon.
    monkeypatch.setattr(
        batch, "run_live_mvp",
        lambda **kw: (_ for _ in ()).throw(
            LivePilotSportradarFailed("429", failure_kind="HTTP_429")
        ),
    )
    rc_first = production_batch.main([])
    out_first = capsys.readouterr().out
    assert rc_first == 2
    assert "PROVIDER_AUTHORITY_BLOCKED" not in out_first
    assert "PROVIDER_FAILOVER state=OPEN" in out_first
    assert _read_state(dsn, scope) == pc.STATE_OPEN

    # --- C: new process; primary readiness intentionally withdrawn to prove the
    #        fallback cycle validates ONLY the fallback pair.
    monkeypatch.delenv("ODDSPAPI_API_KEY", raising=False)

    def fallback_run(**kw):
        calls.append(kw)
        return _success(kw["slate_date"].isoformat())

    monkeypatch.setattr(batch, "run_live_mvp", fallback_run)
    rc_fallback = production_batch.main([])
    out_fallback = capsys.readouterr().out
    assert rc_fallback == 0
    assert "PROVIDER_FAILOVER state=OPEN" in out_fallback
    assert calls[0]["api_tennis_key"] == "at-key"
    assert "fetch_fixtures" not in calls[0]
    assert calls[0].get("fixture_provider_code", "API_TENNIS") == "API_TENNIS"
    assert "circuit=OPEN" in calls[0]["provider_lineage"]
    assert _read_state(dsn, scope) == pc.STATE_OPEN

    # --- D: cooldown expiry + successful probe -> HALF_OPEN, fallback this cycle.
    _force_probe_past(dsn, scope)
    monkeypatch.setattr(
        pc, "sportradar_health_probe",
        lambda env, **k: (lambda: pc.ProbeResult(ok=True)),
    )
    rc_probe = production_batch.main([])
    out_probe = capsys.readouterr().out
    assert rc_probe == 0
    assert "PROVIDER_RECOVERY state=HALF_OPEN" in out_probe
    assert _read_state(dsn, scope) == pc.STATE_HALF_OPEN

    # --- E: next process selects primary, healthy cycle closes the circuit.
    monkeypatch.setenv("ODDSPAPI_API_KEY", "od-key")
    rc_primary = production_batch.main([])
    out_primary = capsys.readouterr().out
    assert rc_primary == 0
    assert calls[-1]["api_tennis_key"] == "sr-key"
    assert calls[-1]["fixture_provider_code"] == "SPORTRADAR_V3"
    assert _read_state(dsn, scope) == pc.STATE_CLOSED
    assert "PROVIDER_CIRCUIT state=CLOSED" in out_primary or "state=CLOSED" in out_primary
