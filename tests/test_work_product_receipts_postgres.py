"""PostgreSQL integration for the database-backed work-product receipt.

Creates only the forward-evidence tables the publisher reads, so the test is
independent of PR #394's migration history. Every count comes from real rows.
"""
from __future__ import annotations

import json
from datetime import timedelta

import psycopg
import pytest

from sports_edge_tennis.agents.agent1_surface_elo import DEFAULT_CONFIG as A1_CONFIG
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG as A2_CONFIG,
)
from sports_edge_tennis.agents.agent3_intransitivity import DEFAULT_CONFIG as A3_CONFIG
from sports_edge_tennis.manager.persistence import ensure_manager_policy_version_v1
from sports_edge_tennis.ops.monitoring import (
    OUTCOME_NO_ELIGIBLE_FIXTURES,
    OUTCOME_SUCCEEDED,
    evaluate_run,
)
from sports_edge_tennis.ops.work_product_receipts import (
    ReceiptScope,
    WorkProductReceiptPublisher,
)
from sports_edge_tennis.runner import AgentConfigRefV1, FullSlateRunRequestV1, run_full_slate

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"


def _val(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _player(conn, name):
    return _val(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,))


def _target(conn, *, start, as_of, ingest_run_id=None):
    p1, p2 = _player(conn, "Receipt One"), _player(conn, "Receipt Two")
    tournament = _val(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Receipt Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
    )
    edition = _val(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) "
        "VALUES (%s, 2026, 'Receipt Open 2026') RETURNING tournament_edition_id",
        (tournament,),
    )
    match = _val(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition,),
    )
    version = _val(
        conn,
        """
        INSERT INTO match_versions (
          match_id, player_1_id, player_2_id, match_start_at, tour_scope,
          competition_tier, surface, round_code, best_of_sets, qualifying_flag,
          match_status, source_observed_at, as_of, ingest_run_id)
        VALUES (%s,%s,%s,%s,'ATP_MAIN','ATP_250','HARD','R32',3,FALSE,
                'SCHEDULED',%s,%s,%s) RETURNING match_version_id
        """,
        (match, p1, p2, start, as_of, as_of, ingest_run_id),
    )
    return {"match_id": match, "match_version_id": version, "player_1_id": p1,
            "player_2_id": p2}


def _configs(conn):
    for code, config in ((A1, A1_CONFIG), (A2, A2_CONFIG), (A3, A3_CONFIG)):
        conn.execute(
            """
            INSERT INTO agent_config_versions (
              agent_code, config_version, model_version, feature_version,
              calibration_version, parameter_schema_version, config_payload)
            VALUES (%s,%s,%s,%s,NULL,%s,%s::jsonb)
            """,
            (code, config.config_version, config.model_version,
             config.feature_version, config.parameter_schema_version,
             json.dumps(config.to_payload())),
        )


def _request(conn, match_id, as_of):
    return FullSlateRunRequestV1(
        match_ids=(match_id,),
        as_of=as_of,
        agent_config_refs=(
            AgentConfigRefV1(A1, A1_CONFIG.config_version),
            AgentConfigRefV1(A2, A2_CONFIG.config_version),
            AgentConfigRefV1(A3, A3_CONFIG.config_version),
        ),
        manager_policy_version_id=ensure_manager_policy_version_v1(
            conn
        ).manager_policy_version_id,
    )


def _forward_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS forward_originals (
          experiment_version TEXT NOT NULL,
          match_id BIGINT NOT NULL,
          first_prediction_run_id BIGINT NOT NULL,
          payload BYTEA NOT NULL,
          payload_sha256 TEXT NOT NULL,
          issued_at TIMESTAMPTZ NOT NULL,
          PRIMARY KEY (experiment_version, match_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS forward_delivery_events (
          event_id BIGSERIAL PRIMARY KEY,
          experiment_version TEXT NOT NULL,
          match_id BIGINT NOT NULL,
          channel TEXT NOT NULL,
          state TEXT NOT NULL,
          evidence TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS forward_fixture_expectations (
          experiment_version TEXT NOT NULL,
          match_id BIGINT NOT NULL,
          expected_at TIMESTAMPTZ NOT NULL,
          source_evidence BYTEA NOT NULL,
          source_sha256 TEXT NOT NULL,
          PRIMARY KEY (experiment_version, match_id)
        )
        """
    )


def _decision(conn, *, match_id, run_id, as_of):
    return _val(
        conn,
        """
        INSERT INTO decisions (
          match_id, prediction_run_id, signal_type, decision_status, reason_code,
          market_type, selection_type, selection_side, consensus_probability,
          consensus_fair_odds, market_decimal_price, sharp_reference_price,
          price_source_system, price_bookmaker_code, devig_method,
          devig_fair_odds, edge_pct, min_acceptable_odds, active_agent_count,
          abstaining_agent_count, directional_disagreement, probability_spread,
          model_version, feature_version, as_of, prediction_locked_at)
        VALUES (%s,%s,'TYPE_A_MODEL_EDGE','BET','NONE','MATCH_WINNER','PLAYER_1',
                1,0.60,1.80,2.00,1.90,'ODDSPAPI','book-a','POWER',1.85,5.0,
                1.90,3,0,FALSE,0.02,'v1','v1',%s,%s)
        RETURNING decision_id
        """,
        (match_id, run_id, as_of, as_of),
    )


def test_publisher_verifies_real_rows_and_only_then_succeeds(db: psycopg.Connection):
    now = db.execute(
        "SELECT date_trunc('second', clock_timestamp())"
    ).fetchone()[0]
    start = now + timedelta(days=2)
    ingest = _val(
        db,
        """
        INSERT INTO ingest_runs (source_system, source_dataset, started_at,
          finished_at, status, rows_seen, rows_inserted, rows_updated,
          rows_rejected, rows_unmatched)
        VALUES ('API_TENNIS','api_tennis_scheduled_fixtures',%s,%s,'COMPLETED',
                1,1,0,0,0) RETURNING ingest_run_id
        """,
        (now - timedelta(hours=1), now - timedelta(minutes=30)),
    )
    target = _target(db, start=start, as_of=now, ingest_run_id=ingest)
    _configs(db)
    outcome = run_full_slate(db, _request(db, target["match_id"], now))
    assert outcome.runner_status == "COMPLETED"
    run_id = outcome.prediction_run_id

    decision = _decision(db, match_id=target["match_id"], run_id=run_id, as_of=now)
    odds = _val(
        db,
        """
        INSERT INTO odds_snapshots (match_id, source_system, bookmaker_code,
          market_type, selection_type, decimal_price, source_snapshot_id,
          source_observed_at, as_of)
        VALUES (%s,'ODDSPAPI','book-a','MATCH_WINNER','PLAYER_1',2.00,'snap-1',
                %s,%s) RETURNING odds_snapshot_id
        """,
        (target["match_id"], now, now),
    )
    db.execute(
        "INSERT INTO decision_odds_snapshots (decision_id, odds_snapshot_id, "
        "snapshot_role) VALUES (%s,%s,'OFFERED_SELECTION')",
        (decision, odds),
    )
    result = _val(
        db,
        """
        INSERT INTO match_result_observations (match_id, outcome_status,
          winner_side, source_observed_at, as_of, ingested_at)
        VALUES (%s,'COMPLETED',1,%s,%s,%s) RETURNING match_result_observation_id
        """,
        (target["match_id"], now + timedelta(days=1), now + timedelta(days=1),
         now + timedelta(days=1)),
    )
    db.execute(
        """
        INSERT INTO decision_settlements (decision_id, match_result_observation_id,
          settlement_status, settled_at)
        VALUES (%s,%s,'WON',%s)
        """,
        (decision, result, now + timedelta(days=1)),
    )
    _forward_tables(db)
    db.execute(
        "INSERT INTO forward_originals VALUES ('EXP-1',%s,%s,%s,%s,%s)",
        (target["match_id"], run_id, b"original", "sha", now),
    )
    db.execute(
        "INSERT INTO forward_delivery_events (experiment_version, match_id, "
        "channel, state) VALUES ('EXP-1',%s,'ARCHIVE','DELIVERED')",
        (target["match_id"],),
    )
    db.execute(
        "INSERT INTO forward_fixture_expectations VALUES ('EXP-1',%s,%s,%s,%s)",
        (target["match_id"], start, b"expected", "sha"),
    )

    cutoff = now + timedelta(days=2)
    scope = ReceiptScope(
        slot=now,
        window_start=now - timedelta(hours=2),
        window_end=cutoff,
        prediction_run_id=run_id,
        experiment_id="EXP-1",
        cutoff=cutoff,
    )
    publisher = WorkProductReceiptPublisher(db, clock=lambda: cutoff)
    collected = publisher.collect(scope)
    assert collected["success"] is True, collected["checks"]
    assert collected["record"].eligible_fixture_count == 1
    assessment = evaluate_run(
        collected["record"],
        slot=now,
        now=collected["record"].finished_at + timedelta(seconds=1),
        run_timeout=cutoff - scope.window_start,
    )
    assert assessment.outcome == OUTCOME_SUCCEEDED


def test_publisher_never_succeeds_when_a_persisted_row_is_missing(db):
    now = db.execute("SELECT date_trunc('second', clock_timestamp())").fetchone()[0]
    start = now + timedelta(days=2)
    target = _target(db, start=start, as_of=now)
    _configs(db)
    outcome = run_full_slate(db, _request(db, target["match_id"], now))
    scope = ReceiptScope(
        slot=now,
        window_start=now - timedelta(hours=1),
        window_end=now + timedelta(days=3),
        prediction_run_id=outcome.prediction_run_id,
    )
    collected = WorkProductReceiptPublisher(db, clock=lambda: now).collect(scope)
    assert collected["success"] is False
    assert "FIXTURE_DISCOVERY" in collected["missing_stages"]
    assert "BOOKMAKER_ACQUISITION" not in collected["missing_stages"]


def test_zero_fixture_run_is_success_only_with_explicit_reconciliation(db):
    now = db.execute("SELECT date_trunc('second', clock_timestamp())").fetchone()[0]
    ingest = _val(
        db,
        """
        INSERT INTO ingest_runs (source_system, source_dataset, started_at,
          finished_at, status, rows_seen, rows_inserted, rows_updated,
          rows_rejected, rows_unmatched)
        VALUES ('API_TENNIS','api_tennis_scheduled_fixtures',%s,%s,'COMPLETED',
                0,0,0,0,0) RETURNING ingest_run_id
        """,
        (now - timedelta(minutes=30), now - timedelta(minutes=10)),
    )
    assert ingest
    scope = ReceiptScope(
        slot=now,
        window_start=now - timedelta(hours=1),
        window_end=now + timedelta(hours=1),
        acknowledge_empty_discovery=True,
    )
    collected = WorkProductReceiptPublisher(db, clock=lambda: now).collect(scope)
    assert collected["success"] is True
    assert collected["record"].eligible_fixture_count == 0
    assessment = evaluate_run(
        collected["record"],
        slot=now,
        now=collected["record"].finished_at + timedelta(seconds=1),
        run_timeout=timedelta(hours=2),
    )
    assert assessment.outcome == OUTCOME_NO_ELIGIBLE_FIXTURES
