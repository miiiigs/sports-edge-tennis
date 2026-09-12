from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

import psycopg
import pytest

from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.clv import finalize_closing_evidence, load_clv_summary
from sports_edge_tennis.manager import (
    ensure_manager_policy_version_v1,
    evaluate_and_persist_manager_consensus,
)
from sports_edge_tennis.market import (
    SharpReferenceSpec,
    ensure_market_policy_version_v1,
    evaluate_and_persist_market_slate,
)

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"
AGENTS = (A1, A2, A3)
SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")
CLV_FIXTURE_PROVIDER = "CLV_FIXTURE_PROVIDER"


def _dt(hour: int, minute: int = 0) -> datetime:
    # Match and quote evidence stays safely in the past. Prospective CLV activation
    # is derived separately from the decision's real database-created timestamp.
    return datetime(2026, 9, 1, hour, minute, tzinfo=timezone.utc)


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
    """Create canonical Predictive -> Manager -> Market lineage for CLV tests."""
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
                VALUES (%s, %s, 'clv-fixture-model', 'clv-fixture-feature',
                        'params-v1', '{"clv_fixture": true}'::jsonb)
                RETURNING agent_config_version_id
                """,
                (agent, f"{agent.lower()}-clv-fixture-v1"),
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
                    'clv-fixture-model', 'clv-fixture-feature', %s, %s, %s
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


def _live_lineage(
    conn: psycopg.Connection,
    *,
    match_id: int,
    source_system: str,
    bookmaker_code: str,
    snapshot_id: str,
    observed_at: datetime,
) -> tuple[int, int]:
    """Create/reuse the minimum canonical LIVE_PROVIDER provenance for one quote pair."""
    if source_system not in {"SHARP", "OFFERED"}:
        raise AssertionError(f"invalid live normalized source for CLV fixture: {source_system}")

    bookmaker_key = f"{source_system.lower()}:{bookmaker_code.lower()}"
    mapping_row = conn.execute(
        """
        SELECT provider_bookmaker_mapping_id
        FROM provider_bookmaker_mappings
        WHERE provider_code = %s
          AND provider_bookmaker_key = %s
        ORDER BY mapping_version DESC
        LIMIT 1
        """,
        (CLV_FIXTURE_PROVIDER, bookmaker_key),
    ).fetchone()
    if mapping_row is None:
        mapping_id = _fetchval(
            conn,
            """
            INSERT INTO provider_bookmaker_mappings (
                provider_code, provider_bookmaker_key, mapping_version,
                bookmaker_code, normalization_class, effective_from
            )
            VALUES (%s, %s, 1, %s, %s, %s)
            RETURNING provider_bookmaker_mapping_id
            """,
            (
                CLV_FIXTURE_PROVIDER,
                bookmaker_key,
                bookmaker_code,
                source_system,
                _dt(0),
            ),
        )
    else:
        mapping_id = mapping_row[0]

    observation_key = f"{source_system}:{bookmaker_code}:{match_id}:{snapshot_id}"
    raw_row = conn.execute(
        """
        SELECT raw_odds_observation_id
        FROM raw_odds_observations
        WHERE provider_code = %s AND provider_observation_key = %s
        """,
        (CLV_FIXTURE_PROVIDER, observation_key),
    ).fetchone()
    if raw_row is None:
        ingest_run_id = _fetchval(
            conn,
            """
            INSERT INTO ingest_runs (
                source_system, source_dataset, started_at, status,
                source_version, source_file_name
            )
            VALUES (%s, 'CLV_FIXTURE_ODDS', %s, 'RUNNING', 'clv-fixture-v1', %s)
            RETURNING ingest_run_id
            """,
            (CLV_FIXTURE_PROVIDER, observed_at, f"{snapshot_id}.json"),
        )
        raw_id = _fetchval(
            conn,
            """
            INSERT INTO raw_odds_observations (
                ingest_run_id, provider_code, provider_event_id,
                provider_observation_key, provider_observed_at, received_at,
                observation_time, observation_time_basis,
                payload_hash, payload_json, adapter_version, normalizer_version
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, 'PROVIDER_TIME',
                %s, '{}'::jsonb, 'clv-fixture-adapter-v1', 'clv-fixture-normalizer-v1'
            )
            RETURNING raw_odds_observation_id
            """,
            (
                ingest_run_id,
                CLV_FIXTURE_PROVIDER,
                f"clv-match-{match_id}",
                observation_key,
                observed_at,
                observed_at,
                observed_at,
                f"clv-fixture:{observation_key}",
            ),
        )
    else:
        raw_id = raw_row[0]

    return int(raw_id), int(mapping_id)


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
    raw_id, mapping_id = _live_lineage(
        conn,
        match_id=match_id,
        source_system=source_system,
        bookmaker_code=bookmaker_code,
        snapshot_id=snapshot_id,
        observed_at=observed_at,
    )
    return int(
        _fetchval(
            conn,
            """
            INSERT INTO odds_snapshots (
                match_id, source_system, bookmaker_code, market_type,
                selection_type, decimal_price, market_status,
                source_snapshot_id, source_record_hash,
                source_observed_at, as_of, ingested_at, odds_origin,
                raw_odds_observation_id, provider_bookmaker_mapping_id
            )
            VALUES (
                %s, %s, %s, 'MATCH_WINNER', %s, %s, 'OPEN',
                %s, %s, %s, %s, %s, 'LIVE_PROVIDER', %s, %s
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
                raw_id,
                mapping_id,
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


def _evaluate_market_decision(
    conn: psycopg.Connection,
    *,
    run_id: int,
    market_policy_id: int,
) -> tuple[int, str, str, object]:
    outcome = evaluate_and_persist_market_slate(
        conn,
        prediction_run_id=run_id,
        market_policy_version_id=market_policy_id,
        decision_as_of=_dt(8, 40),
        market_execution_now=_dt(8, 41),
        sharp_reference=SHARP,
    )
    row = conn.execute(
        """
        SELECT decision_id, decision_status::text, reason_code::text
        FROM decisions
        WHERE prediction_run_id = %s
          AND manager_consensus_result_id IS NOT NULL
        ORDER BY decision_id DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    assert row is not None
    return int(row[0]), str(row[1]), str(row[2]), outcome


def _clv_window(conn: psycopg.Connection, decision_id: int) -> tuple[datetime, datetime]:
    row = conn.execute(
        """
        SELECT d.created_at, mv.match_start_at
        FROM decisions d
        JOIN LATERAL (
            SELECT match_start_at
            FROM match_versions
            WHERE match_id = d.match_id
            ORDER BY source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        WHERE d.decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    assert row is not None
    decision_created_at, match_start_at = row
    return (
        decision_created_at - timedelta(seconds=1),
        match_start_at + timedelta(hours=2, seconds=1),
    )


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
    _insert_pair(
        conn,
        match_id=match_id,
        source_system="OFFERED",
        bookmaker_code="BOOK_A",
        p1="2.10",
        p2="1.82",
        snapshot_id="book-open",
        observed_at=_dt(8, 35),
    )
    decision_id, status, reason, outcome = _evaluate_market_decision(
        conn,
        run_id=run_id,
        market_policy_id=market_policy_id,
    )
    assert status == "BET"
    assert reason == "NONE"
    assert outcome.qualified_bet_count == 1
    return decision_id, match_id, run_id, manager_id, market_policy_id


def test_latest_complete_prestart_pairs_drive_clv_and_poststart_quotes_are_rejected(
    db: psycopg.Connection,
):
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
        source_system="OFFERED",
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
        source_system="OFFERED",
        bookmaker_code="BOOK_A",
        p1="1.55",
        p2="2.60",
        snapshot_id="book-inplay",
        observed_at=_dt(12, 5),
    )
    activation_at, captured_at = _clv_window(db, decision_id)

    outcome = finalize_closing_evidence(
        db,
        activation_at=activation_at,
        captured_at=captured_at,
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
    raw_margin = (
        (Decimal("1") / Decimal("1.95"))
        + (Decimal("1") / Decimal("2.00"))
        - Decimal("1")
    )
    expected_margin = raw_margin.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
    assert clv[3] == expected_margin
    expected_clv = (
        Decimal("2.10")
        / Decimal("1.95")
        / (Decimal("1") + expected_margin)
        - Decimal("1")
    ).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
    assert clv[6] == expected_clv


def test_no_bet_receives_closing_evidence_without_fake_price_or_clv(
    db: psycopg.Connection,
):
    match_id = _seed_match(db, start=_dt(12))
    run_id, _, market_policy_id = _seed_parent(db, match_id=match_id)
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        p1="2.00",
        p2="2.00",
        snapshot_id="sharp-open-no-bet",
        observed_at=_dt(8, 30),
    )
    _insert_snapshot(
        db,
        match_id=match_id,
        source_system="OFFERED",
        bookmaker_code="BOOK_A",
        selection_type="PLAYER_1",
        price="1.70",
        snapshot_id="book-low-value",
        observed_at=_dt(8, 35),
    )
    decision_id, status, reason, outcome = _evaluate_market_decision(
        db,
        run_id=run_id,
        market_policy_id=market_policy_id,
    )
    assert status == "NO_BET"
    assert reason == "EDGE_BELOW_THRESHOLD"
    assert outcome.qualified_bet_count == 0

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
    activation_at, captured_at = _clv_window(db, decision_id)

    close_outcome = finalize_closing_evidence(
        db,
        activation_at=activation_at,
        captured_at=captured_at,
        sharp_reference=SHARP,
    )
    assert close_outcome.finalized_count == 1
    assert close_outcome.clv_created_count == 0
    assert close_outcome.clv_unavailable_count == 1

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


def test_missing_complete_same_book_close_never_fabricates_clv(
    db: psycopg.Connection,
):
    match_id = _seed_match(db, start=_dt(12))
    run_id, _, market_policy_id = _seed_parent(db, match_id=match_id)
    _insert_pair(
        db,
        match_id=match_id,
        source_system="SHARP",
        bookmaker_code="PINNACLE",
        p1="1.80",
        p2="2.10",
        snapshot_id="sharp-open",
        observed_at=_dt(8, 30),
    )
    _insert_snapshot(
        db,
        match_id=match_id,
        source_system="OFFERED",
        bookmaker_code="BOOK_A",
        selection_type="PLAYER_1",
        price="2.20",
        snapshot_id="book-selected-only",
        observed_at=_dt(8, 35),
    )
    decision_id, status, reason, outcome = _evaluate_market_decision(
        db,
        run_id=run_id,
        market_policy_id=market_policy_id,
    )
    assert status == "BET"
    assert reason == "NONE"
    assert outcome.qualified_bet_count == 1

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
    activation_at, captured_at = _clv_window(db, decision_id)

    close_outcome = finalize_closing_evidence(
        db,
        activation_at=activation_at,
        captured_at=captured_at,
        sharp_reference=SHARP,
    )
    assert close_outcome.finalized_count == 1
    assert close_outcome.clv_created_count == 0
    evidence = db.execute(
        """
        SELECT close_status, clv_available, clv_unavailable_reason
        FROM decision_close_evidence WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    assert evidence == ("COMPLETE", False, "SAME_BOOK_CLOSE_MISSING")


def test_activation_boundary_and_repeated_finalization_are_idempotent(
    db: psycopg.Connection,
):
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
        source_system="OFFERED",
        bookmaker_code="BOOK_A",
        p1="1.95",
        p2="2.00",
        snapshot_id="book-close",
        observed_at=_dt(11, 58),
    )
    activation_at, captured_at = _clv_window(db, decision_id)

    before_activation = finalize_closing_evidence(
        db,
        activation_at=activation_at + timedelta(seconds=2),
        captured_at=captured_at,
        sharp_reference=SHARP,
    )
    assert before_activation.eligible_count == 0
    assert db.execute("SELECT count(*) FROM decision_close_evidence").fetchone()[0] == 0

    first = finalize_closing_evidence(
        db,
        activation_at=activation_at,
        captured_at=captured_at,
        sharp_reference=SHARP,
    )
    second = finalize_closing_evidence(
        db,
        activation_at=activation_at,
        captured_at=captured_at + timedelta(hours=1),
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
        source_system="OFFERED",
        bookmaker_code="BOOK_A",
        p1="1.95",
        p2="2.00",
        snapshot_id="book-close",
        observed_at=_dt(11, 58),
    )
    activation_at, captured_at = _clv_window(db, decision_id)
    finalize_closing_evidence(
        db,
        activation_at=activation_at,
        captured_at=captured_at,
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
    assert summary.activation_at == activation_at

    with pytest.raises(psycopg.Error):
        db.execute(
            "UPDATE decision_close_evidence SET close_status = 'MISSING' WHERE decision_id = %s",
            (decision_id,),
        )