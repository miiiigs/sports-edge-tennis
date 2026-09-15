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
    ensure_market_policy_version_v1,
    evaluate_and_persist_market_slate,
)
from sports_edge_tennis.market import evaluator as market_evaluator
from sports_edge_tennis.market.persistence import (
    load_competition_tier,
    load_manager_slate,
    load_market_policy_version,
    load_match_winner_odds,
)
from sports_edge_tennis.publication import PublicationError


A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"
AGENTS = (A1, A2, A3)
SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 15, hour, minute, tzinfo=timezone.utc)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_player(conn: psycopg.Connection, name: str) -> int:
    return _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (name,),
    )


def _setup_consensus(conn: psycopg.Connection) -> tuple[int, int]:
    p1 = _insert_player(conn, "Publication Player One")
    p2 = _insert_player(conn, "Publication Player Two")
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Publication Open', 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2026, 'Publication Open 2026')
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
            tour_scope, competition_tier, surface, round_code, best_of_sets,
            qualifying_flag, match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3,
                FALSE, 'SCHEDULED', %s, %s)
        """,
        (match_id, p1, p2, _utc(18), _utc(10), _utc(10)),
    )

    run_id = _fetchval(
        conn,
        """
        INSERT INTO prediction_runs (
            run_status, as_of, started_at, prediction_contract_version
        )
        VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1')
        RETURNING prediction_run_id
        """,
        (_utc(10), _utc(10)),
    )
    conn.execute(
        "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
        (run_id, match_id),
    )

    locked_at = _utc(10, 30)
    for agent in AGENTS:
        config_id = _fetchval(
            conn,
            """
            INSERT INTO agent_config_versions (
                agent_code, config_version, model_version, feature_version,
                parameter_schema_version, config_payload
            )
            VALUES (%s, %s, 'm1', 'f1', 'params-v1', '{"publication": true}'::jsonb)
            RETURNING agent_config_version_id
            """,
            (agent, f"publication-{agent.lower()}"),
        )
        conn.execute(
            """
            INSERT INTO predictions (
                match_id, prediction_run_id, agent_config_version_id, agent_code,
                prediction_status, selection_side, win_probability,
                model_version, feature_version, as_of, prediction_locked_at,
                probability_representation
            )
            VALUES (%s, %s, %s, %s, 'PREDICTED', 1, 0.600000,
                    'm1', 'f1', %s, %s, %s)
            """,
            (
                match_id,
                run_id,
                config_id,
                agent,
                _utc(10),
                locked_at,
                AGENT_PROBABILITY_REPRESENTATIONS[agent],
            ),
        )

    conn.execute(
        """
        UPDATE prediction_runs
        SET run_status = 'COMPLETED', prediction_finalized_at = %s, ended_at = %s
        WHERE prediction_run_id = %s
        """,
        (locked_at, _utc(10, 35), run_id),
    )
    manager_policy_id = ensure_manager_policy_version_v1(conn).manager_policy_version_id
    evaluate_and_persist_manager_consensus(
        conn,
        prediction_run_id=run_id,
        manager_policy_version_id=manager_policy_id,
    )
    return run_id, match_id


def _insert_odds(
    conn: psycopg.Connection,
    *,
    match_id: int,
    source_system: str,
    bookmaker: str,
    selection: str,
    price: str,
    snapshot_id: str,
) -> int:
    return _fetchval(
        conn,
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type, selection_type,
            decimal_price, market_status, source_snapshot_id, source_record_hash,
            source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, 'MATCH_WINNER', %s, %s, 'OPEN', %s, %s, %s, %s, %s)
        RETURNING odds_snapshot_id
        """,
        (
            match_id,
            source_system,
            bookmaker,
            selection,
            Decimal(price),
            snapshot_id,
            f"publication-{snapshot_id}-{selection}",
            _utc(10, 40),
            _utc(10, 40),
            _utc(10, 40),
        ),
    )


def _seed_value_market(conn: psycopg.Connection, match_id: int) -> None:
    _insert_odds(
        conn,
        match_id=match_id,
        source_system="SHARP",
        bookmaker="PINNACLE",
        selection="PLAYER_1",
        price="2.00",
        snapshot_id="sharp-1",
    )
    _insert_odds(
        conn,
        match_id=match_id,
        source_system="SHARP",
        bookmaker="PINNACLE",
        selection="PLAYER_2",
        price="2.00",
        snapshot_id="sharp-1",
    )
    _insert_odds(
        conn,
        match_id=match_id,
        source_system="SOFT",
        bookmaker="BOOK_A",
        selection="PLAYER_1",
        price="2.20",
        snapshot_id="offer-1",
    )


def _market_policy_id(conn: psycopg.Connection) -> int:
    return ensure_market_policy_version_v1(conn).market_policy_version_id


def _run_market(
    conn: psycopg.Connection,
    run_id: int,
    *,
    execution_at: datetime = _utc(10, 46),
):
    return evaluate_and_persist_market_slate(
        conn,
        prediction_run_id=run_id,
        market_policy_version_id=_market_policy_id(conn),
        decision_as_of=_utc(10, 45),
        market_execution_now=execution_at,
        sharp_reference=SHARP,
    )


def test_new_final_bet_is_published_at_transaction_boundary(db: psycopg.Connection):
    run_id, match_id = _setup_consensus(db)
    _seed_value_market(db, match_id)

    result = _run_market(db, run_id, execution_at=_utc(10, 46))

    assert result.qualified_bet_count == 1
    row = db.execute(
        "SELECT decision_status::text, published_at FROM decisions WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()
    assert row == ("BET", _utc(10, 46))


def test_no_bet_evidence_is_not_marked_published(db: psycopg.Connection):
    run_id, _match_id = _setup_consensus(db)

    result = _run_market(db, run_id)

    assert result.qualified_bet_count == 0
    status, published_at = db.execute(
        "SELECT decision_status::text, published_at FROM decisions WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()
    assert status == "NO_BET"
    assert published_at is None


def test_idempotent_retry_preserves_original_publication_timestamp(db: psycopg.Connection):
    run_id, match_id = _setup_consensus(db)
    _seed_value_market(db, match_id)

    first = _run_market(db, run_id, execution_at=_utc(10, 46))
    second = _run_market(db, run_id, execution_at=_utc(10, 55))

    assert first.status == "PERSISTED"
    assert second.status == "IDEMPOTENT_SUCCESS"
    published_at = db.execute(
        "SELECT published_at FROM decisions WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()[0]
    assert published_at == _utc(10, 46)


def test_existing_legacy_unpublished_bet_is_not_backfilled_on_retry(
    db: psycopg.Connection,
):
    run_id, match_id = _setup_consensus(db)
    _seed_value_market(db, match_id)
    policy_id = _market_policy_id(db)

    manager_row = load_manager_slate(db, prediction_run_id=run_id)[0]
    policy = load_market_policy_version(db, market_policy_version_id=policy_id)
    decision = market_evaluator.evaluate_market_match(
        manager_row=manager_row,
        policy=policy,
        competition_tier=load_competition_tier(
            db,
            match_id=match_id,
            decision_as_of=_utc(10, 45),
        ),
        sharp_reference=SHARP,
        odds_snapshots=load_match_winner_odds(
            db,
            match_id=match_id,
            decision_as_of=_utc(10, 45),
        ),
        decision_as_of=_utc(10, 45),
    )
    assert decision.decision_status == "BET"

    with db.transaction():
        decision_id = market_evaluator._persist_decision(db, decision, _utc(10, 46))
        market_evaluator._persist_lineage(db, decision_id, decision.lineage)

    assert db.execute(
        "SELECT published_at FROM decisions WHERE decision_id = %s",
        (decision_id,),
    ).fetchone()[0] is None

    result = _run_market(db, run_id, execution_at=_utc(10, 55))

    assert result.status == "IDEMPOTENT_SUCCESS"
    assert result.new_decision_count == 0
    assert db.execute(
        "SELECT published_at FROM decisions WHERE decision_id = %s",
        (decision_id,),
    ).fetchone()[0] is None


def test_publication_failure_rolls_back_new_decision(
    db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
):
    run_id, match_id = _setup_consensus(db)
    _seed_value_market(db, match_id)

    def fail_publication(*_args, **_kwargs):
        raise PublicationError("injected publication failure")

    monkeypatch.setattr(
        "sports_edge_tennis.veto.integration.publish_new_bet_decision",
        fail_publication,
    )

    with pytest.raises(PublicationError, match="injected publication failure"):
        _run_market(db, run_id)

    assert db.execute(
        "SELECT count(*) FROM decisions WHERE prediction_run_id = %s",
        (run_id,),
    ).fetchone()[0] == 0
