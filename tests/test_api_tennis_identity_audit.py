from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.live.api_tennis_identity_audit import (
    audit_canonical_duplicate_aliases,
    audit_provider_identity,
)

INGESTED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
MATCH_START = datetime(2026, 9, 9, 18, 30, tzinfo=UTC)
AS_OF = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)


def _run(db: psycopg.Connection) -> int:
    return int(
        db.execute(
            """
            INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
            VALUES ('SEED', 'identity_audit_test', %s, 'RUNNING')
            RETURNING ingest_run_id
            """,
            (INGESTED_AT,),
        ).fetchone()[0]
    )


def _seed_player_with_history(
    db: psycopg.Connection,
    repository: HistoricalRepository,
    *,
    name: str,
    source_id: str,
    suffix: str,
) -> int:
    player_id = repository.create_player_with_source_id(
        source_system="TENNIS_DATA",
        source_player_id=source_id,
        canonical_name=name,
        observed_at=INGESTED_AT,
    )
    opponent_id = repository.create_player_with_source_id(
        source_system="TENNIS_DATA",
        source_player_id=f"{source_id}-opponent",
        canonical_name=f"Opponent {suffix}",
        observed_at=INGESTED_AT,
    )
    tournament_id = repository.get_or_create_tournament(
        source_system="TENNIS_DATA",
        source_tournament_id=f"ident-{suffix}",
        canonical_name=f"Identity Event {suffix}",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
    )
    edition_id = repository.get_or_create_tournament_edition(
        tournament_id=tournament_id,
        season_year=MATCH_START.year,
        edition_name=f"Identity Event {suffix} {MATCH_START.year}",
        surface="HARD",
        start_date=MATCH_START.date(),
        end_date=None,
    )
    locator = repository.upsert_match_version(
        tournament_edition_id=edition_id,
        source_system="TENNIS_DATA",
        source_match_id=f"ident-match-{suffix}",
        source_record_hash=f"hash:ident-match-{suffix}",
        source_record_locator=f"tennis_data:ident-match-{suffix}",
        source_file_name="test",
        source_version_hash=f"vhash:ident-match-{suffix}",
        player_1_id=player_id,
        player_2_id=opponent_id,
        match_start_at=MATCH_START,
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="COMPLETED",
        source_observed_at=INGESTED_AT,
        as_of=INGESTED_AT,
    )
    repository.insert_match_result(
        match_id=locator.match_id,
        outcome_status="COMPLETED",
        winner_side=1,
        retired_side=None,
        sets_won_player_1=2,
        sets_won_player_2=0,
        games_won_player_1=12,
        games_won_player_2=7,
        scoreline_text="2-0",
        source_observed_at=INGESTED_AT,
        as_of=INGESTED_AT,
    )
    return player_id


def test_exact_provider_mapping_is_safe(db: psycopg.Connection):
    repository = HistoricalRepository(db, _run(db))
    player_id = repository.create_player_with_source_id(
        source_system="API_TENNIS",
        source_player_id="atp-known",
        canonical_name="Known Player",
        observed_at=INGESTED_AT,
    )
    rows = audit_provider_identity(
        db,
        provider_players=[("API_TENNIS", "atp-known", "Known Player")],
        as_of=AS_OF,
    )
    assert rows[0].safe is True
    assert rows[0].deterministic_resolution is True
    assert rows[0].exact_mapped_player_id == player_id
    assert rows[0].resolution_player_id == player_id


def test_unmapped_without_candidate_is_blocked(db: psycopg.Connection):
    rows = audit_provider_identity(
        db,
        provider_players=[("API_TENNIS", "atp-unknown", "Nobody Here")],
        as_of=AS_OF,
    )
    assert rows[0].safe is False
    assert rows[0].deterministic_resolution is False
    assert rows[0].conflict == "no_plausible_canonical_player"
    assert rows[0].candidate_player_ids == ()


def test_unique_alias_candidate_is_deterministic_but_not_auto_safe(db: psycopg.Connection):
    repository = HistoricalRepository(db, _run(db))
    player_id = _seed_player_with_history(
        db,
        repository,
        name="Rafael Nadal",
        source_id="td-nadal",
        suffix="nadal",
    )
    rows = audit_provider_identity(
        db,
        provider_players=[("API_TENNIS", "atp-nadal", "Rafael Nadal")],
        as_of=AS_OF,
    )
    assert rows[0].candidate_player_ids == (player_id,)
    assert rows[0].deterministic_resolution is True
    # Resolution is available but is not applied automatically by the audit.
    assert rows[0].safe is False
    assert rows[0].conflict is None


def test_multiple_alias_candidates_remain_ambiguous(db: psycopg.Connection):
    repository = HistoricalRepository(db, _run(db))
    first = _seed_player_with_history(
        db, repository, name="Rafael Nadal", source_id="td-nadal-a", suffix="a"
    )
    second = _seed_player_with_history(
        db, repository, name="Rafael Nadal", source_id="td-nadal-b", suffix="b"
    )
    rows = audit_provider_identity(
        db,
        provider_players=[("API_TENNIS", "atp-nadal", "Rafael Nadal")],
        as_of=AS_OF,
    )
    assert rows[0].deterministic_resolution is False
    assert rows[0].conflict == "multiple_plausible_canonical_players"
    assert set(rows[0].candidate_player_ids) == {first, second}


def test_duplicate_canonical_aliases_are_surfaced_not_merged(db: psycopg.Connection):
    repository = HistoricalRepository(db, _run(db))
    first = _seed_player_with_history(
        db, repository, name="Rafael Nadal", source_id="td-nadal-a", suffix="a"
    )
    second = _seed_player_with_history(
        db, repository, name="Rafael Nadal", source_id="td-nadal-b", suffix="b"
    )
    groups = audit_canonical_duplicate_aliases(db)
    matches = [group for group in groups if group.normalized_alias == "rafael nadal"]
    assert len(matches) == 1
    assert set(matches[0].player_ids) == {first, second}
    assert db.execute("SELECT count(*) FROM players").fetchone()[0] == 4
