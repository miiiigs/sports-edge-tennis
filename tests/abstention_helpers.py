"""Shared PostgreSQL fixtures for abstention-observability tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import psycopg

from sports_edge_tennis.agents.contracts import (
    AGENT1_RAW_PROBABILITY_V1,
    AGENT2_RAW_PROBABILITY_V1,
    AGENT3_RAW_PROBABILITY_V1,
    PredictiveAgentResultV1,
)
from sports_edge_tennis.data.prediction_persistence import (
    PredictiveAgentMatrixPrediction,
    persist_and_finalize_prediction_matrix,
)

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"

_REPRESENTATION = {
    A1: AGENT1_RAW_PROBABILITY_V1,
    A2: AGENT2_RAW_PROBABILITY_V1,
    A3: AGENT3_RAW_PROBABILITY_V1,
}


def utc(day: int = 1, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def insert_player(conn: psycopg.Connection, name: str) -> int:
    return fetchval(
        conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,)
    )


def insert_match(
    conn: psycopg.Connection,
    *,
    status: str = "SCHEDULED",
    surface: str | None = "HARD",
    match_start_at: datetime | None = None,
) -> int:
    p1 = insert_player(conn, "Player One")
    p2 = insert_player(conn, "Player Two")
    tournament_id = fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Observability Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
    )
    edition_id = fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) "
        "VALUES (%s, 2026, 'Observability Open 2026') RETURNING tournament_edition_id",
        (tournament_id,),
    )
    match_id = fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    start = match_start_at or utc(hour=9)
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', %s, 'R32', 3, FALSE, %s, %s, %s)
        """,
        (match_id, p1, p2, start, surface, status, start, start),
    )
    return match_id


def insert_run(conn: psycopg.Connection, *, as_of: datetime) -> int:
    return fetchval(
        conn,
        """
        INSERT INTO prediction_runs (run_status, as_of, started_at, prediction_contract_version)
        VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1')
        RETURNING prediction_run_id
        """,
        (as_of, as_of),
    )


def insert_target(conn: psycopg.Connection, *, run_id: int, match_id: int) -> None:
    conn.execute(
        "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
        (run_id, match_id),
    )


def insert_agent_config(conn: psycopg.Connection, agent_code: str) -> int:
    return fetchval(
        conn,
        """
        INSERT INTO agent_config_versions (
            agent_code, config_version, model_version, feature_version,
            parameter_schema_version, config_payload
        )
        VALUES (%s, 'cv', 'm', 'f', 'ps', %s::jsonb)
        RETURNING agent_config_version_id
        """,
        (agent_code, json.dumps({"test": True})),
    )


def seed_agent_configs(conn: psycopg.Connection) -> dict[str, int]:
    return {agent_code: insert_agent_config(conn, agent_code) for agent_code in (A1, A2, A3)}


def make_result(
    agent_code: str,
    as_of: datetime,
    *,
    status: str,
    side: int | None = None,
    probability=None,
    reason: str | None = None,
) -> PredictiveAgentResultV1:
    representation = _REPRESENTATION[agent_code] if status == "PREDICTED" else None
    if probability is not None:
        probability = Decimal(str(probability))
    return PredictiveAgentResultV1(
        agent_code=agent_code,
        prediction_status=status,
        selected_side=side,
        selected_probability=probability,
        probability_representation=representation,
        abstain_reason=reason,
        model_version="m",
        feature_version="f",
        config_version="cv",
        parameter_schema_version="ps",
        as_of=as_of,
    )


def finalize_matrix(
    conn: psycopg.Connection,
    *,
    run_id: int,
    match_id: int,
    configs: dict[str, int],
    results,
    finalized_at: datetime,
) -> list[int]:
    rows = [
        PredictiveAgentMatrixPrediction(
            match_id=match_id,
            agent_config_version_id=configs[result.agent_code],
            result=result,
        )
        for result in results
    ]
    return persist_and_finalize_prediction_matrix(
        conn,
        prediction_run_id=run_id,
        rows=rows,
        prediction_finalized_at=finalized_at,
        ended_at=finalized_at,
    )


def raw_insert_and_finalize(
    conn: psycopg.Connection,
    *,
    run_id: int,
    match_id: int,
    configs: dict[str, int],
    specs,
    finalized_at: datetime,
) -> None:
    """Insert predictions directly to simulate pre-0017 historical rows (NULL reason)."""
    with conn.transaction():
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        for agent_code, status, side, probability, representation, reason in specs:
            conn.execute(
                """
                INSERT INTO predictions (
                    match_id, prediction_run_id, agent_config_version_id, agent_code,
                    prediction_status, selection_side, win_probability,
                    probability_representation, model_version, feature_version,
                    as_of, prediction_locked_at, abstain_reason_code
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::numeric, %s, 'm', 'f', %s, %s, %s)
                """,
                (
                    match_id,
                    run_id,
                    configs[agent_code],
                    agent_code,
                    status,
                    side,
                    None if probability is None else str(probability),
                    representation,
                    finalized_at,
                    finalized_at,
                    reason,
                ),
            )
        conn.execute(
            """
            UPDATE prediction_runs
            SET run_status = 'COMPLETED', prediction_finalized_at = %s, ended_at = %s
            WHERE prediction_run_id = %s
            """,
            (finalized_at, finalized_at, run_id),
        )
