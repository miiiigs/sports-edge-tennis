"""Focused read-only evaluator tests for cross-provider reconciliation policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.data import cross_provider_reconciliation as cpr

BASE_START = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _mk_player(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (name,),
    ).fetchone()[0]


def _mk_tournament(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES (%s, %s, %s) RETURNING tournament_id",
        (name, "CHALLENGER", "CHALLENGER"),
    ).fetchone()[0]


def _mk_edition(conn: psycopg.Connection, tournament_id: int, season: int, name: str) -> int:
    return conn.execute(
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) "
        "VALUES (%s, %s, %s) RETURNING tournament_edition_id",
        (tournament_id, season, name),
    ).fetchone()[0]


def _mk_match(conn: psycopg.Connection, edition_id: int) -> int:
    return conn.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]


def _mk_version(
    conn: psycopg.Connection,
    match_id: int,
    p1: int,
    p2: int,
    start: datetime,
    *,
    tier: str = "CHALLENGER",
    scope: str = "CHALLENGER",
) -> int:
    ts = datetime.now(UTC) - timedelta(minutes=1)
    return conn.execute(
        "INSERT INTO match_versions (match_id, player_1_id, player_2_id, match_start_at, "
        "tour_scope, competition_tier, best_of_sets, match_status, source_observed_at, as_of) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING match_version_id",
        (match_id, p1, p2, start, scope, tier, 3, "SCHEDULED", ts, ts),
    ).fetchone()[0]


def _mk_source(conn: psycopg.Connection, match_id: int, system: str, sid: str) -> None:
    conn.execute(
        "INSERT INTO match_source_ids (match_id, source_system, source_match_id) "
        "VALUES (%s, %s, %s)",
        (match_id, system, sid),
    )


def _mk_result(
    conn: psycopg.Connection,
    match_id: int,
    *,
    outcome: str = "COMPLETED",
    winner_side: int | None = 1,
    retired_side: int | None = None,
    sets: tuple[int | None, int | None] = (2, 0),
    games: tuple[int | None, int | None] = (12, 7),
    scoreline: str | None = "6-3 6-4",
) -> int:
    ts = datetime.now(UTC) - timedelta(minutes=1)
    return conn.execute(
        "INSERT INTO match_result_observations (match_id, outcome_status, winner_side, "
        "retired_side, sets_won_player_1, sets_won_player_2, games_won_player_1, "
        "games_won_player_2, scoreline_text, source_observed_at, as_of) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING match_result_observation_id",
        (
            match_id,
            outcome,
            winner_side,
            retired_side,
            sets[0],
            sets[1],
            games[0],
            games[1],
            scoreline,
            ts,
            ts,
        ),
    ).fetchone()[0]


def _scenario(
    conn: psycopg.Connection,
    *,
    reverse: bool = False,
    player_mismatch: bool = False,
    delta_minutes: int = 0,
    same_tournament: bool = True,
    same_season: bool = True,
    same_tier: bool = True,
    with_result: bool = True,
    with_source: bool = True,
    winner_side: int | None = 1,
    sets: tuple[int | None, int | None] = (2, 0),
    games: tuple[int | None, int | None] = (12, 7),
) -> tuple[int, int]:
    """Build a target/evidence pair. Returns (target_match_id, evidence_match_id)."""
    pa = _mk_player(conn, "Player A")
    pb = _mk_player(conn, "Player B")
    target_tournament = _mk_tournament(conn, "Tour X")
    evidence_tournament = (
        target_tournament if same_tournament else _mk_tournament(conn, "Tour Y")
    )
    target_edition = _mk_edition(conn, target_tournament, 2026, "Ed Target")
    evidence_edition = _mk_edition(
        conn, evidence_tournament, 2026 if same_season else 2025, "Ed Evidence"
    )
    target = _mk_match(conn, target_edition)
    evidence = _mk_match(conn, evidence_edition)

    if reverse:
        tp1, tp2, ep1, ep2 = pa, pb, pb, pa
    else:
        tp1, tp2, ep1, ep2 = pa, pb, pa, pb
    if player_mismatch:
        ep1 = _mk_player(conn, "Player C")

    _mk_version(conn, target, tp1, tp2, BASE_START)
    _mk_version(
        conn,
        evidence,
        ep1,
        ep2,
        BASE_START + timedelta(minutes=delta_minutes),
        tier="CHALLENGER" if same_tier else "ATP_250",
    )
    if with_source:
        _mk_source(conn, target, "SPORTRADAR", f"sr-{target}")
        _mk_source(conn, evidence, "API_TENNIS", f"at-{evidence}")
    if with_result:
        _mk_result(
            conn,
            evidence,
            winner_side=winner_side,
            sets=sets,
            games=games,
        )
    return target, evidence


def test_direct_exact_pair(db: psycopg.Connection):
    target, evidence = _scenario(db)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is True
    assert ev.classification == cpr.CLASSIFICATION_EXACT
    assert ev.player_order_mapping == cpr.PLAYER_ORDER_DIRECT
    assert ev.target_tournament_id == ev.evidence_tournament_id
    assert ev.target_tournament_edition_id != ev.evidence_tournament_edition_id


def test_reversed_exact_pair(db: psycopg.Connection):
    target, evidence = _scenario(db, reverse=True)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is True
    assert ev.classification == cpr.CLASSIFICATION_REVERSED
    assert ev.player_order_mapping == cpr.PLAYER_ORDER_REVERSED


def test_direct_winner_translation(db: psycopg.Connection):
    target, evidence = _scenario(db, winner_side=2)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.evidence_winner_side == 2
    assert ev.translated_winner_side == 2


def test_reversed_winner_translation(db: psycopg.Connection):
    target, evidence = _scenario(db, reverse=True, winner_side=1)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.evidence_winner_side == 1
    assert ev.translated_winner_side == 2


def test_reversed_sets_and_games_swap(db: psycopg.Connection):
    target, evidence = _scenario(
        db, reverse=True, sets=(2, 0), games=(12, 7)
    )
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.translated_sets_won_player_1 == 0
    assert ev.translated_sets_won_player_2 == 2
    assert ev.translated_games_won_player_1 == 7
    assert ev.translated_games_won_player_2 == 12


def test_player_mismatch_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db, player_mismatch=True)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_IDENTITY_CONFLICT


def test_start_delta_over_limit_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db, delta_minutes=16)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_TIME_MISMATCH


def test_tournament_mismatch_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db, same_tournament=False)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_TOURNAMENT_MISMATCH


def test_season_mismatch_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db, same_season=False)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_SEASON_MISMATCH


def test_tier_mismatch_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db, same_tier=False)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_TIER_MISMATCH


def test_missing_source_identity_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db, with_source=False)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_SOURCE_IDENTITY_MISSING


def test_ambiguous_source_identity_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _mk_source(db, evidence, "ODDSPAPI", f"od-{evidence}")
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_SOURCE_IDENTITY_AMBIGUOUS


def test_missing_terminal_result_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db, with_result=False)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_RESULT_MISSING


def test_evaluation_performs_zero_writes(db: psycopg.Connection):
    tracked = (
        "players",
        "tournaments",
        "tournament_editions",
        "matches",
        "match_versions",
        "match_source_ids",
        "match_result_observations",
        "cross_provider_match_reconciliations",
    )

    def counts() -> dict[str, int]:
        return {
            t: int(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
            for t in tracked
        }

    target, evidence = _scenario(db)
    before = counts()
    cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=target
    )
    assert counts() == before


REVIEWED_AT = datetime(2026, 5, 2, 9, 0, tzinfo=UTC)


def _apply(
    db: psycopg.Connection,
    target: int,
    evidence: int,
    *,
    reviewed_at: datetime = REVIEWED_AT,
) -> cpr.CrossProviderReconciliationApplyResult:
    return cpr.apply_cross_provider_reconciliation(
        db,
        target_match_id=target,
        evidence_match_id=evidence,
        reviewed_at=reviewed_at,
    )


def _count(db: psycopg.Connection, table: str, where: str = "", params: tuple = ()) -> int:
    sql = f"SELECT count(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return int(db.execute(sql, params).fetchone()[0])


def test_apply_direct_returns_applied(db: psycopg.Connection):
    target, evidence = _scenario(db)
    result = _apply(db, target, evidence)
    assert result.status == cpr.STATUS_APPLIED
    assert result.reconciliation_id is not None
    assert result.target_result_observation_id is not None
    assert result.classification == cpr.CLASSIFICATION_EXACT
    assert result.already_reconciled is False


def test_apply_reversed_returns_applied(db: psycopg.Connection):
    target, evidence = _scenario(db, reverse=True)
    result = _apply(db, target, evidence)
    assert result.status == cpr.STATUS_APPLIED
    assert result.classification == cpr.CLASSIFICATION_REVERSED
    assert result.player_order_mapping == cpr.PLAYER_ORDER_REVERSED


def test_apply_inserts_reconciliation_row(db: psycopg.Connection):
    target, evidence = _scenario(db)
    result = _apply(db, target, evidence)
    row = db.execute(
        "SELECT canonical_target_match_id, evidence_match_id, classification, "
        "player_order_mapping, reconciliation_policy_version "
        "FROM cross_provider_match_reconciliations"
    ).fetchone()
    assert _count(db, "cross_provider_match_reconciliations") == 1
    assert int(row[0]) == target
    assert int(row[1]) == evidence
    assert row[2] == cpr.CLASSIFICATION_EXACT
    assert row[3] == cpr.PLAYER_ORDER_DIRECT
    assert row[4] == cpr.POLICY_VERSION
    assert result.reconciliation_id == db.execute(
        "SELECT reconciliation_id FROM cross_provider_match_reconciliations"
    ).fetchone()[0]


def test_apply_appends_target_result(db: psycopg.Connection):
    target, evidence = _scenario(db)
    result = _apply(db, target, evidence)
    assert _count(db, "match_result_observations", "match_id = %s", (target,)) == 1
    assert result.target_result_observation_id is not None


def test_apply_direct_winner_preserved(db: psycopg.Connection):
    target, evidence = _scenario(db, winner_side=2)
    _apply(db, target, evidence)
    winner = db.execute(
        "SELECT winner_side FROM match_result_observations WHERE match_id = %s",
        (target,),
    ).fetchone()[0]
    assert int(winner) == 2


def test_apply_reversed_winner_translated(db: psycopg.Connection):
    target, evidence = _scenario(db, reverse=True, winner_side=1)
    _apply(db, target, evidence)
    winner = db.execute(
        "SELECT winner_side FROM match_result_observations WHERE match_id = %s",
        (target,),
    ).fetchone()[0]
    assert int(winner) == 2


def test_apply_reversed_sets_and_games_translated(db: psycopg.Connection):
    target, evidence = _scenario(db, reverse=True, sets=(2, 0), games=(12, 7))
    _apply(db, target, evidence)
    row = db.execute(
        "SELECT sets_won_player_1, sets_won_player_2, games_won_player_1, games_won_player_2 "
        "FROM match_result_observations WHERE match_id = %s",
        (target,),
    ).fetchone()
    assert tuple(int(v) for v in row) == (0, 2, 7, 12)


def test_apply_uses_reviewed_at_as_visibility_time(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _apply(db, target, evidence)
    row = db.execute(
        "SELECT source_observed_at, as_of, ingested_at FROM match_result_observations "
        "WHERE match_id = %s",
        (target,),
    ).fetchone()
    assert row[0] == REVIEWED_AT
    assert row[1] == REVIEWED_AT
    assert row[2] >= REVIEWED_AT


def test_apply_evidence_payload_preserves_original_timestamps(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _apply(db, target, evidence)
    payload = db.execute(
        "SELECT evidence_payload FROM cross_provider_match_reconciliations"
    ).fetchone()[0]
    original = db.execute(
        "SELECT match_result_observation_id, source_observed_at, as_of "
        "FROM match_result_observations WHERE match_id = %s",
        (evidence,),
    ).fetchone()
    assert payload["policy_version"] == cpr.POLICY_VERSION
    assert payload["evidence_result_observation_id"] == int(original[0])
    assert payload["evidence_source_observed_at"] == original[1].isoformat()
    assert payload["evidence_as_of"] == original[2].isoformat()
    assert payload["reviewed_at"] == REVIEWED_AT.isoformat()


def test_apply_second_call_returns_already_reconciled(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _apply(db, target, evidence)
    second = _apply(db, target, evidence)
    assert second.status == cpr.STATUS_ALREADY_RECONCILED
    assert second.already_reconciled is True


def test_apply_second_call_adds_zero_reconciliation_rows(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _apply(db, target, evidence)
    _apply(db, target, evidence)
    assert _count(db, "cross_provider_match_reconciliations") == 1


def test_apply_second_call_adds_zero_target_results(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _apply(db, target, evidence)
    _apply(db, target, evidence)
    assert _count(db, "match_result_observations", "match_id = %s", (target,)) == 1


def test_apply_equivalent_existing_result_no_duplicate(db: psycopg.Connection):
    target, evidence = _scenario(db, winner_side=1)
    existing_id = _mk_result(db, target, winner_side=1)
    result = _apply(db, target, evidence)
    assert result.status == cpr.STATUS_EXISTING_EQUIVALENT_RESULT
    assert result.existing_equivalent_result is True
    assert result.target_result_observation_id == existing_id
    assert _count(db, "match_result_observations", "match_id = %s", (target,)) == 1


def test_apply_equivalent_existing_result_still_records_provenance(db: psycopg.Connection):
    target, evidence = _scenario(db, winner_side=1)
    _mk_result(db, target, winner_side=1)
    _apply(db, target, evidence)
    assert _count(db, "cross_provider_match_reconciliations") == 1


def test_apply_conflicting_target_result_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db, winner_side=1)
    _mk_result(db, target, winner_side=2)
    result = _apply(db, target, evidence)
    assert result.status == cpr.STATUS_RESULT_CONFLICT
    assert _count(db, "cross_provider_match_reconciliations") == 0
    assert _count(db, "match_result_observations", "match_id = %s", (target,)) == 1


def test_apply_unsafe_evaluation_writes_nothing(db: psycopg.Connection):
    target, evidence = _scenario(db, player_mismatch=True)
    result = _apply(db, target, evidence)
    assert result.status == cpr.STATUS_UNSAFE_EVALUATION
    assert _count(db, "cross_provider_match_reconciliations") == 0
    assert _count(db, "match_result_observations", "match_id = %s", (target,)) == 0


def test_apply_rolls_back_on_second_write_failure(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
):
    target, evidence = _scenario(db)

    def _boom(*args, **kwargs):
        raise RuntimeError("forced failure after reconciliation insert")

    monkeypatch.setattr(cpr, "_insert_target_result", _boom)
    with pytest.raises(RuntimeError):
        _apply(db, target, evidence)
    assert _count(db, "cross_provider_match_reconciliations") == 0
    assert _count(db, "match_result_observations", "match_id = %s", (target,)) == 0


def test_apply_mutates_no_other_tables(db: psycopg.Connection):
    tracked = (
        "players",
        "tournaments",
        "tournament_editions",
        "matches",
        "match_versions",
        "match_source_ids",
        "predictions",
        "manager_consensus_results",
    )

    def counts() -> dict[str, int]:
        return {
            t: int(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
            for t in tracked
        }

    target, evidence = _scenario(db)
    before = counts()
    _apply(db, target, evidence)
    assert counts() == before


def test_requested_target_provider_ignores_unrelated_rows(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _mk_source(db, target, "ODDSPAPI", f"od-{target}")
    ev = cpr.evaluate_cross_provider_reconciliation(
        db,
        target_match_id=target,
        evidence_match_id=evidence,
        target_source_system="SPORTRADAR",
        evidence_source_system="API_TENNIS",
    )
    assert ev.apply_safe is True
    assert ev.target_source_system == "SPORTRADAR"
    assert ev.evidence_source_system == "API_TENNIS"


def test_requested_evidence_provider_ignores_unrelated_rows(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _mk_source(db, evidence, "PINNAPI", f"pin-{evidence}")
    ev = cpr.evaluate_cross_provider_reconciliation(
        db,
        target_match_id=target,
        evidence_match_id=evidence,
        target_source_system="SPORTRADAR",
        evidence_source_system="API_TENNIS",
    )
    assert ev.apply_safe is True
    assert ev.evidence_source_match_id == f"at-{evidence}"


def test_requested_provider_missing_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db,
        target_match_id=target,
        evidence_match_id=evidence,
        target_source_system="PINNAPI",
        evidence_source_system="API_TENNIS",
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_SOURCE_IDENTITY_MISSING


def test_requested_provider_duplicate_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _mk_source(db, target, "SPORTRADAR", f"sr2-{target}")
    ev = cpr.evaluate_cross_provider_reconciliation(
        db,
        target_match_id=target,
        evidence_match_id=evidence,
        target_source_system="SPORTRADAR",
        evidence_source_system="API_TENNIS",
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_SOURCE_IDENTITY_AMBIGUOUS


def test_no_provider_single_source_succeeds(db: psycopg.Connection):
    target, evidence = _scenario(db)
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is True


def test_no_provider_multiple_sources_fails_closed(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _mk_source(db, target, "ODDSPAPI", f"od2-{target}")
    ev = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=target, evidence_match_id=evidence
    )
    assert ev.apply_safe is False
    assert ev.rejection_reason == cpr.REASON_SOURCE_IDENTITY_AMBIGUOUS


def test_apply_with_providers_succeeds_with_unrelated_rows(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _mk_source(db, target, "ODDSPAPI", f"od3-{target}")
    result = cpr.apply_cross_provider_reconciliation(
        db,
        target_match_id=target,
        evidence_match_id=evidence,
        reviewed_at=REVIEWED_AT,
        target_source_system="SPORTRADAR",
        evidence_source_system="API_TENNIS",
    )
    assert result.status == cpr.STATUS_APPLIED
    assert (
        _count(db, "cross_provider_match_reconciliations") == 1
    )


def test_apply_stores_selected_provider_ids(db: psycopg.Connection):
    target, evidence = _scenario(db)
    _mk_source(db, target, "ODDSPAPI", f"od4-{target}")
    cpr.apply_cross_provider_reconciliation(
        db,
        target_match_id=target,
        evidence_match_id=evidence,
        reviewed_at=REVIEWED_AT,
        target_source_system="SPORTRADAR",
        evidence_source_system="API_TENNIS",
    )
    row = db.execute(
        "SELECT target_source_system, target_source_match_id, "
        "evidence_source_system, evidence_source_match_id "
        "FROM cross_provider_match_reconciliations"
    ).fetchone()
    assert row[0] == "SPORTRADAR"
    assert row[1] == f"sr-{target}"
    assert row[2] == "API_TENNIS"
    assert row[3] == f"at-{evidence}"
    payload = db.execute(
        "SELECT evidence_payload FROM cross_provider_match_reconciliations"
    ).fetchone()[0]
    assert payload["target_source_system"] == "SPORTRADAR"
    assert payload["target_source_match_id"] == f"sr-{target}"
    assert payload["evidence_source_system"] == "API_TENNIS"
    assert payload["evidence_source_match_id"] == f"at-{evidence}"
