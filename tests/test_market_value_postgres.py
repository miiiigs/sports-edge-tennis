from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.manager.persistence import (
    ensure_manager_policy_version_v1,
    evaluate_and_persist_manager_consensus,
)
from sports_edge_tennis.market import (
    SharpReferenceSpec,
    evaluate_and_persist_market_slate,
    ensure_market_policy_version_v1,
    snapshot_lineage_hash,
)
from sports_edge_tennis.market.contracts import MarketExecutionError

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"
AGENTS = (A1, A2, A3)

SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)


def _utc_year(year: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, 6, 1, hour, minute, tzinfo=timezone.utc)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_player(conn: psycopg.Connection, name: str) -> int:
    return _fetchval(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,))


def _insert_match(conn: psycopg.Connection, tier: str = "ATP_250", as_of: datetime | None = None) -> int:
    version_as_of = as_of or _utc(10, 0)
    p1 = _insert_player(conn, "Player One")
    p2 = _insert_player(conn, "Player Two")
    tournament_id = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES (%s, 'ATP_MAIN', %s) RETURNING tournament_id",
        ("Market Open", tier),
    )
    edition_id = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) VALUES (%s, 2026, %s) RETURNING tournament_edition_id",
        (tournament_id, "Market Open 2026"),
    )
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    _fetchval(
        conn,
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', %s, 'HARD', 'R32', 3, FALSE, 'SCHEDULED', %s, %s)
        RETURNING match_version_id
        """,
        (match_id, p1, p2, version_as_of + timedelta(days=1), tier, version_as_of, version_as_of),
    )
    return match_id


def _insert_agent_config_version(conn: psycopg.Connection, agent_code: str) -> int:
    return _fetchval(
        conn,
        """
        INSERT INTO agent_config_versions (
            agent_code, config_version, model_version, feature_version, parameter_schema_version, config_payload
        )
        VALUES (%s, %s, 'm1', 'f1', 'params-v1', %s::jsonb)
        RETURNING agent_config_version_id
        """,
        (agent_code, f"{agent_code.lower()}-config-v1", '{"manager": true}'),
    )


def _insert_target(conn: psycopg.Connection, run_id: int, match_id: int) -> None:
    conn.execute(
        "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
        (run_id, match_id),
    )


def _insert_prediction(
    conn: psycopg.Connection,
    *,
    match_id: int,
    run_id: int,
    config_id: int,
    agent_code: str,
    status: str,
    side: int | None,
    prob: Decimal | None,
    as_of: datetime,
    locked_at: datetime,
) -> int:
    if status == "PREDICTED":
        representation = AGENT_PROBABILITY_REPRESENTATIONS[agent_code]
    else:
        side = None
        prob = None
        representation = None
    return _fetchval(
        conn,
        """
        INSERT INTO predictions (
            match_id, prediction_run_id, agent_config_version_id, agent_code,
            prediction_status, selection_side, win_probability, model_version, feature_version,
            as_of, prediction_locked_at, probability_representation
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'm1', 'f1', %s, %s, %s)
        RETURNING prediction_id
        """,
        (match_id, run_id, config_id, agent_code, status, side, prob, as_of, locked_at, representation),
    )


def _insert_prediction_run(
    conn: psycopg.Connection,
    *,
    run_status: str,
    as_of: datetime,
    prediction_finalized_at: datetime | None,
    ended_at: datetime | None,
) -> int:
    return _fetchval(
        conn,
        """
        INSERT INTO prediction_runs (
            run_status, as_of, started_at, prediction_finalized_at, ended_at, prediction_contract_version
        )
        VALUES (%s, %s, %s, %s, %s, 'predictive_agent_result_v1')
        RETURNING prediction_run_id
        """,
        (run_status, as_of, as_of, prediction_finalized_at, ended_at),
    )


def _build_finalized_run(
    conn: psycopg.Connection,
    *,
    match_id: int,
    agents: dict,
    as_of: datetime,
) -> int:
    finalized_at = as_of + timedelta(minutes=30)
    with conn.transaction():
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        run_id = _insert_prediction_run(
            conn, run_status="RUNNING", as_of=as_of, prediction_finalized_at=None, ended_at=None
        )
        config_ids = {agent: _insert_agent_config_version(conn, agent) for agent in AGENTS}
        _insert_target(conn, run_id, match_id)
        for agent in AGENTS:
            status, side, prob = agents[agent]
            _insert_prediction(
                conn,
                match_id=match_id,
                run_id=run_id,
                config_id=config_ids[agent],
                agent_code=agent,
                status=status,
                side=side,
                prob=prob,
                as_of=as_of,
                locked_at=finalized_at,
            )
        conn.execute(
            "UPDATE prediction_runs SET run_status = 'COMPLETED', prediction_finalized_at = %s, ended_at = %s WHERE prediction_run_id = %s",
            (finalized_at, finalized_at + timedelta(minutes=5), run_id),
        )
    return run_id


def _agreeing(*, side: int = 1, prob: str = "0.60") -> dict:
    return {
        A1: ("PREDICTED", side, Decimal(prob)),
        A2: ("PREDICTED", side, Decimal(prob)),
        A3: ("PREDICTED", side, Decimal(prob)),
    }


def _manager_policy_id(conn: psycopg.Connection) -> int:
    return ensure_manager_policy_version_v1(conn).manager_policy_version_id


def _market_policy_id(conn: psycopg.Connection) -> int:
    return ensure_market_policy_version_v1(conn).market_policy_version_id


def _run_manager(conn: psycopg.Connection, run_id: int) -> None:
    evaluate_and_persist_manager_consensus(
        conn, prediction_run_id=run_id, manager_policy_version_id=_manager_policy_id(conn)
    )


def _manager_row_id(conn: psycopg.Connection, run_id: int) -> int:
    return _fetchval(
        conn,
        "SELECT manager_consensus_result_id FROM manager_consensus_results WHERE prediction_run_id = %s",
        (run_id,),
    )


def _setup_consensus(
    conn: psycopg.Connection,
    *,
    side: int = 1,
    prob: str = "0.60",
    tier: str = "ATP_250",
    as_of: datetime | None = None,
) -> tuple[int, int, int]:
    run_as_of = as_of or _utc(10, 0)
    match_id = _insert_match(conn, tier=tier, as_of=run_as_of)
    run_id = _build_finalized_run(conn, match_id=match_id, agents=_agreeing(side=side, prob=prob), as_of=run_as_of)
    _run_manager(conn, run_id)
    return run_id, match_id, _manager_row_id(conn, run_id)


def _insert_odds(
    conn: psycopg.Connection,
    *,
    match_id: int,
    selection_type: str,
    price: str,
    source_system: str = "SHARP",
    bookmaker_code: str = "PINNACLE",
    source_snapshot_id: str = "s1",
    source_observed_at: datetime | None = None,
    as_of: datetime | None = None,
    market_status: str = "OPEN",
    ingested_at: datetime | None = None,
) -> int:
    observed = source_observed_at or _utc(10, 40)
    aof = as_of or observed
    return _fetchval(
        conn,
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type, selection_type,
            line_value, decimal_price, market_status, booksum, source_snapshot_id,
            source_record_hash, source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, 'MATCH_WINNER', %s, NULL, %s, %s, NULL, %s, %s, %s, %s, %s)
        RETURNING odds_snapshot_id
        """,
        (
            match_id,
            source_system,
            bookmaker_code,
            selection_type,
            Decimal(price),
            market_status,
            source_snapshot_id,
            f"hash-{selection_type}-{source_snapshot_id}",
            observed,
            aof,
            ingested_at if ingested_at is not None else observed,
        ),
    )


def _insert_reference_pair(
    conn: psycopg.Connection,
    match_id: int,
    *,
    p1_price: str,
    p2_price: str,
    source_observed_at: datetime | None = None,
    source_snapshot_id: str = "ref-1",
    market_status: str = "OPEN",
) -> tuple[int, int]:
    ref_p1 = _insert_odds(
        conn,
        match_id=match_id,
        selection_type="PLAYER_1",
        price=p1_price,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        source_snapshot_id=source_snapshot_id,
        source_observed_at=source_observed_at,
        market_status=market_status,
    )
    ref_p2 = _insert_odds(
        conn,
        match_id=match_id,
        selection_type="PLAYER_2",
        price=p2_price,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        source_snapshot_id=source_snapshot_id,
        source_observed_at=source_observed_at,
        market_status=market_status,
    )
    return ref_p1, ref_p2


def _insert_offered(
    conn: psycopg.Connection,
    match_id: int,
    *,
    selection_type: str,
    price: str,
    source_observed_at: datetime | None = None,
    bookmaker_code: str = "BOOK_A",
) -> int:
    return _insert_odds(
        conn,
        match_id=match_id,
        selection_type=selection_type,
        price=price,
        source_system="SOFT",
        bookmaker_code=bookmaker_code,
        source_snapshot_id="off-1",
        source_observed_at=source_observed_at,
    )


def _decision_rows(conn: psycopg.Connection, run_id: int) -> list:
    return conn.execute(
        """
        SELECT decision_id, manager_consensus_result_id, decision_status::text, reason_code::text,
               selection_side, edge_pct, min_acceptable_odds, snapshot_lineage_hash
        FROM decisions
        WHERE prediction_run_id = %s AND manager_consensus_result_id IS NOT NULL
        ORDER BY decision_id
        """,
        (run_id,),
    ).fetchall()


def _lineage(conn: psycopg.Connection, decision_id: int) -> list:
    return conn.execute(
        "SELECT snapshot_role::text, odds_snapshot_id FROM decision_odds_snapshots WHERE decision_id = %s ORDER BY snapshot_role",
        (decision_id,),
    ).fetchall()


def _eval_market(
    conn: psycopg.Connection,
    run_id: int,
    *,
    decision_as_of: datetime | None = None,
    market_execution_now: datetime | None = None,
) -> object:
    return evaluate_and_persist_market_slate(
        conn,
        prediction_run_id=run_id,
        market_policy_version_id=_market_policy_id(conn),
        decision_as_of=decision_as_of or _utc(10, 45),
        market_execution_now=market_execution_now or _utc(10, 46),
        sharp_reference=SHARP,
    )


# --- Manager safety ---


def test_manager_no_bet_becomes_not_manager_eligible(db: psycopg.Connection):
    match_id = _insert_match(db)
    agents = {
        A1: ("PREDICTED", 1, Decimal("0.60")),
        A2: ("PREDICTED", 2, Decimal("0.60")),
        A3: ("PREDICTED", 1, Decimal("0.60")),
    }
    run_id = _build_finalized_run(db, match_id=match_id, agents=agents, as_of=_utc(10, 0))
    _run_manager(db, run_id)

    outcome = _eval_market(db, run_id)
    assert outcome.manager_result_count == 1
    assert outcome.qualified_bet_count == 0

    row = _decision_rows(db, run_id)[0]
    assert row[2] == "NO_BET"
    assert row[3] == "NOT_MANAGER_ELIGIBLE"
    assert _lineage(db, row[0]) == []


def test_db_guard_prevents_bet_linked_to_manager_no_bet(db: psycopg.Connection):
    match_id = _insert_match(db)
    agents = {
        A1: ("PREDICTED", 1, Decimal("0.60")),
        A2: ("PREDICTED", 2, Decimal("0.60")),
        A3: ("PREDICTED", 1, Decimal("0.60")),
    }
    run_id = _build_finalized_run(db, match_id=match_id, agents=agents, as_of=_utc(10, 0))
    _run_manager(db, run_id)
    manager_id = _manager_row_id(db, run_id)
    policy_id = _market_policy_id(db)

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decisions (
                match_id, prediction_run_id, signal_type, decision_status, reason_code,
                market_type, as_of, prediction_locked_at, model_version, feature_version,
                manager_consensus_result_id, market_policy_version_id, snapshot_lineage_hash
            )
            VALUES (%s, %s, 'TYPE_A_MODEL_EDGE', 'BET', 'NONE',
                    'MATCH_WINNER', %s, %s, 'market_value_v1', 'type_a',
                    %s, %s, %s)
            """,
            (match_id, run_id, _utc(10, 45), _utc(10, 46), manager_id, policy_id, "x"),
        )


def test_manager_and_predictions_remain_unchanged(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    before = db.execute(
        "SELECT manager_status::text, consensus_probability FROM manager_consensus_results WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()
    _eval_market(db, run_id)
    after = db.execute(
        "SELECT manager_status::text, consensus_probability FROM manager_consensus_results WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()
    assert before == after


# --- Valid Type A ---


def test_valid_type_a_bet(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.20")

    outcome = _eval_market(db, run_id)
    assert outcome.qualified_bet_count == 1
    row = _decision_rows(db, run_id)[0]
    assert row[2] == "BET"
    assert row[3] == "NONE"
    assert row[4] == 1
    roles = _lineage(db, row[0])
    assert [r[0] for r in roles] == ["OFFERED_SELECTION", "REFERENCE_PLAYER_1", "REFERENCE_PLAYER_2"]


def test_no_model_edge_vs_sharp(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.55")
    _insert_reference_pair(db, match_id, p1_price="1.50", p2_price="3.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.50")

    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[2] == "NO_BET"
    assert row[3] == "NO_MODEL_EDGE_VS_SHARP"


def test_edge_below_threshold(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="1.70")

    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[2] == "NO_BET"
    assert row[3] == "EDGE_BELOW_THRESHOLD"


# --- PIT ---


def test_historical_replay_pit(db: psycopg.Connection):
    as_of = _utc_year(2025, 10, 0)
    run_id, match_id, _ = _setup_consensus(db, prob="0.60", as_of=as_of)
    decision_as_of = _utc_year(2025, 10, 45)
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00", source_observed_at=_utc_year(2025, 10, 40))
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.20", source_observed_at=_utc_year(2025, 10, 40))

    outcome = evaluate_and_persist_market_slate(
        db,
        prediction_run_id=run_id,
        market_policy_version_id=_market_policy_id(db),
        decision_as_of=decision_as_of,
        market_execution_now=_utc(10, 46),
        sharp_reference=SHARP,
    )
    assert outcome.qualified_bet_count == 1


def test_decision_as_of_before_run_as_of_rejects(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00")
    with pytest.raises(MarketExecutionError):
        _eval_market(db, run_id, decision_as_of=_utc(9, 0))


def test_execution_clock_before_decision_as_of_rejects(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00")
    with pytest.raises(MarketExecutionError):
        _eval_market(db, run_id, decision_as_of=_utc(10, 45), market_execution_now=_utc(10, 30))


def test_future_source_observed_at_excluded(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00", source_observed_at=_utc(11, 0))
    _eval_market(db, run_id, decision_as_of=_utc(10, 45))
    row = _decision_rows(db, run_id)[0]
    assert row[3] == "MISSING_SHARP_REFERENCE"


def test_future_snapshot_as_of_excluded(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_odds(
        db,
        match_id=match_id,
        selection_type="PLAYER_1",
        price="2.00",
        source_snapshot_id="ref-fut",
        source_observed_at=_utc(10, 40),
        as_of=_utc(11, 0),
    )
    _insert_odds(
        db,
        match_id=match_id,
        selection_type="PLAYER_2",
        price="2.00",
        source_snapshot_id="ref-fut",
        source_observed_at=_utc(10, 40),
        as_of=_utc(11, 0),
    )
    _eval_market(db, run_id, decision_as_of=_utc(10, 45))
    row = _decision_rows(db, run_id)[0]
    assert row[3] == "MISSING_SHARP_REFERENCE"


def test_ingested_at_does_not_control_pit(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    ref_p1 = _insert_odds(
        db,
        match_id=match_id,
        selection_type="PLAYER_1",
        price="2.00",
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        source_snapshot_id="ref-late",
        source_observed_at=_utc(10, 40),
        ingested_at=_utc(12, 0),
    )
    ref_p2 = _insert_odds(
        db,
        match_id=match_id,
        selection_type="PLAYER_2",
        price="2.00",
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        source_snapshot_id="ref-late",
        source_observed_at=_utc(10, 40),
        ingested_at=_utc(12, 0),
    )
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.20")

    outcome = _eval_market(db, run_id, decision_as_of=_utc(10, 45))
    assert outcome.qualified_bet_count == 1


# --- Market completeness ---


def test_incomplete_market_one_side(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_odds(db, match_id=match_id, selection_type="PLAYER_1", price="2.00")
    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[3] == "INCOMPLETE_MARKET"


def test_suspended_reference_market(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00", market_status="SUSPENDED")
    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[3] == "MARKET_SUSPENDED"


def test_crossed_reference_market(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="3.00", p2_price="3.00")
    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[3] == "CROSSED_MARKET"


def test_zero_overround_reference_is_valid(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.20")
    outcome = _eval_market(db, run_id)
    assert outcome.qualified_bet_count == 1


def test_stale_reference_quote(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00", source_observed_at=_utc(9, 0))
    _eval_market(db, run_id, decision_as_of=_utc(10, 45))
    row = _decision_rows(db, run_id)[0]
    assert row[3] == "STALE_MARKET"


def test_stale_offered_quote_fails_closed(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.20", source_observed_at=_utc(9, 0))
    _eval_market(db, run_id, decision_as_of=_utc(10, 45))
    row = _decision_rows(db, run_id)[0]
    assert row[3] == "MISSING_MARKET"


def test_missing_sharp_reference(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.20")
    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[3] == "MISSING_SHARP_REFERENCE"


# --- Long price ---


def test_long_price_reference_player_1(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.90", side=2)
    _insert_reference_pair(db, match_id, p1_price="5.00", p2_price="1.20")
    _insert_offered(db, match_id, selection_type="PLAYER_2", price="1.30")
    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[2] == "BET"
    assert row[6] == Decimal("1.200000")


def test_long_price_reference_player_2(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.90", side=1)
    _insert_reference_pair(db, match_id, p1_price="1.20", p2_price="5.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="1.30")
    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[2] == "BET"
    assert row[6] == Decimal("1.200000")


def test_exact_4_00_no_escalation(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.90", side=2)
    _insert_reference_pair(db, match_id, p1_price="4.00", p2_price="1.20")
    _insert_offered(db, match_id, selection_type="PLAYER_2", price="1.30")
    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[2] == "BET"
    assert row[6] == Decimal("1.166667")


# --- Idempotency / lineage ---


def test_idempotent_retry(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.20")

    first = _eval_market(db, run_id)
    assert first.status == "PERSISTED"
    assert first.new_decision_count == 1

    second = _eval_market(db, run_id)
    assert second.status == "IDEMPOTENT_SUCCESS"
    assert second.new_decision_count == 0
    assert second.existing_decision_count == 1
    assert len(_decision_rows(db, run_id)) == 1


def test_late_arriving_snapshot_creates_new_decision(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.20")

    first = _eval_market(db, run_id)
    assert first.status == "PERSISTED"
    before = _decision_rows(db, run_id)
    old_hash = before[0][7]

    # Late-arriving better offered price changes the resolved lineage.
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.30", bookmaker_code="BOOK_B")

    second = _eval_market(db, run_id)
    assert second.new_decision_count == 1
    assert second.existing_decision_count == 0

    rows = _decision_rows(db, run_id)
    assert len(rows) == 2
    hashes = {r[7] for r in rows}
    assert old_hash in hashes
    assert len(hashes) == 2


def test_manager_no_bet_has_empty_lineage(db: psycopg.Connection):
    match_id = _insert_match(db)
    agents = {
        A1: ("PREDICTED", 1, Decimal("0.60")),
        A2: ("PREDICTED", 2, Decimal("0.60")),
        A3: ("PREDICTED", 1, Decimal("0.60")),
    }
    run_id = _build_finalized_run(db, match_id=match_id, agents=agents, as_of=_utc(10, 0))
    _run_manager(db, run_id)

    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[7] == snapshot_lineage_hash(())
    assert _lineage(db, row[0]) == []


def test_partial_lineage_reflects_inspected_side(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    ref_p1 = _insert_odds(db, match_id=match_id, selection_type="PLAYER_1", price="2.00")
    _eval_market(db, run_id)
    row = _decision_rows(db, run_id)[0]
    assert row[3] == "INCOMPLETE_MARKET"
    assert _lineage(db, row[0]) == [("REFERENCE_PLAYER_1", ref_p1)]


# --- Whole slate ---


def _build_finalized_multi_run(
    conn: psycopg.Connection,
    *,
    matches: dict[int, dict],
    as_of: datetime,
) -> int:
    finalized_at = as_of + timedelta(minutes=30)
    with conn.transaction():
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        run_id = _insert_prediction_run(
            conn, run_status="RUNNING", as_of=as_of, prediction_finalized_at=None, ended_at=None
        )
        config_ids = {agent: _insert_agent_config_version(conn, agent) for agent in AGENTS}
        for match_id, agents in matches.items():
            _insert_target(conn, run_id, match_id)
            for agent in AGENTS:
                status, side, prob = agents[agent]
                _insert_prediction(
                    conn,
                    match_id=match_id,
                    run_id=run_id,
                    config_id=config_ids[agent],
                    agent_code=agent,
                    status=status,
                    side=side,
                    prob=prob,
                    as_of=as_of,
                    locked_at=finalized_at,
                )
        conn.execute(
            "UPDATE prediction_runs SET run_status = 'COMPLETED', prediction_finalized_at = %s, ended_at = %s WHERE prediction_run_id = %s",
            (finalized_at, finalized_at + timedelta(minutes=5), run_id),
        )
    return run_id


def test_whole_slate_n_outcomes(db: psycopg.Connection):
    match_1 = _insert_match(db)
    match_2 = _insert_match(db)
    run_id = _build_finalized_multi_run(
        db,
        matches={match_1: _agreeing(prob="0.60"), match_2: _agreeing(prob="0.62")},
        as_of=_utc(10, 0),
    )
    _run_manager(db, run_id)
    _insert_reference_pair(db, match_1, p1_price="2.00", p2_price="2.00")
    _insert_offered(db, match_1, selection_type="PLAYER_1", price="2.20")
    _insert_reference_pair(db, match_2, p1_price="2.00", p2_price="2.00", source_snapshot_id="ref-2")
    _insert_offered(db, match_2, selection_type="PLAYER_1", price="2.25")

    outcome = _eval_market(db, run_id)
    assert outcome.manager_result_count == 2
    assert outcome.new_decision_count == 2
    assert outcome.qualified_bet_count == 2


def test_two_match_slate_mixed_retry(db: psycopg.Connection):
    match_1 = _insert_match(db)
    match_2 = _insert_match(db)
    run_id = _build_finalized_multi_run(
        db,
        matches={match_1: _agreeing(prob="0.60"), match_2: _agreeing(prob="0.62")},
        as_of=_utc(10, 0),
    )
    _run_manager(db, run_id)

    _insert_reference_pair(db, match_1, p1_price="2.00", p2_price="2.00")
    _insert_offered(db, match_1, selection_type="PLAYER_1", price="2.20")
    _insert_reference_pair(db, match_2, p1_price="2.00", p2_price="2.00", source_snapshot_id="ref-2")
    _insert_offered(db, match_2, selection_type="PLAYER_1", price="2.25")

    first = _eval_market(db, run_id)
    assert first.manager_result_count == 2
    assert first.new_decision_count == 2
    assert first.qualified_bet_count == 2

    _insert_offered(db, match_1, selection_type="PLAYER_1", price="2.40", bookmaker_code="BOOK_B")
    second = _eval_market(db, run_id)
    assert second.new_decision_count == 1
    assert second.existing_decision_count == 1
    assert len(_decision_rows(db, run_id)) == 3


def test_zero_bet_slate_valid(db: psycopg.Connection):
    run_id, match_id, _ = _setup_consensus(db, prob="0.55")
    _insert_reference_pair(db, match_id, p1_price="1.50", p2_price="3.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.50")
    outcome = _eval_market(db, run_id)
    assert outcome.qualified_bet_count == 0
    assert outcome.manager_result_count == 1


def test_system_failure_rolls_back_new_writes(db: psycopg.Connection, monkeypatch):
    run_id, match_id, _ = _setup_consensus(db, prob="0.60")
    _insert_reference_pair(db, match_id, p1_price="2.00", p2_price="2.00")
    _insert_offered(db, match_id, selection_type="PLAYER_1", price="2.20")

    import sports_edge_tennis.market.evaluator as evaluator

    original = evaluator._persist_decision
    def flaky(conn, decision, market_execution_now):
        raise RuntimeError("injected persistence failure")
    monkeypatch.setattr(evaluator, "_persist_decision", flaky)

    with pytest.raises(RuntimeError):
        _eval_market(db, run_id)

    assert _decision_rows(db, run_id) == []
