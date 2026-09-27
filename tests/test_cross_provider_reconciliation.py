"""Focused read-only evaluator tests for cross-provider reconciliation policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

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
