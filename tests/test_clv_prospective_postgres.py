from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.clv import finalize_closing_evidence, load_clv_summary
from sports_edge_tennis.market import SharpReferenceSpec

SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 12, hour, minute, tzinfo=timezone.utc)


def _fetchval(conn: psycopg.Connection, sql: str, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_match(conn: psycopg.Connection, *, start: datetime) -> int:
    p1 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES ('CLV Player One') RETURNING player_id",
    )
    p2 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES ('CLV Player Two') RETURNING player_id",
    )
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('CLV ATP', 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name)
        VALUES (%s, 2026, 'CLV ATP 2026')
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
        (match_id, p1, p2, start, _dt(8), _dt(8), _dt(8)),
    )
    return int(match_id)


def _seed_parent(conn: psycopg.Connection, *, match_id: int) -> tuple[int, int, int]:
    run_as_of = _dt(8)
    run_id = _fetchval(
        conn,
        """
        INSERT INTO prediction_runs (
            run_status, as_of, started_at, prediction_finalized_at,
            ended_at, prediction_contract_version
        )
        VALUES ('COMPLETED', %s, %s, %s, %s, 'predictive_agent_result_v1')
        RETURNING prediction_run_id
        """,
        (run_as_of, run_as_of, _dt(8, 10), _dt(8, 11)),
    )
    conn.execute(
        "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
        (run_id, match_id),
    )
    manager_policy_id = _fetchval(
        conn,
        "SELECT manager_policy_version_id FROM manager_policy_versions ORDER BY manager_policy_version_id LIMIT 1",
    )
    manager_id = _fetchval(
        conn,
        """
        INSERT INTO manager_consensus_results (
            prediction_run_id, match_id, manager_policy_version_id,
            manager_status, selected_side, consensus_probability,
            active_agent_count, abstaining_agent_count, probability_spread,
            reason_code, as_of
        )
        VALUES (%s, %s, %s, 'CONSENSUS', 1, 0.60, 3, 0, 0.02, 'NONE', %s)
        RETURNING manager_consensus_result_id
        """,
        (run_id, match_id, manager_policy_id, run_as_of),
    )
    market_policy_id = _fetchval(
        conn,
        "SELECT market_policy_version_id FROM market_policy_versions ORDER BY market_policy_version_id LIMIT 1",
    )
    return int(run_id), int(manager_id), int(market_policy_id)


def _insert_snapshot(
    conn: psycopg.Connection,
    *,
    match_id: int,
    source_system: str,
    bookmaker_code: str,
    selection_type: str,
    price: str,
    snapshot_id: str,
    observed_at: datetime,
) -> int:
    return int(
        _fetchval(
            conn,
            """
            INSERT INTO odds_snapshots (
                match_id, source_system, bookmaker_code, market_type,
                selection_type, decimal_price, market_status,
                source_snapshot_id, source_record_hash,
                source_observed_at, as_of, ingested_at, odds_origin
            )
            VALUES (
                %s, %s, %s, 'MATCH_WINNER', %s, %s, 'OPEN',
                %s, %s, %s, %s, %s, 'LIVE_PROVIDER'
            )
            RETURNING odds_snapshot_id
            """,
            (
                match_id,
                source_system,
                bookmaker_code,
                selection_type,
                Decimal(price),
                snapshot_id,
                f"{snapshot_id}:{selection_type}",
                observed_at,
                observed_at,
                observed_at,
            ),
        )
    )


def _insert_pair(
    conn: psycopg.Connection,
    *,
    match_id: int,
    source_system: str,
    bookmaker_code: str,
    p1: str,
    p2: str,
    snapshot_id: str,
    observed_at: datetime,
) -> tuple[int, int]:
    return (
        _insert_snapshot(
            conn,
            match_id=match_id,
            source_system=source_system,
            bookmaker_code=bookmaker_code,
            selection_type="PLAYER_1",
            price=p1,
            snapshot_id=snapshot_id,
            observed_at=observed_at,
        ),
        _insert_snapshot(
            conn,
            match_id=match_id,
            source_system=source_system,
            bookmaker_code=bookmaker_code,
            selection_type="PLAYER_2",
            price=p2,
            snapshot_id=snapshot_id,
            observed_at=observed_at,
        ),
    )


def _insert_decision(
    conn: psycopg.Connection,
    *,
    match_id: int,
    run_id: int,
    manager_id: int,
    market_policy_id: int,
    status: str,
    created_at: datetime,
    offered_snapshot_id: int | None = None,
    offered_price: str | None = None,
) -> int:
    priced = status in {"BET", "VETOED"}
    reason = "NONE" if status == "BET" else ("VETO_TRIGGERED" if status == "VETOED" else "EDGE_BELOW_THRESHOLD")
    hash_value = ("a" if priced else "b") * 64
    decision_id = _fetchval(
        conn,
        """
        INSERT INTO decisions (
            match_id, prediction_run_id, signal_type, decision_status, reason_code,
            market_type, selection_type, selection_side,
            consensus_probability, consensus_fair_odds,
            market_decimal_price, sharp_reference_price,
            price_source_system, price_bookmaker_code,
            devig_method, devig_fair_odds, edge_pct, min_acceptable_odds,
            active_agent_count, abstaining_agent_count, directional_disagreement,
            probability_spread, model_version, feature_version,
            as_of, prediction_locked_at, created_at,
            manager_consensus_result_id, market_policy_version_id,
            snapshot_lineage_hash
        )
        VALUES (
            %s, %s, 'TYPE_A_MODEL_EDGE', %s, %s,
            'MATCH_WINNER', %s, %s,
            0.60, %s,
            %s, %s,
            %s, %s,
            %s, %s, %s, %s,
            3, 0, FALSE, 0.02, 'market_value_v1', 'type_a',
            %s, %s, %s,
            %s, %s, %s
        )
        RETURNING decision_id
        """,
        (
            match_id,
            run_id,
            status,
            reason,
            "PLAYER_1" if priced else None,
            1 if priced else None,
            Decimal("1.666667") if priced else None,
            Decimal(offered_price) if priced and offered_price is not None else None,
            Decimal("1.850000") if priced else None,
            "SOFT" if priced else None,
            "BOOK_A" if priced else None,
            "POWER" if priced else None,
            Decimal("1.900000") if priced else None,
            Decimal("0.260000") if priced else None,
            Decimal("1.750000") if priced else None,
            _dt(8, 40),
            _dt(8, 41),
            created_at,
            manager_id,
            market_policy_id,
            hash_value,
        ),
    )
    if priced and offered_snapshot_id is not None:
        conn.execute(
            """
            INSERT INTO decision_odds_snapshots (decision_id, odds_snapshot_id, snapshot_role)
            VALUES (%s, %s, 'OFFERED_SELECTION')
            """,
            (decision_id, offered_snapshot_id),
        )
    return int(decision_id)


def _seed_priced_decision(conn: psycopg.Connection, *, start: datetime = _dt(12)):
    match_id = _seed_match(conn, start=start)
    run_id, manager_id, market_policy_id = _seed_parent(conn, match_id=match_id)
    _insert_pair(
        conn,
        match_id=match_id,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        p1="1.80",
        p2="2.10",
        snapshot_id="sharp-open",
        observed_at=_dt(8, 30),
    )
    offered_p1, _ = _insert_pair(
        conn,
        match_id=match_id,
        source_system="SOFT",
        bookmaker_code="BOOK_A",
        p1="2.10",
        p2="1.82",
        snapshot_id="book-open",
        observed_at=_dt(8, 35),
    )
    decision_id = _insert_decision(
        conn,
        match_id=match_id,
        run_id=run_id,
        manager_id=manager_id,
        market_policy_id=market_policy_id,
        status="BET",
        created_at=_dt(8, 41),
        offered_snapshot_id=offered_p1,
        offered_price="2.10",
    )
    return decision_id, match_id, run_id, manager_id, market_policy_id


def test_latest_complete_prestart_pairs_drive_clv_and_poststart_quotes_are_rejected(db: psycopg.Connection):
    decision_id, match_id, *_ = _seed_priced_decision(db)

    sharp_close = _insert_pair(
        db,
        match_id=match_id,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        p1="1.90",
        p2="2.02",
        snapshot_id="sharp-close",
        observed_at=_dt(11, 55),
    )
    book_close = _insert_pair(
        db,
        match_id=match_id,
        source_system="SOFT",
        bookmaker_code="BOOK_A",
        p1="1.95",
        p2="2.00",
        snapshot_id="book-close",
        observed_at=_dt(11, 58),
    )
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        p1="1.60",
        p2="2.50",
        snapshot_id="sharp-inplay",
        observed_at=_dt(12, 5),
    )
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SOFT",
        bookmaker_code="BOOK_A",
        p1="1.55",
        p2="2.60",
        snapshot_id="book-inplay",
        observed_at=_dt(12, 5),
    )

    outcome = finalize_closing_evidence(
        db,
        activation_at=_dt(8),
        captured_at=_dt(14, 30),
        sharp_reference=SHARP,
    )

    assert outcome.finalized_count == 1
    assert outcome.complete_count == 1
    assert outcome.clv_created_count == 1

    evidence = db.execute(
        """
        SELECT close_status, sharp_player_1_snapshot_id, sharp_player_2_snapshot_id,
               same_book_player_1_snapshot_id, same_book_player_2_snapshot_id,
               sharp_player_1_price, same_book_player_1_price,
               clv_available, clv_unavailable_reason
        FROM decision_close_evidence
        WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    assert evidence[0] == "COMPLETE"
    assert tuple(evidence[1:3]) == sharp_close
    assert tuple(evidence[3:5]) == book_close
    assert evidence[5] == Decimal("1.900000")
    assert evidence[6] == Decimal("1.950000")
    assert evidence[7] is True
    assert evidence[8] is None

    clv = db.execute(
        """
        SELECT price_taken, closing_same_book_price, closing_sharp_price,
               closing_margin, closing_observed_at, calculation_version, clv_pct
        FROM decision_clv
        WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    assert clv[0] == Decimal("2.100000")
    assert clv[1] == Decimal("1.950000")
    assert clv[2] == Decimal("1.900000")
    expected_margin = (Decimal("1") / Decimal("1.95")) + (Decimal("1") / Decimal("2.00")) - Decimal("1")
    assert clv[3] == expected_margin
    expected_clv = (Decimal("2.10") / Decimal("1.95") / (Decimal("1") + expected_margin) - Decimal("1")).quantize(Decimal("0.00000001"))
    assert clv[6] == expected_clv


def test_no_bet_receives_closing_evidence_without_fake_price_or_clv(db: psycopg.Connection):
    match_id = _seed_match(db, start=_dt(12))
    run_id, manager_id, market_policy_id = _seed_parent(db, match_id=match_id)
    decision_id = _insert_decision(
        db,
        match_id=match_id,
        run_id=run_id,
        manager_id=manager_id,
        market_policy_id=market_policy_id,
        status="NO_BET",
        created_at=_dt(8, 42),
    )
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        p1="1.88",
        p2="2.04",
        snapshot_id="sharp-close-no-bet",
        observed_at=_dt(11, 57),
    )

    outcome = finalize_closing_evidence(
        db,
        activation_at=_dt(8),
        captured_at=_dt(14, 30),
        sharp_reference=SHARP,
    )
    assert outcome.finalized_count == 1
    assert outcome.clv_created_count == 0
    assert outcome.clv_unavailable_count == 1

    evidence = db.execute(
        """
        SELECT close_status, same_book_bookmaker_code, clv_available, clv_unavailable_reason
        FROM decision_close_evidence WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    assert evidence == ("COMPLETE", None, False, "NOT_PRICED_DECISION")
    assert db.execute(
        "SELECT count(*) FROM decision_clv WHERE decision_id = %s", (decision_id,)
    ).fetchone()[0] == 0


def test_missing_complete_same_book_close_never_fabricates_clv(db: psycopg.Connection):
    match_id = _seed_match(db, start=_dt(12))
    run_id, manager_id, market_policy_id = _seed_parent(db, match_id=match_id)
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        p1="1.90",
        p2="2.02",
        snapshot_id="sharp-close",
        observed_at=_dt(11, 55),
    )
    offered_p1 = _insert_snapshot(
        db,
        match_id=match_id,
        source_system="SOFT",
        bookmaker_code="BOOK_A",
        selection_type="PLAYER_1",
        price="2.10",
        snapshot_id="book-selected-only",
        observed_at=_dt(8, 35),
    )
    decision_id = _insert_decision(
        db,
        match_id=match_id,
        run_id=run_id,
        manager_id=manager_id,
        market_policy_id=market_policy_id,
        status="BET",
        created_at=_dt(8, 41),
        offered_snapshot_id=offered_p1,
        offered_price="2.10",
    )

    outcome = finalize_closing_evidence(
        db,
        activation_at=_dt(8),
        captured_at=_dt(14, 30),
        sharp_reference=SHARP,
    )
    assert outcome.finalized_count == 1
    assert outcome.clv_created_count == 0
    evidence = db.execute(
        "SELECT close_status, clv_available, clv_unavailable_reason FROM decision_close_evidence WHERE decision_id = %s",
        (decision_id,),
    ).fetchone()
    assert evidence == ("COMPLETE", False, "SAME_BOOK_CLOSE_MISSING")


def test_activation_boundary_and_repeated_finalization_are_idempotent(db: psycopg.Connection):
    decision_id, match_id, *_ = _seed_priced_decision(db)
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        p1="1.90",
        p2="2.02",
        snapshot_id="sharp-close",
        observed_at=_dt(11, 55),
    )
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SOFT",
        bookmaker_code="BOOK_A",
        p1="1.95",
        p2="2.00",
        snapshot_id="book-close",
        observed_at=_dt(11, 58),
    )

    before_activation = finalize_closing_evidence(
        db,
        activation_at=_dt(9),
        captured_at=_dt(14, 30),
        sharp_reference=SHARP,
    )
    assert before_activation.eligible_count == 0
    assert db.execute("SELECT count(*) FROM decision_close_evidence").fetchone()[0] == 0

    first = finalize_closing_evidence(
        db,
        activation_at=_dt(8),
        captured_at=_dt(14, 30),
        sharp_reference=SHARP,
    )
    second = finalize_closing_evidence(
        db,
        activation_at=_dt(8),
        captured_at=_dt(15),
        sharp_reference=SHARP,
    )
    assert first.finalized_count == 1
    assert second.eligible_count == 0
    assert db.execute(
        "SELECT count(*) FROM decision_close_evidence WHERE decision_id = %s", (decision_id,)
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT count(*) FROM decision_clv WHERE decision_id = %s", (decision_id,)
    ).fetchone()[0] == 1


def test_clv_summary_reports_close_and_priced_coverage(db: psycopg.Connection):
    decision_id, match_id, *_ = _seed_priced_decision(db)
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        p1="1.90",
        p2="2.02",
        snapshot_id="sharp-close",
        observed_at=_dt(11, 55),
    )
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SOFT",
        bookmaker_code="BOOK_A",
        p1="1.95",
        p2="2.00",
        snapshot_id="book-close",
        observed_at=_dt(11, 58),
    )
    finalize_closing_evidence(
        db,
        activation_at=_dt(8),
        captured_at=_dt(14, 30),
        sharp_reference=SHARP,
    )

    summary = load_clv_summary(db)
    assert summary.initialized is True
    assert summary.close_records == 1
    assert summary.complete_closes == 1
    assert summary.priced_decisions == 1
    assert summary.clv_records == 1
    assert summary.close_coverage == Decimal("1")
    assert summary.priced_clv_coverage == Decimal("1")
    assert summary.mean_clv is not None
    assert summary.median_clv is not None
    assert summary.activation_at == _dt(8)

    with pytest.raises(psycopg.Error):
        db.execute(
            "UPDATE decision_close_evidence SET close_status = 'MISSING' WHERE decision_id = %s",
            (decision_id,),
        )
