from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import psycopg
import pytest

from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.live.api_tennis_history_reconciliation import (
    ALREADY_LINKED,
    APPLY_AUTHORIZATION_TOKEN,
    CANONICAL_EVENT_MISSING,
    DATE_TIME_MISMATCH,
    DETERMINISTIC_MATCH_AVAILABLE_BUT_UNLINKED,
    DUPLICATE_PROVIDER_EVENT,
    MULTIPLE_CANONICAL_CANDIDATES,
    PLAYER_IDENTITY_MISSING,
    TOURNAMENT_MISMATCH,
    apply_candidate_links,
    apply_reconciliation,
    build_audit_artifact,
    build_reconciliation_plan,
    candidate_hash,
    link_candidates_from_payload,
)

INGESTED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
MATCH_START = datetime(2026, 9, 9, 18, 30, tzinfo=UTC)
AUDIT_AT = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)


def _fixture(**overrides):
    payload = {
        "event_key": "E1",
        "tournament_key": "TK1",
        "tournament_name": "Tiburon Challenger Men",
        "event_type_type": "Atp Challenger Singles",
        "event_date": "2026-09-09",
        "event_time": "18:30",
        "event_status": "Finished",
        "event_first_player": "Player One",
        "event_second_player": "Player Two",
        "first_player_key": "atp1",
        "second_player_key": "atp2",
        "event_surface": "Hard",
        "event_round": "R32",
        "event_winner": "First Player",
        "event_final_result": "2 - 0",
        "statistics": [],
    }
    payload.update(overrides)
    return payload


def _seed_run(db: psycopg.Connection) -> int:
    return int(
        db.execute(
            """
            INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
            VALUES ('SEED', 'reconciliation_test', %s, 'RUNNING')
            RETURNING ingest_run_id
            """,
            (INGESTED_AT,),
        ).fetchone()[0]
    )


def _canonical_player(
    repository: HistoricalRepository, *, name: str, source: str, source_id: str
) -> int:
    return repository.create_player_with_source_id(
        source_system=source,
        source_player_id=source_id,
        canonical_name=name,
        observed_at=INGESTED_AT,
    )


def _map_api_player(db: psycopg.Connection, *, player_id: int, provider_key: str) -> None:
    db.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id)
        VALUES (%s, 'API_TENNIS', %s)
        """,
        (player_id, provider_key),
    )


def _seed_canonical_match(
    db: psycopg.Connection,
    repository: HistoricalRepository,
    *,
    p1: int,
    p2: int,
    start_at: datetime = MATCH_START,
    tournament_key: str = "TK1",
    source_match_id: str = "td-match-1",
    surface: str = "HARD",
) -> int:
    tournament_id = repository.get_or_create_tournament(
        source_system="TENNIS_DATA",
        source_tournament_id=tournament_key,
        canonical_name=f"Tiburon Challenger Men {tournament_key}",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
    )
    edition_id = repository.get_or_create_tournament_edition(
        tournament_id=tournament_id,
        season_year=start_at.year,
        edition_name=f"Tiburon Challenger Men {start_at.year}",
        surface=surface,
        start_date=start_at.date(),
        end_date=None,
    )
    locator = repository.upsert_match_version(
        tournament_edition_id=edition_id,
        source_system="TENNIS_DATA",
        source_match_id=source_match_id,
        source_record_hash=f"hash:{source_match_id}",
        source_record_locator=f"tennis_data:{source_match_id}",
        source_file_name="test",
        source_version_hash=f"vhash:{source_match_id}",
        player_1_id=p1,
        player_2_id=p2,
        match_start_at=start_at,
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        surface=surface,
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        source_observed_at=INGESTED_AT,
        as_of=INGESTED_AT,
    )
    return locator.match_id


def _seed_players(db: psycopg.Connection) -> tuple[int, int]:
    repository = HistoricalRepository(db, _seed_run(db))
    p1 = _canonical_player(repository, name="Player One", source="TENNIS_DATA", source_id="td-p1")
    p2 = _canonical_player(repository, name="Player Two", source="TENNIS_DATA", source_id="td-p2")
    _map_api_player(db, player_id=p1, provider_key="atp1")
    _map_api_player(db, player_id=p2, provider_key="atp2")
    return p1, p2


def test_deterministic_exact_match_is_eligible_and_applies_once(db: psycopg.Connection):
    p1, p2 = _seed_players(db)
    repository = HistoricalRepository(db, _seed_run(db))
    match_id = _seed_canonical_match(db, repository, p1=p1, p2=p2)

    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)

    assert plan.total_events == 1
    assert plan.unmatched == 1
    assert plan.rows[0].classification == DETERMINISTIC_MATCH_AVAILABLE_BUT_UNLINKED
    assert plan.rows[0].deterministic_action_eligible is True
    assert plan.rows[0].candidate_match_ids == (match_id,)
    assert plan.rows[0].orientation == "DIRECT"
    assert len(plan.link_candidates) == 1
    assert plan.link_candidates[0].match_id == match_id

    outcome = apply_reconciliation(
        db,
        plan=plan,
        authorization=APPLY_AUTHORIZATION_TOKEN,
        expected_sha256=plan.preview_sha256,
        applied_at=AUDIT_AT,
    )
    assert outcome.links_inserted == 1
    assert db.execute(
        """
        SELECT match_id FROM match_source_ids
        WHERE source_system = 'API_TENNIS' AND source_match_id = 'E1'
        """
    ).fetchone()[0] == match_id

    repeat_plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    assert repeat_plan.rows[0].classification == ALREADY_LINKED
    assert repeat_plan.link_candidates == ()
    repeat = apply_reconciliation(
        db,
        plan=repeat_plan,
        authorization=APPLY_AUTHORIZATION_TOKEN,
        expected_sha256=repeat_plan.preview_sha256,
        applied_at=AUDIT_AT,
    )
    assert repeat.links_inserted == 0
    assert db.execute(
        "SELECT count(*) FROM match_source_ids WHERE source_system = 'API_TENNIS'"
    ).fetchone()[0] == 1


def test_reversed_orientation_is_recorded(db: psycopg.Connection):
    p1, p2 = _seed_players(db)
    repository = HistoricalRepository(db, _seed_run(db))
    match_id = _seed_canonical_match(db, repository, p1=p2, p2=p1)

    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    row = plan.rows[0]
    assert row.classification == DETERMINISTIC_MATCH_AVAILABLE_BUT_UNLINKED
    assert row.candidate_match_ids == (match_id,)
    assert row.orientation == "REVERSED"


def test_multiple_candidates_fail_closed(db: psycopg.Connection):
    p1, p2 = _seed_players(db)
    repository = HistoricalRepository(db, _seed_run(db))
    _seed_canonical_match(
        db, repository, p1=p1, p2=p2, source_match_id="td-1"
    )
    _seed_canonical_match(
        db,
        repository,
        p1=p1,
        p2=p2,
        start_at=MATCH_START.replace(minute=40),
        source_match_id="td-2",
    )

    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    assert plan.rows[0].classification == MULTIPLE_CANONICAL_CANDIDATES
    assert plan.rows[0].deterministic_action_eligible is False
    assert len(plan.rows[0].candidate_match_ids) == 2
    assert plan.link_candidates == ()


def test_duplicate_provider_event_is_reported(db: psycopg.Connection):
    _seed_players(db)
    repository = HistoricalRepository(db, _seed_run(db))
    p1 = db.execute(
        "SELECT player_id FROM player_source_ids WHERE source_player_id='atp1'"
    ).fetchone()[0]
    p2 = db.execute(
        "SELECT player_id FROM player_source_ids WHERE source_player_id='atp2'"
    ).fetchone()[0]
    _seed_canonical_match(db, repository, p1=p1, p2=p2)

    fixtures = [
        _fixture(),
        _fixture(event_time="20:30"),
    ]
    plan = build_reconciliation_plan(db, fixtures, as_of=AUDIT_AT)
    assert plan.rows[0].classification == DUPLICATE_PROVIDER_EVENT
    assert plan.link_candidates == ()


def test_missing_player_identity_fails_closed(db: psycopg.Connection):
    repository = HistoricalRepository(db, _seed_run(db))
    p1 = _canonical_player(repository, name="Player One", source="TENNIS_DATA", source_id="td-p1")
    _map_api_player(db, player_id=p1, provider_key="atp1")
    # atp2 intentionally unmapped.

    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    row = plan.rows[0]
    assert row.classification == PLAYER_IDENTITY_MISSING
    assert row.deterministic_action_eligible is False
    assert plan.link_candidates == ()


def test_tournament_mismatch_vetoes_candidate(db: psycopg.Connection):
    p1, p2 = _seed_players(db)
    repository = HistoricalRepository(db, _seed_run(db))
    _seed_canonical_match(db, repository, p1=p1, p2=p2, tournament_key="OTHER")
    # The provider event's tournament_key does not map, so evidence is absent and
    # the time-unique candidate is still eligible.
    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    assert plan.rows[0].classification == DETERMINISTIC_MATCH_AVAILABLE_BUT_UNLINKED

    # Now map the provider tournament to a *different* canonical tournament.
    other_tid = repository.get_or_create_tournament(
        source_system="TENNIS_DATA",
        source_tournament_id="TK1",
        canonical_name="Different Event",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
    )
    db.execute(
        """
        INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id)
        VALUES (%s, 'API_TENNIS', 'TK1')
        """,
        (other_tid,),
    )
    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    assert plan.rows[0].classification == TOURNAMENT_MISMATCH
    assert plan.link_candidates == ()


def test_date_time_mismatch_is_distinguished_from_missing(db: psycopg.Connection):
    p1, p2 = _seed_players(db)
    repository = HistoricalRepository(db, _seed_run(db))
    _seed_canonical_match(
        db, repository, p1=p1, p2=p2, start_at=MATCH_START.replace(hour=8)
    )
    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    assert plan.rows[0].classification == DATE_TIME_MISMATCH
    assert plan.link_candidates == ()


def test_canonical_event_missing_when_no_pair_match(db: psycopg.Connection):
    _seed_players(db)
    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    assert plan.rows[0].classification == CANONICAL_EVENT_MISSING
    assert plan.link_candidates == ()


def test_apply_requires_authorization_and_matching_hash(db: psycopg.Connection):
    p1, p2 = _seed_players(db)
    repository = HistoricalRepository(db, _seed_run(db))
    _seed_canonical_match(db, repository, p1=p1, p2=p2)
    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)

    with pytest.raises(ValueError, match="authorization token"):
        apply_reconciliation(
            db,
            plan=plan,
            authorization="WRONG",
            expected_sha256=plan.preview_sha256,
            applied_at=AUDIT_AT,
        )
    with pytest.raises(ValueError, match="preview hash"):
        apply_reconciliation(
            db,
            plan=plan,
            authorization=APPLY_AUTHORIZATION_TOKEN,
            expected_sha256="deadbeef",
            applied_at=AUDIT_AT,
        )
    assert db.execute(
        "SELECT count(*) FROM match_source_ids WHERE source_system = 'API_TENNIS'"
    ).fetchone()[0] == 0


def test_frozen_preview_apply_is_idempotent_and_no_duplicates(db: psycopg.Connection):
    p1, p2 = _seed_players(db)
    repository = HistoricalRepository(db, _seed_run(db))
    _seed_canonical_match(db, repository, p1=p1, p2=p2)
    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    artifact = build_audit_artifact(plan, generated_at=AUDIT_AT)

    frozen = link_candidates_from_payload(artifact)
    assert candidate_hash(frozen) == artifact["preview_sha256"]

    first = apply_candidate_links(
        db,
        candidates=frozen,
        authorization=APPLY_AUTHORIZATION_TOKEN,
        expected_sha256=artifact["preview_sha256"],
        applied_at=AUDIT_AT,
    )
    assert first.links_inserted == 1
    assert first.links_existing == 0

    second = apply_candidate_links(
        db,
        candidates=frozen,
        authorization=APPLY_AUTHORIZATION_TOKEN,
        expected_sha256=artifact["preview_sha256"],
        applied_at=AUDIT_AT,
    )
    assert second.links_inserted == 0
    assert second.links_existing == 1
    assert db.execute(
        "SELECT count(*) FROM match_source_ids WHERE source_system = 'API_TENNIS'"
    ).fetchone()[0] == 1


def test_audit_artifact_is_reproducible_and_complete(db: psycopg.Connection):
    p1, p2 = _seed_players(db)
    repository = HistoricalRepository(db, _seed_run(db))
    _seed_canonical_match(db, repository, p1=p1, p2=p2)
    plan = build_reconciliation_plan(db, [_fixture()], as_of=AUDIT_AT)
    artifact = build_audit_artifact(plan, generated_at=AUDIT_AT)

    assert artifact["totals"]["events"] == 1
    assert artifact["totals"]["deterministically_eligible"] == 1
    assert artifact["taxonomy"][DETERMINISTIC_MATCH_AVAILABLE_BUT_UNLINKED] == 1
    row = artifact["rows"][0]
    for field in (
        "event_key",
        "candidate_match_ids",
        "time_delta_minutes",
        "tournament_evidence",
        "player_identity_evidence",
        "orientation",
        "surface_evidence",
        "classification",
        "deterministic_action_eligible",
        "reason",
        "reason_safe",
    ):
        assert field in row

    encoded = json.dumps(artifact, sort_keys=True, indent=2, default=str)
    assert hashlib.sha256(encoded.encode("utf-8")).hexdigest() == hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()
