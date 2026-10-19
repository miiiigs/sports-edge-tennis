from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient

import sports_edge_tennis.dashboard.app as dashboard_app
from sports_edge_tennis.dashboard import presentation as pres
from sports_edge_tennis.dashboard.read_model import (
    LiveHealthView,
    get_history,
    get_latest_live_operational_health,
    get_run_for_date,
)
from sports_edge_tennis.live.orchestrator import ensure_agent_config_versions_v1
from sports_edge_tennis.manager.persistence import ensure_manager_policy_version_v1
from sports_edge_tennis.runner.contracts import AGENT_1, AGENT_2, AGENT_3

AGENT_ORDER = (AGENT_1, AGENT_2, AGENT_3)
AGENT_PROB_REPR = {
    AGENT_1: "agent1_raw_probability_v1",
    AGENT_2: "agent2_raw_probability_v1",
    AGENT_3: "agent3_raw_probability_v1",
}


# ---------------------------------------------------------------- presentation


def test_state_labels_are_client_friendly():
    assert pres.state_label("MODEL_CONSENSUS") == "Consensus"
    assert pres.state_label("NO_BET_MODEL") == "No Bet — Model Filter"
    assert pres.state_label("SHARP_UNAVAILABLE") == "Pinnacle Unavailable"
    assert pres.state_label("RECONCILIATION_FAILED") == "Price Match Unavailable"
    assert pres.state_label("AWAITING_OFFERED_PRICE") == "Awaiting Bookmaker Price"


def test_reason_translation():
    assert pres.reason_explanation("DIRECTIONAL_DISAGREEMENT") == "Models disagreed on the winner."
    assert pres.reason_explanation("THREE_AGENT_SPREAD_EXCEEDED") == "Model confidence was too inconsistent."
    assert pres.reason_explanation("TWO_OR_MORE_ABSTENTIONS") == "Not enough model agreement."
    assert pres.reason_explanation("NONE") is None
    assert pres.reason_explanation(None) is None


def test_client_state_requires_persisted_market_bet():
    # Consensus + sharp-only remains pending until a persisted Market decision exists.
    assert pres.client_state("CONSENSUS", True) == "AWAITING_OFFERED_PRICE"
    assert pres.client_state("CONSENSUS", False) == "SHARP_UNAVAILABLE"
    assert pres.client_state("CONSENSUS", True, "BET") == "BET"
    assert pres.client_state("CONSENSUS", True, "NO_BET") == "NO_BET_MARKET"
    # Market state can never rescue a Manager-level NO_BET.
    assert pres.client_state("NO_BET", False, "BET") == "NO_BET_MODEL"
    assert pres.client_state("NO_BET", True, "BET") == "NO_BET_MODEL"


def test_bet_terminology_is_scoped_to_market_bet_state():
    assert pres.STATE_LABELS["BET"] == "Bet"
    for state, label in pres.STATE_LABELS.items():
        if state == "BET":
            continue
        assert "BET" not in label.upper() or "NO BET" in label.upper()


def test_tier_and_percent():
    assert pres.tier_label("MASTERS_1000") == "ATP Masters 1000"
    assert pres.tier_label("CHALLENGER") == "Challenger"
    assert pres.percent(Decimal("0.683")) == "68.3%"
    assert pres.percent(None) == "—"


# ---------------------------------------------------------------- read model (DB)


def _fetchval(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_match(conn, p1_name, p2_name, tournament, tier, start):
    p1 = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (p1_name,))
    p2 = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (p2_name,))
    tid = _fetchval(conn, "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES (%s, 'ATP_MAIN', %s) RETURNING tournament_id", (tournament, tier))
    eid = _fetchval(conn, "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, start_date) VALUES (%s, 2026, %s, '2026-09-01') RETURNING tournament_edition_id", (tid, tournament))
    mid = _fetchval(conn, "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (eid,))
    observed = datetime(2026, 9, 6, 10, 0, 0, tzinfo=timezone.utc)
    _fetchval(conn, """
        INSERT INTO match_versions (match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier, round_code, best_of_sets, qualifying_flag, match_status, source_observed_at, as_of)
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', %s, 'R32', 3, FALSE, 'SCHEDULED', %s, %s)
        RETURNING match_version_id
    """, (mid, p1, p2, start, tier, observed, observed))
    return mid


def _seed_agent_configs(conn) -> dict[str, dict]:
    ensure_agent_config_versions_v1(conn)
    ensure_manager_policy_version_v1(conn)
    rows = conn.execute(
        "SELECT agent_code::text, agent_config_version_id, model_version, feature_version FROM agent_config_versions"
    ).fetchall()
    return {r[0]: {"id": r[1], "model": r[2], "feature": r[3]} for r in rows}


def _seed_run(conn, run_date, match_ids):
    as_of = datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    run_id = _fetchval(conn, """
        INSERT INTO prediction_runs (run_status, as_of, started_at, prediction_contract_version)
        VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1') RETURNING prediction_run_id
    """, (as_of, as_of))
    for mid in match_ids:
        conn.execute("INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)", (run_id, mid))
    return run_id, as_of


def _seed_predictions_and_finalize(conn, run_id, match_id, sides, probs, as_of, configs):
    with conn.transaction():
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        for agent_code, side, prob in zip(AGENT_ORDER, sides, probs):
            cfg = configs[agent_code]
            conn.execute("""
                INSERT INTO predictions (
                    match_id, prediction_run_id, agent_config_version_id, agent_code,
                    prediction_status, selection_side, win_probability, probability_representation,
                    model_version, feature_version, as_of, prediction_locked_at
                )
                VALUES (%s, %s, %s, %s, 'PREDICTED', %s, %s, %s, %s, %s, %s, %s)
            """, (match_id, run_id, cfg["id"], agent_code, side, Decimal(prob), AGENT_PROB_REPR[agent_code], cfg["model"], cfg["feature"], as_of, as_of))
        conn.execute(
            "UPDATE prediction_runs SET run_status='COMPLETED', prediction_finalized_at=%s, ended_at=%s WHERE prediction_run_id=%s",
            (as_of, as_of, run_id),
        )


def _seed_manager(conn, run_id, match_id, status, side, prob, reason, active, abstaining, spread="0.04", as_of=None):
    as_of = as_of or datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)
    conn.execute("""
        INSERT INTO manager_consensus_results (prediction_run_id, match_id, manager_policy_version_id, manager_status, selected_side, consensus_probability, active_agent_count, abstaining_agent_count, probability_spread, reason_code, as_of)
        VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (run_id, match_id, status, side, Decimal(prob) if prob else None, active, abstaining, Decimal(spread), reason, as_of))


def _seed_live_health(conn, *, status="HEALTHY", checked_at=None, freshness_state="FRESH", error_count=0):
    checked_at = checked_at or datetime(2026, 9, 18, 16, 3, 0, tzinfo=timezone.utc)
    conn.execute(
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
        VALUES (
            'SPORTS_EDGE_TENNIS',
            'LIVE_BATCH_RUNTIME',
            %s,
            %s,
            %s,
            NULL,
            0,
            %s,
            %s,
            'dashboard health test'
        )
        """,
        (checked_at, checked_at, freshness_state, error_count, status),
    )
    return checked_at


def _seed_sharp(conn, match_id):
    ts = datetime(2026, 9, 6, 13, 0, 0, tzinfo=timezone.utc)
    for sel, price in (("PLAYER_1", "1.57"), ("PLAYER_2", "2.40")):
        conn.execute("""
            INSERT INTO odds_snapshots (match_id, source_system, bookmaker_code, market_type, selection_type, decimal_price, market_status, source_snapshot_id, source_record_hash, source_observed_at, as_of, odds_origin)
            VALUES (%s, 'SHARP', 'PINNACLE', 'MATCH_WINNER', %s, %s, 'OPEN', %s, %s, %s, %s, 'HISTORICAL')
        """, (match_id, sel, price, f"snap-{sel}", f"hash-{sel}", ts, ts))


def test_get_latest_live_operational_health_returns_newest_snapshot(db: psycopg.Connection):
    older = datetime(2026, 9, 18, 15, 0, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 18, 16, 0, 0, tzinfo=timezone.utc)
    _seed_live_health(db, status="WARNING", checked_at=older)
    _seed_live_health(db, status="HEALTHY", checked_at=newer)

    health = get_latest_live_operational_health(db)

    assert health is not None
    assert health.status == "HEALTHY"
    assert health.freshness_state == "FRESH"
    assert health.checked_at == newer
    assert health.error_count == 0


def test_get_run_for_date_populated_consensus(db: psycopg.Connection):
    start = datetime(2026, 9, 7, 14, 0, 0, tzinfo=timezone.utc)
    mid = _seed_match(db, "Carlos Alcaraz", "Holger Rune", "Indian Wells", "MASTERS_1000", start)
    configs = _seed_agent_configs(db)
    run_id, as_of = _seed_run(db, date(2026, 9, 7), [mid])
    _seed_predictions_and_finalize(db, run_id, mid, [1, 1, 1], ["0.72", "0.70", "0.68"], as_of, configs)
    _seed_manager(db, run_id, mid, "CONSENSUS", 1, "0.70", "NONE", 3, 0, as_of=as_of)
    _seed_sharp(db, mid)

    state = get_run_for_date(db, date(2026, 9, 7))
    assert state.kind == "POPULATED"
    assert len(state.matches) == 1
    m = state.matches[0]
    assert m.player_1 == "Carlos Alcaraz"
    assert m.selected_player == "Carlos Alcaraz"
    assert m.consensus_probability == Decimal("0.70")
    assert m.client_state == "AWAITING_OFFERED_PRICE"
    assert m.sharp_available is True


def test_get_run_for_date_uses_target_slate_date_not_execution_date(db: psycopg.Connection):
    configs = _seed_agent_configs(db)
    sep19 = _seed_match(
        db,
        "Today Player A",
        "Today Player B",
        "Today Open",
        "CHALLENGER",
        datetime(2026, 9, 19, 14, 0, 0, tzinfo=timezone.utc),
    )
    sep20 = _seed_match(
        db,
        "Tomorrow Player A",
        "Tomorrow Player B",
        "Tomorrow Open",
        "CHALLENGER",
        datetime(2026, 9, 20, 14, 0, 0, tzinfo=timezone.utc),
    )

    today_run, today_as_of = _seed_run(db, date(2026, 9, 19), [sep19])
    _seed_predictions_and_finalize(
        db, today_run, sep19, [1, 1, 1], ["0.70", "0.68", "0.66"], today_as_of, configs
    )
    _seed_manager(
        db, today_run, sep19, "CONSENSUS", 1, "0.68", "NONE", 3, 0, as_of=today_as_of
    )

    tomorrow_run, tomorrow_as_of = _seed_run(db, date(2026, 9, 19), [sep20])
    _seed_predictions_and_finalize(
        db, tomorrow_run, sep20, [2, 2, 2], ["0.35", "0.34", "0.33"], tomorrow_as_of, configs
    )
    _seed_manager(
        db, tomorrow_run, sep20, "CONSENSUS", 2, "0.67", "NONE", 3, 0, as_of=tomorrow_as_of
    )

    assert tomorrow_run > today_run

    today_state = get_run_for_date(db, date(2026, 9, 19))
    tomorrow_state = get_run_for_date(db, date(2026, 9, 20))

    assert today_state.prediction_run_id == today_run
    assert [m.player_1 for m in today_state.matches] == ["Today Player A"]
    assert tomorrow_state.prediction_run_id == tomorrow_run
    assert [m.player_1 for m in tomorrow_state.matches] == ["Tomorrow Player A"]

    history = get_history(db)
    populated = {row.run_date: row.prediction_run_id for row in history if row.kind == "POPULATED"}
    assert populated[date(2026, 9, 19)] == today_run
    assert populated[date(2026, 9, 20)] == tomorrow_run


def test_get_run_for_date_no_bet(db: psycopg.Connection):
    start = datetime(2026, 9, 7, 15, 0, 0, tzinfo=timezone.utc)
    mid = _seed_match(db, "Andrey Rublev", "Taylor Fritz", "Phoenix", "CHALLENGER", start)
    configs = _seed_agent_configs(db)
    run_id, as_of = _seed_run(db, date(2026, 9, 7), [mid])
    _seed_predictions_and_finalize(db, run_id, mid, [1, 2, 2], ["0.66", "0.44", "0.40"], as_of, configs)
    _seed_manager(db, run_id, mid, "NO_BET", None, None, "DIRECTIONAL_DISAGREEMENT", 3, 0, spread="0.26", as_of=as_of)

    state = get_run_for_date(db, date(2026, 9, 7))
    m = state.matches[0]
    assert m.client_state == "NO_BET_MODEL"
    assert m.selected_player is None


def test_empty_slate_distinct_from_no_run(db: psycopg.Connection):
    # An API-Tennis ingest run with no prediction run => empty slate.
    db.execute("""
        INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
        VALUES ('API_TENNIS', 'api_tennis_scheduled_fixtures', %s, 'PARTIAL')
    """, (datetime(2026, 9, 7, 5, 50, 0, tzinfo=timezone.utc),))

    empty = get_run_for_date(db, date(2026, 9, 7))
    assert empty.kind == "EMPTY_SLATE"

    missing = get_run_for_date(db, date(2026, 9, 8))
    assert missing.kind == "NO_RUN"


def test_history_ordering(db: psycopg.Connection):
    start = datetime(2026, 9, 7, 14, 0, 0, tzinfo=timezone.utc)
    mid = _seed_match(db, "Jannik Sinner", "Daniil Medvedev", "Rotterdam", "ATP_500", start)
    configs = _seed_agent_configs(db)
    run_id, as_of = _seed_run(db, date(2026, 9, 7), [mid])
    _seed_predictions_and_finalize(db, run_id, mid, [1, 1, 1], ["0.63", "0.60", "0.61"], as_of, configs)
    _seed_manager(db, run_id, mid, "CONSENSUS", 1, "0.613", "NONE", 3, 0, as_of=as_of)

    history = get_history(db)
    assert any(r.kind == "POPULATED" and r.consensus_count == 1 for r in history)


# ---------------------------------------------------------------- web


@pytest.fixture
def client():
    return TestClient(dashboard_app.app)


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_today_demo_populated(client, monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    resp = client.get("/today")
    assert resp.status_code == 200
    assert "Demo data" in resp.text
    assert "Model Picks" in resp.text
    assert "Carlos Alcaraz" in resp.text
    # The demo slate evaluates five matches but only three are picks.
    assert "3 picks today" in resp.text
    assert resp.text.count('<article class="pick-card') == 3
    # Rejected matches exist only inside the collapsed reference disclosure.
    assert "Show no-bet matches" in resp.text
    assert "Andrey Rublev" in resp.text


def test_today_renders_operational_warning(client, monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    monkeypatch.setattr(
        dashboard_app,
        "_load_live_health",
        lambda: LiveHealthView(
            status="WARNING",
            freshness_state="FRESH",
            checked_at=datetime(2026, 9, 18, 16, 3, 0, tzinfo=timezone.utc),
            latest_observation_at=datetime(2026, 9, 18, 16, 2, 0, tzinfo=timezone.utc),
            error_count=0,
            notes="completed_runs=2 target_count=0 zero_volume=1",
        ),
    )

    resp = client.get("/today")

    assert resp.status_code == 200
    assert "Live data coverage or freshness requires attention." in resp.text
    assert "Today's picks may be incomplete or out of date." in resp.text
    assert "16:03 UTC" in resp.text
    # Internal operational notes must never reach the client surface.
    assert "zero_volume" not in resp.text


def test_today_db_error_safe(client, monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", False)
    monkeypatch.setattr(dashboard_app, "_connect", lambda: None)
    resp = client.get("/today")
    assert resp.status_code == 200
    assert "Picks are temporarily unavailable" in resp.text
    assert "TENNIS_DATABASE_URL" not in resp.text


def test_history_no_runs(client, monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", False)
    monkeypatch.setattr(dashboard_app, "_connect", lambda: None)
    resp = client.get("/history")
    assert resp.status_code == 200
    assert "No runs available" in resp.text


def test_invalid_date(client):
    resp = client.get("/today?date=not-a-date")
    assert resp.status_code == 400


def test_api_today_includes_structured_operational_health(client, monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    monkeypatch.setattr(
        dashboard_app,
        "_load_live_health",
        lambda: LiveHealthView(
            status="ERROR",
            freshness_state="UNKNOWN",
            checked_at=datetime(2026, 9, 18, 16, 3, 0, tzinfo=timezone.utc),
            latest_observation_at=None,
            error_count=1,
            notes="live_batch_status=2",
        ),
    )

    resp = client.get("/api/dashboard/today")

    assert resp.status_code == 200
    health = resp.json()["operational_health"]
    assert health["status"] == "ERROR"
    assert health["freshness_state"] == "UNKNOWN"
    assert health["error_count"] == 1
    assert health["latest_observation_at"] is None


def test_api_today_readonly_json(client, monkeypatch):
    monkeypatch.setattr(dashboard_app, "DEMO_MODE", True)
    resp = client.get("/api/dashboard/today")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "POPULATED"
    assert any("internal" not in str(m) for m in body["matches"])
    for m in body["matches"]:
        assert "match_id" not in m
        assert "event_key" not in m
