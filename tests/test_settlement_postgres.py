from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.manager import (
    ensure_manager_policy_version_v1,
    evaluate_and_persist_manager_consensus,
)
from sports_edge_tennis.market import MarketDecisionV1, ensure_market_policy_version_v1
from sports_edge_tennis.market.evaluator import _persist_decision
from sports_edge_tennis.market.pricing import snapshot_lineage_hash
from sports_edge_tennis.settlement import (
    SettlementEvidenceConflict,
    classify_match_winner_settlement,
    settle_type_a_match_winner_decisions,
)

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"
AGENTS = (A1, A2, A3)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 1, hour, minute, tzinfo=timezone.utc)


def _fetchval(conn: psycopg.Connection, sql: str, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_match(conn: psycopg.Connection) -> int:
    p1 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES ('Settlement Player One') RETURNING player_id",
    )
    p2 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES ('Settlement Player Two') RETURNING player_id",
    )
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Settlement ATP', 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2026, 'Settlement ATP 2026')
        RETURNING tournament_edition_id
        """,
        (tournament_id,),
    )
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, surface, round_code,
            best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of, ingested_at
        )
        VALUES (
            %s, %s, %s, %s,
            'ATP_MAIN', 'ATP_250', 'HARD', 'R32',
            3, FALSE, 'SCHEDULED', %s, %s, %s
        )
        """,
        (match_id, p1, p2, _dt(12), _dt(8), _dt(8), _dt(8)),
    )
    return int(match_id)


def _seed_prediction_lineage(conn: psycopg.Connection, *, match_id: int) -> tuple[int, int, int]:
    run_as_of = _dt(8)
    finalized_at = _dt(8, 10)

    with conn.transaction():
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        run_id = _fetchval(
            conn,
            """
            INSERT INTO prediction_runs (
                run_status, as_of, started_at, prediction_finalized_at,
                ended_at, prediction_contract_version
            )
            VALUES ('RUNNING', %s, %s, NULL, NULL, 'predictive_agent_result_v1')
            RETURNING prediction_run_id
            """,
            (run_as_of, run_as_of),
        )
        conn.execute(
            "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
            (run_id, match_id),
        )
        for agent in AGENTS:
            config_id = _fetchval(
                conn,
                """
                INSERT INTO agent_config_versions (
                    agent_code, config_version, model_version, feature_version,
                    parameter_schema_version, config_payload
                )
                VALUES (%s, %s, 'settlement-test-model', 'settlement-test-feature',
                        'params-v1', '{"settlement_test": true}'::jsonb)
                RETURNING agent_config_version_id
                """,
                (agent, f"{agent.lower()}-settlement-test-v1"),
            )
            conn.execute(
                """
                INSERT INTO predictions (
                    match_id, prediction_run_id, agent_config_version_id, agent_code,
                    prediction_status, selection_side, win_probability,
                    model_version, feature_version, as_of, prediction_locked_at,
                    probability_representation
                )
                VALUES (
                    %s, %s, %s, %s, 'PREDICTED', 1, 0.60,
                    'settlement-test-model', 'settlement-test-feature', %s, %s, %s
                )
                """,
                (
                    match_id,
                    run_id,
                    config_id,
                    agent,
                    run_as_of,
                    finalized_at,
                    AGENT_PROBABILITY_REPRESENTATIONS[agent],
                ),
            )
        conn.execute(
            """
            UPDATE prediction_runs
            SET run_status = 'COMPLETED', prediction_finalized_at = %s, ended_at = %s
            WHERE prediction_run_id = %s
            """,
            (finalized_at, _dt(8, 11), run_id),
        )

    manager_policy = ensure_manager_policy_version_v1(conn)
    manager_outcome = evaluate_and_persist_manager_consensus(
        conn,
        prediction_run_id=run_id,
        manager_policy_version_id=manager_policy.manager_policy_version_id,
    )
    assert manager_outcome.status == "PERSISTED"
    manager_id = _fetchval(
        conn,
        """
        SELECT manager_consensus_result_id
        FROM manager_consensus_results
        WHERE prediction_run_id = %s AND match_id = %s
        """,
        (run_id, match_id),
    )
    market_policy = ensure_market_policy_version_v1(conn)
    return int(run_id), int(manager_id), int(market_policy.market_policy_version_id)


def _seed_bet(conn: psycopg.Connection, *, match_id: int) -> int:
    run_id, manager_id, market_policy_id = _seed_prediction_lineage(conn, match_id=match_id)
    decision = MarketDecisionV1(
        manager_consensus_result_id=manager_id,
        prediction_run_id=run_id,
        match_id=match_id,
        market_policy_version_id=market_policy_id,
        decision_as_of=_dt(9),
        decision_status="BET",
        reason_code="NONE",
        selected_side=1,
        consensus_probability=Decimal("0.60000000"),
        edge_pct=Decimal("0.20000000"),
        min_acceptable_odds=Decimal("1.800000"),
        market_decimal_price=Decimal("2.000000"),
        sharp_reference_price=Decimal("1.900000"),
        price_source_system="OFFERED",
        price_bookmaker_code="TEST_BOOK",
        devig_method="POWER",
        devig_fair_odds=Decimal("1.900000"),
        active_agent_count=3,
        abstaining_agent_count=0,
        probability_spread=Decimal("0.00000000"),
        lineage=(),
        snapshot_lineage_hash=snapshot_lineage_hash(()),
    )
    return int(_persist_decision(conn, decision, _dt(9, 1)))


def _seed_result(
    conn: psycopg.Connection,
    *,
    match_id: int,
    outcome_status: str,
    winner_side: int | None,
    observed_at: datetime,
) -> int:
    ingest_id = _fetchval(
        conn,
        """
        INSERT INTO ingest_runs (
            source_system, source_dataset, started_at, status,
            source_version, source_file_name
        )
        VALUES ('SETTLEMENT_TEST', 'RESULTS', %s, 'RUNNING', 'v1', %s)
        RETURNING ingest_run_id
        """,
        (observed_at, f"result-{observed_at.isoformat()}.json"),
    )
    return int(
        _fetchval(
            conn,
            """
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, retired_side,
                sets_won_player_1, sets_won_player_2,
                games_won_player_1, games_won_player_2,
                scoreline_text, source_observed_at, as_of, ingest_run_id
            )
            VALUES (%s, %s, %s, NULL, NULL, NULL, NULL, NULL, NULL, %s, %s, %s)
            RETURNING match_result_observation_id
            """,
            (match_id, outcome_status, winner_side, observed_at, observed_at, ingest_id),
        )
    )


def test_classification_is_provider_neutral_and_conservative():
    assert classify_match_winner_settlement(
        outcome_status="COMPLETED", winner_side=1, selection_side=1
    ) == "WON"
    assert classify_match_winner_settlement(
        outcome_status="COMPLETED", winner_side=2, selection_side=1
    ) == "LOST"
    assert classify_match_winner_settlement(
        outcome_status="RETIRED", winner_side=1, selection_side=1
    ) == "VOID"
    assert classify_match_winner_settlement(
        outcome_status="WALKOVER", winner_side=1, selection_side=1
    ) == "VOID"
    assert classify_match_winner_settlement(
        outcome_status="CANCELLED", winner_side=None, selection_side=1
    ) == "CANCELLED"


def test_settlement_waits_for_result_then_is_idempotent(db):
    match_id = _seed_match(db)
    decision_id = _seed_bet(db, match_id=match_id)

    pending = settle_type_a_match_winner_decisions(db, settled_at=_dt(13))
    assert pending.eligible_decisions == 1
    assert pending.pending_results == 1
    assert pending.inserted == 0

    result_id = _seed_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        observed_at=_dt(14),
    )
    first = settle_type_a_match_winner_decisions(db, settled_at=_dt(14, 1))
    assert first.inserted == 1
    assert first.won == 1
    assert first.lost == 0

    persisted = db.execute(
        """
        SELECT match_result_observation_id, settlement_status::text, settlement_notes
        FROM decision_settlements
        WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    assert int(persisted[0]) == result_id
    assert persisted[1] == "WON"
    assert "tennis_match_winner_settlement_v1" in persisted[2]

    second = settle_type_a_match_winner_decisions(db, settled_at=_dt(15))
    assert second.inserted == 0
    assert second.existing == 1
    assert second.won == 1


def test_later_conflicting_result_truth_fails_closed(db):
    match_id = _seed_match(db)
    _seed_bet(db, match_id=match_id)
    _seed_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        observed_at=_dt(14),
    )
    settle_type_a_match_winner_decisions(db, settled_at=_dt(14, 1))

    _seed_result(
        db,
        match_id=match_id,
        outcome_status="COMPLETED",
        winner_side=2,
        observed_at=_dt(15),
    )
    with pytest.raises(SettlementEvidenceConflict):
        settle_type_a_match_winner_decisions(db, settled_at=_dt(15, 1))
