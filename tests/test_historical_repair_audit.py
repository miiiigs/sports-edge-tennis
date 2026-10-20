from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg

from sports_edge_tennis.live.historical_repair_audit import (
    ACTION_APPEND_EXPLICIT_WALKOVER_RESULT,
    ACTION_HOLD_DUPLICATE_FIXTURE,
    ACTION_PROVIDER_RESULT_REQUIRED,
    ACTION_RECONCILE_CROSS_PROVIDER_RESULT,
    SAFETY_BLOCKED,
    SAFETY_REQUIRES_OWNER_APPROVAL,
    BracketAdvancement,
    CrossSourceResultCandidate,
    PendingMatchEvidence,
    SamePlayerTimeConflict,
    classify_correction,
    find_cross_source_result_candidates,
    find_same_player_time_conflicts,
)


def _ingest_run(db: psycopg.Connection, observed_at: datetime) -> int:
    return db.execute(
        """
        INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
        VALUES ('TEST', 'historical-repair-audit', %s, 'COMPLETED')
        RETURNING ingest_run_id
        """,
        (observed_at,),
    ).fetchone()[0]


def _player(db: psycopg.Connection, name: str, observed_at: datetime) -> int:
    return db.execute(
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (name,),
    ).fetchone()[0]


def _edition(
    db: psycopg.Connection,
    *,
    tournament_id: int,
    name: str,
    observed_at: datetime,
) -> int:
    return db.execute(
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, surface, start_date
        )
        VALUES (%s, 2026, %s, 'HARD', %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, name, observed_at.date()),
    ).fetchone()[0]


def _tournament(db: psycopg.Connection, name: str) -> int:
    return db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
        (name,),
    ).fetchone()[0]


def _match(
    db: psycopg.Connection,
    *,
    edition_id: int,
    player_1_id: int,
    player_2_id: int,
    start_at: datetime,
    observed_at: datetime,
    source_system: str,
    source_match_id: str,
    status: str = "SCHEDULED",
) -> int:
    match_id = db.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    assert db.execute(
        "SELECT 1 FROM matches WHERE match_id = %s", (match_id,)
    ).fetchone()
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, surface, round_code, best_of_sets,
            qualifying_flag, match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3,
                FALSE, %s, %s, %s)
        """,
        (
            match_id,
            player_1_id,
            player_2_id,
            start_at,
            status,
            observed_at,
            observed_at,
        ),
    )
    ingest_run_id = _ingest_run(db, observed_at)
    db.execute(
        """
        INSERT INTO match_source_ids (
            match_id, source_system, source_match_id, ingest_run_id
        )
        VALUES (%s, %s, %s, %s)
        """,
        (match_id, source_system, source_match_id, ingest_run_id),
    )
    return match_id


def _result(
    db: psycopg.Connection,
    *,
    match_id: int,
    observed_at: datetime,
    outcome_status: str = "COMPLETED",
    winner_side: int | None = 1,
) -> None:
    db.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (match_id, outcome_status, winner_side, observed_at, observed_at),
    )


def _evidence(
    *,
    match_id: int,
    status: str = "SCHEDULED",
    bet_decisions: int = 0,
) -> PendingMatchEvidence:
    start = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    return PendingMatchEvidence(
        match_id=match_id,
        player_1_id=1,
        player_2_id=2,
        player_1="Player One",
        player_2="Player Two",
        tournament="Roanne",
        competition_tier="CHALLENGER",
        tournament_edition_id=1,
        match_start_at=start,
        match_status=status,
        surface="HARD",
        edition_surface="HARD",
        round_code="QF",
        best_of_sets=3,
        qualifying_flag=False,
        api_tennis_event_key="12171502",
        bet_decisions=bet_decisions,
        bet_sides=(1,) if bet_decisions else (),
    )


def test_explicit_walkover_is_proposed_as_void_not_an_ordinary_win():
    proposal = classify_correction(
        SimpleNamespace(match_id=132211, reason="TERMINAL_FIXTURE_WITHOUT_RESULT"),
        evidence=_evidence(match_id=132211, status="WALKOVER", bet_decisions=12),
        cross_source=None,
        same_player_conflict=None,
        bracket=BracketAdvancement(
            target_match_id=132211,
            advancing_side=1,
            player_1_later_matches=(132233,),
            player_2_later_matches=(),
        ),
    )

    assert proposal.action == ACTION_APPEND_EXPLICIT_WALKOVER_RESULT
    assert proposal.safety == SAFETY_REQUIRES_OWNER_APPROVAL
    assert proposal.after == {
        "outcome_status": "WALKOVER",
        "winner_side": 1,
        "settlement_status": "VOID",
        "scoreline_text": None,
    }
    assert proposal.writes == ("match_result_observations",)


def test_bracket_advancement_alone_never_becomes_a_completed_result():
    proposal = classify_correction(
        SimpleNamespace(match_id=130717, reason="PROVIDER_FIXTURE_NOT_REOBSERVED"),
        evidence=_evidence(match_id=130717, bet_decisions=10),
        cross_source=None,
        same_player_conflict=None,
        bracket=BracketAdvancement(
            target_match_id=130717,
            advancing_side=1,
            player_1_later_matches=(130805,),
            player_2_later_matches=(),
        ),
    )

    assert proposal.action == ACTION_PROVIDER_RESULT_REQUIRED
    assert proposal.safety == SAFETY_BLOCKED
    assert proposal.writes == ()
    assert proposal.after is None


def test_cross_source_duplicate_result_is_not_copied_onto_the_target():
    proposal = classify_correction(
        SimpleNamespace(match_id=131036, reason="TERMINAL_FIXTURE_WITHOUT_RESULT"),
        evidence=_evidence(match_id=131036, status="COMPLETED"),
        cross_source=CrossSourceResultCandidate(
            target_match_id=131036,
            evidence_match_id=131137,
            outcome_status="COMPLETED",
            winner_side=1,
            scoreline_text="2 - 0",
            source_observed_at=datetime(2026, 9, 27, 0, 14, tzinfo=UTC),
            evidence_source_system="API_TENNIS",
            evidence_source_match_id="12166029",
            target_source_system="SPORTRADAR",
        ),
        same_player_conflict=None,
        bracket=None,
    )

    assert proposal.action == ACTION_RECONCILE_CROSS_PROVIDER_RESULT
    assert proposal.writes == ("cross_provider_match_reconciliations",)
    assert proposal.after is not None
    assert proposal.after["target_result_written"] is False


def test_same_player_same_time_conflict_is_held_for_review():
    proposal = classify_correction(
        SimpleNamespace(match_id=131307, reason="TERMINAL_FIXTURE_WITHOUT_RESULT"),
        evidence=_evidence(match_id=131307, status="COMPLETED"),
        cross_source=None,
        same_player_conflict=SamePlayerTimeConflict(
            target_match_id=131307,
            other_match_id=131354,
            shared_player_id=1,
            shared_player="R. Te",
            target_opponent="J. Duckworth",
            other_opponent="A. Gea",
            target_start_at=datetime(2026, 9, 28, 3, 20, tzinfo=UTC),
            other_start_at=datetime(2026, 9, 28, 3, 25, tzinfo=UTC),
            target_has_result=False,
            other_has_result=True,
        ),
        bracket=None,
    )

    assert proposal.action == ACTION_HOLD_DUPLICATE_FIXTURE
    assert proposal.safety == SAFETY_BLOCKED
    assert proposal.writes == ()


def test_cross_source_duplicate_result_detection_uses_tournament_not_edition(
    db: psycopg.Connection,
):
    observed_at = datetime(2026, 9, 26, 11, 0, tzinfo=UTC)
    tournament_id = _tournament(db, "Plovdiv 4 (Bulgaria) - Qualification")
    target_edition = _edition(
        db, tournament_id=tournament_id, name="target", observed_at=observed_at
    )
    evidence_edition = _edition(
        db, tournament_id=tournament_id, name="evidence", observed_at=observed_at
    )
    player_1 = _player(db, "M. Alcala Gurri", observed_at)
    player_2 = _player(db, "C. Taberner", observed_at)
    target_match = _match(
        db,
        edition_id=target_edition,
        player_1_id=player_1,
        player_2_id=player_2,
        start_at=observed_at,
        observed_at=observed_at,
        source_system="SPORTRADAR",
        source_match_id="sr:sport_event:74753168",
        status="COMPLETED",
    )
    evidence_match = _match(
        db,
        edition_id=evidence_edition,
        player_1_id=player_1,
        player_2_id=player_2,
        start_at=observed_at + timedelta(minutes=40),
        observed_at=observed_at + timedelta(hours=1),
        source_system="API_TENNIS",
        source_match_id="12166029",
        status="COMPLETED",
    )
    _result(
        db,
        match_id=evidence_match,
        observed_at=observed_at + timedelta(hours=1),
    )

    candidates = find_cross_source_result_candidates(db, match_ids=[target_match])

    assert target_match in candidates
    candidate = candidates[target_match]
    assert candidate.evidence_match_id == evidence_match
    assert candidate.evidence_source_system == "API_TENNIS"
    assert candidate.target_source_system == "SPORTRADAR"
    assert candidate.outcome_status == "COMPLETED"


def test_same_player_same_time_conflict_detection(db: psycopg.Connection):
    observed_at = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)
    tournament_id = _tournament(db, "Beijing")
    edition_id = _edition(
        db, tournament_id=tournament_id, name="2026", observed_at=observed_at
    )
    shared = _player(db, "R. Te", observed_at)
    opponent_a = _player(db, "J. Duckworth", observed_at)
    opponent_b = _player(db, "A. Gea", observed_at)
    target_match = _match(
        db,
        edition_id=edition_id,
        player_1_id=shared,
        player_2_id=opponent_a,
        start_at=observed_at + timedelta(minutes=20),
        observed_at=observed_at,
        source_system="API_TENNIS",
        source_match_id="12166372",
        status="COMPLETED",
    )
    other_match = _match(
        db,
        edition_id=edition_id,
        player_1_id=shared,
        player_2_id=opponent_b,
        start_at=observed_at + timedelta(minutes=25),
        observed_at=observed_at,
        source_system="API_TENNIS",
        source_match_id="12166487",
        status="COMPLETED",
    )
    _result(
        db,
        match_id=other_match,
        observed_at=observed_at + timedelta(hours=1),
        winner_side=2,
    )

    conflicts = find_same_player_time_conflicts(db, match_ids=[target_match])

    assert target_match in conflicts
    conflict = conflicts[target_match]
    assert conflict.other_match_id == other_match
    assert conflict.shared_player == "R. Te"
    assert conflict.target_has_result is False
    assert conflict.other_has_result is True
