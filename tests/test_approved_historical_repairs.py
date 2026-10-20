from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.live.approved_historical_repairs import (
    APPROVAL_PHRASE,
    REPAIR_130717,
    REPAIR_RECONCILE,
    REPAIR_WALKOVER,
    STATUS_ALREADY_APPLIED,
    STATUS_APPLIED,
    STATUS_BLOCKED,
    STATUS_READY,
    STATUS_STALE_PLAN,
    STATUS_UNAUTHORIZED,
    apply_plan,
    build_plan,
)
from sports_edge_tennis.settlement import (
    classify_match_winner_settlement,
    settle_type_a_match_winner_decisions,
)

OBSERVED_AT = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _player(db: psycopg.Connection, name: str) -> int:
    return int(
        db.execute(
            "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
            (name,),
        ).fetchone()[0]
    )


def _tournament(db: psycopg.Connection, name: str, tier: str = "CHALLENGER") -> int:
    return int(
        db.execute(
            """
            INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
            VALUES (%s, %s, %s)
            RETURNING tournament_id
            """,
            (name, "CHALLENGER" if tier == "CHALLENGER" else "ATP_MAIN", tier),
        ).fetchone()[0]
    )


def _edition(
    db: psycopg.Connection,
    *,
    tournament_id: int,
    name: str,
    season: int = 2026,
    surface: str = "HARD",
) -> int:
    return int(
        db.execute(
            """
            INSERT INTO tournament_editions (
                tournament_id, season_year, edition_name, surface, start_date
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING tournament_edition_id
            """,
            (tournament_id, season, name, surface, OBSERVED_AT.date()),
        ).fetchone()[0]
    )


def _match(
    db: psycopg.Connection,
    *,
    edition_id: int,
    player_1_id: int,
    player_2_id: int,
    start_at: datetime,
    status: str,
    round_code: str,
    source_system: str,
    source_match_id: str,
    match_id: int | None = None,
) -> int:
    if match_id is None:
        match_id = int(
            db.execute(
                "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
                (edition_id,),
            ).fetchone()[0]
        )
    else:
        db.execute(
            """
            INSERT INTO matches (match_id, tournament_edition_id)
            OVERRIDING SYSTEM VALUE
            VALUES (%s, %s)
            """,
            (match_id, edition_id),
        )
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, surface, round_code, best_of_sets,
            qualifying_flag, match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', 'HARD', %s, 3,
                FALSE, %s, %s, %s)
        """,
        (
            match_id,
            player_1_id,
            player_2_id,
            start_at,
            round_code,
            status,
            OBSERVED_AT,
            OBSERVED_AT,
        ),
    )
    ingest_run = int(
        db.execute(
            """
            INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
            VALUES ('TEST', 'approved-repair-test', %s, 'COMPLETED')
            RETURNING ingest_run_id
            """,
            (OBSERVED_AT,),
        ).fetchone()[0]
    )
    db.execute(
        """
        INSERT INTO match_source_ids (
            match_id, source_system, source_match_id, ingest_run_id
        )
        VALUES (%s, %s, %s, %s)
        """,
        (match_id, source_system, source_match_id, ingest_run),
    )
    return match_id


def _result(
    db: psycopg.Connection,
    *,
    match_id: int,
    outcome: str,
    winner_side: int | None,
    observed_at: datetime,
) -> int:
    return int(
        db.execute(
            """
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, source_observed_at, as_of
            )
            VALUES (%s, %s, %s, %s, %s)
            RETURNING match_result_observation_id
            """,
            (match_id, outcome, winner_side, observed_at, observed_at),
        ).fetchone()[0]
    )


def _prediction_run(db: psycopg.Connection) -> int:
    return int(
        db.execute(
            """
            INSERT INTO prediction_runs (
                run_status, as_of, started_at, prediction_contract_version
            )
            VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1')
            RETURNING prediction_run_id
            """,
            (OBSERVED_AT - timedelta(hours=13), OBSERVED_AT - timedelta(hours=13)),
        ).fetchone()[0]
    )


def _bet_decision(
    db: psycopg.Connection,
    *,
    match_id: int,
    prediction_run_id: int,
    side: int,
    index: int,
) -> int:
    as_of = OBSERVED_AT - timedelta(hours=12) + timedelta(minutes=index)
    return int(
        db.execute(
            """
            INSERT INTO decisions (
                match_id, prediction_run_id, signal_type, decision_status,
                reason_code, market_type, selection_type, selection_side,
                consensus_probability, consensus_fair_odds, market_decimal_price,
                sharp_reference_price, price_source_system, price_bookmaker_code,
                devig_method, devig_fair_odds, edge_pct, min_acceptable_odds,
                active_agent_count, model_version, feature_version, as_of,
                prediction_locked_at
            )
            VALUES (
                %s, %s, 'TYPE_A_MODEL_EDGE', 'BET', 'NONE', 'MATCH_WINNER',
                %s, %s, 0.600000, 1.900000, 2.000000, 1.900000, 'TEST',
                'TEST_BOOK', 'POWER', 1.900000, 0.200000, 1.800000, 3,
                'test-model', 'test-feature', %s, %s
            )
            RETURNING decision_id
            """,
            (
                match_id,
                prediction_run_id,
                "PLAYER_1" if side == 1 else "PLAYER_2",
                side,
                as_of,
                as_of + timedelta(minutes=5),
            ),
        ).fetchone()[0]
    )


def _seed_walkover(db: psycopg.Connection) -> tuple[int, int]:
    tournament_id = _tournament(db, "Roanne")
    edition_id = _edition(db, tournament_id=tournament_id, name="Roanne 2026")
    p1 = _player(db, "J. Kym")
    p2 = _player(db, "D. Stricker")
    match_id = _match(
        db,
        edition_id=edition_id,
        player_1_id=p1,
        player_2_id=p2,
        start_at=datetime(2026, 10, 16, 12, 10, tzinfo=UTC),
        status="WALKOVER",
        round_code="QF",
        source_system="API_TENNIS",
        source_match_id="12171502",
        match_id=132211,
    )
    prediction_run_id = _prediction_run(db)
    for index in range(12):
        _bet_decision(
            db,
            match_id=match_id,
            prediction_run_id=prediction_run_id,
            side=1,
            index=index,
        )
    unrelated = _match(
        db,
        edition_id=edition_id,
        player_1_id=_player(db, "Other One"),
        player_2_id=_player(db, "Other Two"),
        start_at=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
        status="COMPLETED",
        round_code="R32",
        source_system="API_TENNIS",
        source_match_id="unrelated-result",
        match_id=132999,
    )
    _result(
        db,
        match_id=unrelated,
        outcome="COMPLETED",
        winner_side=1,
        observed_at=datetime(2026, 10, 15, 15, 0, tzinfo=UTC),
    )
    return match_id, unrelated


def _seed_reconciliation_pair(db: psycopg.Connection) -> tuple[int, int]:
    tournament_id = _tournament(db, "Plovdiv 4 (Bulgaria) - Qualification")
    target_edition = _edition(
        db, tournament_id=tournament_id, name="Plovdiv target", surface="HARD"
    )
    evidence_edition = _edition(
        db, tournament_id=tournament_id, name="Plovdiv evidence", surface="CLAY"
    )
    p1 = _player(db, "M. Alcala Gurri")
    p2 = _player(db, "C. Taberner")
    target = _match(
        db,
        edition_id=target_edition,
        player_1_id=p1,
        player_2_id=p2,
        start_at=datetime(2026, 9, 26, 11, 0, tzinfo=UTC),
        status="COMPLETED",
        round_code="semifinal",
        source_system="SPORTRADAR",
        source_match_id="sr:sport_event:74753168",
        match_id=131036,
    )
    evidence = _match(
        db,
        edition_id=evidence_edition,
        player_1_id=p1,
        player_2_id=p2,
        start_at=datetime(2026, 9, 26, 11, 40, tzinfo=UTC),
        status="COMPLETED",
        round_code="SF",
        source_system="API_TENNIS",
        source_match_id="12166029",
        match_id=131137,
    )
    _result(
        db,
        match_id=evidence,
        outcome="COMPLETED",
        winner_side=1,
        observed_at=datetime(2026, 9, 27, 0, 14, tzinfo=UTC),
    )
    return target, evidence


def test_walkover_plan_is_void_without_a_winner(db: psycopg.Connection):
    match_id, _ = _seed_walkover(db)
    plan = build_plan(db, repair_id=REPAIR_WALKOVER, now=OBSERVED_AT)

    assert plan.target_match_id == match_id
    assert plan.status == STATUS_READY
    assert plan.apply_safe is True
    assert plan.after is not None
    assert plan.after["outcome_status"] == "WALKOVER"
    assert plan.after["winner_side"] is None
    assert plan.after["scoreline_text"] is None
    assert plan.after["settlement_status"] == "VOID"
    sql = " ".join(statement.sql for statement in plan.statements)
    assert "predictions" not in sql
    assert "UPDATE " not in sql
    assert "DELETE " not in sql
    assert "merge" not in sql.lower()


def test_walkover_apply_requires_exact_authorization_and_sha(db: psycopg.Connection):
    match_id, _ = _seed_walkover(db)
    plan = build_plan(db, repair_id=REPAIR_WALKOVER, now=OBSERVED_AT)

    unauthorized = apply_plan(
        db,
        repair_id=REPAIR_WALKOVER,
        authorization="wrong",
        expected_sha256=plan.plan_sha256,
        now=OBSERVED_AT,
    )
    assert unauthorized["status"] == STATUS_UNAUTHORIZED
    assert (
        db.execute(
            "SELECT count(*) FROM match_result_observations WHERE match_id = %s",
            (match_id,),
        ).fetchone()[0]
        == 0
    )

    stale = apply_plan(
        db,
        repair_id=REPAIR_WALKOVER,
        authorization=APPROVAL_PHRASE,
        expected_sha256="0" * 64,
        now=OBSERVED_AT,
    )
    assert stale["status"] == STATUS_STALE_PLAN


def test_walkover_apply_is_idempotent_and_settles_void_once(db: psycopg.Connection):
    match_id, unrelated = _seed_walkover(db)
    unrelated_before = db.execute(
        "SELECT outcome_status::text, winner_side FROM match_result_observations "
        "WHERE match_id = %s",
        (unrelated,),
    ).fetchone()
    plan = build_plan(db, repair_id=REPAIR_WALKOVER, now=OBSERVED_AT)

    applied = apply_plan(
        db,
        repair_id=REPAIR_WALKOVER,
        authorization=APPROVAL_PHRASE,
        expected_sha256=plan.plan_sha256,
        now=OBSERVED_AT,
    )
    assert applied["status"] == STATUS_APPLIED
    result = db.execute(
        "SELECT outcome_status::text, winner_side FROM match_result_observations "
        "WHERE match_id = %s",
        (match_id,),
    ).fetchone()
    assert result == ("WALKOVER", None)
    assert (
        db.execute(
            "SELECT count(*) FROM decisions WHERE match_id = %s", (match_id,)
        ).fetchone()[0]
        == 12
    )
    assert (
        db.execute(
            "SELECT outcome_status::text, winner_side FROM match_result_observations "
            "WHERE match_id = %s",
            (unrelated,),
        ).fetchone()
        == unrelated_before
    )

    second = apply_plan(
        db,
        repair_id=REPAIR_WALKOVER,
        authorization=APPROVAL_PHRASE,
        expected_sha256=plan.plan_sha256,
        now=OBSERVED_AT,
    )
    assert second["status"] == STATUS_ALREADY_APPLIED
    assert (
        db.execute(
            "SELECT count(*) FROM match_result_observations WHERE match_id = %s",
            (match_id,),
        ).fetchone()[0]
        == 1
    )

    first_settlement = settle_type_a_match_winner_decisions(
        db, settled_at=datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    )
    second_settlement = settle_type_a_match_winner_decisions(
        db, settled_at=datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
    )
    assert first_settlement.inserted == 12
    assert first_settlement.void == 12
    assert second_settlement.inserted == 0
    assert second_settlement.existing == 12
    rows = db.execute(
        "SELECT settlement_status::text, count(*) FROM decision_settlements "
        "GROUP BY settlement_status"
    ).fetchall()
    assert rows == [("VOID", 12)]
    assert (
        classify_match_winner_settlement(
            outcome_status="WALKOVER", winner_side=None, selection_side=1
        )
        == "VOID"
    )
    assert (
        classify_match_winner_settlement(
            outcome_status="WALKOVER", winner_side=None, selection_side=2
        )
        == "VOID"
    )


def test_reconciliation_is_record_only_and_idempotent(db: psycopg.Connection):
    target, evidence = _seed_reconciliation_pair(db)
    identity_before = db.execute(
        "SELECT player_id, source_system, source_player_id FROM player_source_ids "
        "ORDER BY player_id, source_system, source_player_id"
    ).fetchall()
    plan = build_plan(db, repair_id=REPAIR_RECONCILE, now=OBSERVED_AT)

    assert plan.status == STATUS_READY
    assert plan.apply_safe is True
    assert plan.evidence["automatic_policy"]["rejection_reason"] == "TIME_MISMATCH"
    assert plan.after is not None
    assert plan.after["target_result_written"] is False
    assert plan.after["identity_merge"] is False
    sql = " ".join(statement.sql for statement in plan.statements)
    assert "predictions" not in sql
    assert "decisions" not in sql
    assert "UPDATE " not in sql
    assert "DELETE " not in sql

    applied = apply_plan(
        db,
        repair_id=REPAIR_RECONCILE,
        authorization=APPROVAL_PHRASE,
        expected_sha256=plan.plan_sha256,
        now=OBSERVED_AT,
    )
    assert applied["status"] == STATUS_APPLIED
    assert (
        db.execute(
            "SELECT count(*) FROM cross_provider_match_reconciliations"
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT count(*) FROM match_result_observations WHERE match_id = %s",
            (target,),
        ).fetchone()[0]
        == 0
    )
    assert (
        db.execute(
            "SELECT count(*) FROM match_result_observations WHERE match_id = %s",
            (evidence,),
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT player_id, source_system, source_player_id FROM player_source_ids "
            "ORDER BY player_id, source_system, source_player_id"
        ).fetchall()
        == identity_before
    )

    second_plan = build_plan(db, repair_id=REPAIR_RECONCILE, now=OBSERVED_AT)
    assert second_plan.status == STATUS_ALREADY_APPLIED
    second = apply_plan(
        db,
        repair_id=REPAIR_RECONCILE,
        authorization=APPROVAL_PHRASE,
        expected_sha256=second_plan.plan_sha256,
        now=OBSERVED_AT,
    )
    assert second["status"] == STATUS_ALREADY_APPLIED
    assert (
        db.execute(
            "SELECT count(*) FROM cross_provider_match_reconciliations"
        ).fetchone()[0]
        == 1
    )


def test_130717_remains_blocked(db: psycopg.Connection):
    plan = build_plan(db, repair_id=REPAIR_130717, now=OBSERVED_AT)
    assert plan.status == STATUS_BLOCKED
    assert plan.apply_safe is False
    assert plan.statements == ()
    applied = apply_plan(
        db,
        repair_id=REPAIR_130717,
        authorization=APPROVAL_PHRASE,
        expected_sha256=plan.plan_sha256,
        now=OBSERVED_AT,
    )
    assert applied["status"] == STATUS_BLOCKED
