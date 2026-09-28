"""Prospective cross-provider canonical reuse for API-Tennis ingestion.

If Sportradar already created canonical Match A and API-Tennis later supplies the same
sporting event under an unseen source id, ingestion must reuse A rather than create a
duplicate Match B. This protects every API-Tennis entry point via the shared
repository layer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.data import cross_provider_reconciliation as cpr
from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.data.sources.api_tennis_live import (
    ingest_api_tennis_source_a_fixtures,
)
from sports_edge_tennis.live.history_refresh import refresh_history_chunk

NOW = datetime.now(UTC)
MATCH_START = NOW - timedelta(hours=4)
OBSERVED = NOW - timedelta(hours=5)
# Slightly in the past so the database clock is always >= the observation time.
RECEIVED_AT = NOW - timedelta(seconds=5)
MATCH_DAY = MATCH_START.date()


def _fv(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_run(conn) -> HistoricalRepository:
    run_id = _fv(
        conn,
        "INSERT INTO ingest_runs (source_system, source_dataset, started_at, status) "
        "VALUES ('TEST', 'canonical_reuse', %s, 'RUNNING') RETURNING ingest_run_id",
        (OBSERVED,),
    )
    return HistoricalRepository(conn, run_id)


def _player(conn, name: str) -> int:
    return _fv(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (name,),
    )


def _tournament(conn, name: str = "Test Open") -> int:
    return _fv(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES (%s, 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
        (name,),
    )


def _edition(conn, tournament_id: int, season: int = MATCH_START.year) -> int:
    return _fv(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, start_date) "
        "VALUES (%s, %s, %s, %s) RETURNING tournament_edition_id",
        (tournament_id, season, f"Edition {season}", MATCH_DAY.isoformat()),
    )


def _canonical_match(
    conn,
    repository: HistoricalRepository,
    *,
    edition_id: int,
    p1: int,
    p2: int,
    start: datetime = MATCH_START,
    source_system: str = "SPORTRADAR",
    source_match_id: str = "sr-1",
) -> int:
    locator = repository.upsert_match_version(
        tournament_edition_id=edition_id,
        source_system=source_system,
        source_match_id=source_match_id,
        source_record_hash=f"hash:{source_match_id}",
        source_record_locator=f"{source_system}:{source_match_id}",
        source_file_name="test",
        source_version_hash=f"vhash:{source_match_id}",
        player_1_id=p1,
        player_2_id=p2,
        match_start_at=start,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="SCHEDULED",
        source_observed_at=OBSERVED,
        as_of=OBSERVED,
    )
    return locator.match_id


def _api_player_link(conn, player_id: int, source_player_id: str) -> None:
    conn.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id) "
        "VALUES (%s, 'API_TENNIS', %s)",
        (player_id, source_player_id),
    )


def _api_tournament_link(conn, tournament_id: int, source_tournament_id: str) -> None:
    conn.execute(
        "INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id) "
        "VALUES (%s, 'API_TENNIS', %s)",
        (tournament_id, source_tournament_id),
    )


def _fixture(**overrides) -> dict:
    payload = {
        "event_key": "at-1",
        "tournament_key": "tk-1",
        "tournament_name": "Test Open",
        "event_type_type": "Atp Singles",
        "competition_tier": "ATP_250",
        "event_date": MATCH_START.date().isoformat(),
        "event_time": MATCH_START.strftime("%H:%M"),
        "event_status": "Finished",
        "event_first_player": "Player A",
        "event_second_player": "Player B",
        "first_player_key": "api-a",
        "second_player_key": "api-b",
        "event_surface": "Hard",
        "event_round": "R32",
        "event_winner": "First Player",
        "event_final_result": "2 - 0",
    }
    payload.update(overrides)
    return payload


def _scenario(conn):
    """Sportradar canonical match A plus the API-Tennis identity maps."""
    repository = _seed_run(conn)
    p1 = _player(conn, "Player A")
    p2 = _player(conn, "Player B")
    tournament_id = _tournament(conn)
    edition_id = _edition(conn, tournament_id)
    match_id = _canonical_match(
        conn, repository, edition_id=edition_id, p1=p1, p2=p2
    )
    _api_player_link(conn, p1, "api-a")
    _api_player_link(conn, p2, "api-b")
    _api_tournament_link(conn, tournament_id, "tk-1")
    return {
        "repository": repository,
        "match_id": match_id,
        "player_1_id": p1,
        "player_2_id": p2,
        "tournament_id": tournament_id,
        "edition_id": edition_id,
    }


def _count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


# --------------------------------------------------------- pure v1 comparator


def _identity(
    *, p1=1, p2=2, tournament_id=3, season=2026, scope="ATP_MAIN", tier="ATP_250", start=MATCH_START
) -> cpr.SportingIdentity:
    return cpr.SportingIdentity(
        player_1_id=p1,
        player_2_id=p2,
        tournament_id=tournament_id,
        season_year=season,
        tour_scope=scope,
        competition_tier=tier,
        match_start_at=start,
    )


def test_comparator_direct_is_safe():
    result = cpr.compare_sporting_identity(_identity(), _identity())
    assert result.safe is True
    assert result.player_order_mapping == cpr.PLAYER_ORDER_DIRECT


def test_comparator_reversed_is_safe():
    result = cpr.compare_sporting_identity(_identity(p1=1, p2=2), _identity(p1=2, p2=1))
    assert result.safe is True
    assert result.player_order_mapping == cpr.PLAYER_ORDER_REVERSED


def test_comparator_delta_zero_is_safe():
    result = cpr.compare_sporting_identity(_identity(), _identity(start=MATCH_START))
    assert result.safe is True
    assert result.start_delta_minutes == 0.0


def test_comparator_delta_fifteen_is_safe():
    result = cpr.compare_sporting_identity(
        _identity(), _identity(start=MATCH_START + timedelta(minutes=15))
    )
    assert result.safe is True
    assert result.start_delta_minutes == 15.0


def test_comparator_delta_over_fifteen_rejected():
    result = cpr.compare_sporting_identity(
        _identity(), _identity(start=MATCH_START + timedelta(minutes=16))
    )
    assert result.safe is False
    assert result.reason == cpr.REASON_TIME_MISMATCH


def test_comparator_identity_conflict_rejected():
    result = cpr.compare_sporting_identity(_identity(), _identity(p2=99))
    assert result.safe is False
    assert result.reason == cpr.REASON_IDENTITY_CONFLICT


def test_comparator_tournament_mismatch_rejected():
    result = cpr.compare_sporting_identity(_identity(), _identity(tournament_id=99))
    assert result.safe is False
    assert result.reason == cpr.REASON_TOURNAMENT_MISMATCH


def test_comparator_season_mismatch_rejected():
    result = cpr.compare_sporting_identity(_identity(), _identity(season=2025))
    assert result.safe is False
    assert result.reason == cpr.REASON_SEASON_MISMATCH


def test_comparator_scope_mismatch_rejected():
    result = cpr.compare_sporting_identity(_identity(), _identity(scope="CHALLENGER"))
    assert result.safe is False
    assert result.reason == cpr.REASON_SCOPE_MISMATCH


def test_comparator_tier_mismatch_rejected():
    result = cpr.compare_sporting_identity(_identity(), _identity(tier="ATP_500"))
    assert result.safe is False
    assert result.reason == cpr.REASON_TIER_MISMATCH


def test_comparator_known_safe_pair_passes():
    # A previously repaired historical pair passes the same shared comparator.
    result = cpr.compare_sporting_identity(
        _identity(), _identity(start=MATCH_START + timedelta(minutes=5))
    )
    assert result.safe is True


def test_comparator_131036_131137_fails_forty_minutes():
    result = cpr.compare_sporting_identity(
        _identity(), _identity(start=MATCH_START + timedelta(minutes=40))
    )
    assert result.safe is False
    assert result.reason == cpr.REASON_TIME_MISMATCH


# ------------------------------------------------------------- repository


def test_known_api_tennis_source_id_reuses_existing_canonical(db: psycopg.Connection):
    scenario = _scenario(db)
    repository = scenario["repository"]
    # A prior API-Tennis ingestion already linked this event.
    db.execute(
        "INSERT INTO match_source_ids (match_id, source_system, source_match_id) "
        "VALUES (%s, 'API_TENNIS', 'at-1')",
        (scenario["match_id"],),
    )
    locator = repository.upsert_match_version(
        tournament_edition_id=scenario["edition_id"],
        source_system="API_TENNIS",
        source_match_id="at-1",
        source_record_hash="hash:at-1",
        source_record_locator="api:at-1",
        source_file_name="test",
        source_version_hash="vhash:at-1",
        player_1_id=scenario["player_1_id"],
        player_2_id=scenario["player_2_id"],
        match_start_at=MATCH_START,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="SCHEDULED",
        source_observed_at=OBSERVED,
        as_of=OBSERVED,
    )
    assert locator.match_id == scenario["match_id"]
    assert _count(db, "matches") == 1


def test_unknown_api_tennis_source_reuses_exact_sportradar_event(db: psycopg.Connection):
    scenario = _scenario(db)
    outcome = ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-2")], received_at=RECEIVED_AT
    )
    assert outcome.rows_ingested == 1
    assert outcome.matches[0].match_id == scenario["match_id"]
    assert _count(db, "matches") == 1
    linked = db.execute(
        "SELECT match_id FROM match_source_ids "
        "WHERE source_system = 'API_TENNIS' AND source_match_id = 'at-2'"
    ).fetchone()
    assert linked[0] == scenario["match_id"]


def test_reversed_api_tennis_event_reuses_canonical(db: psycopg.Connection):
    scenario = _scenario(db)
    outcome = ingest_api_tennis_source_a_fixtures(
        db,
        [_fixture(event_key="at-3", first_player_key="api-b", second_player_key="api-a")],
        received_at=RECEIVED_AT,
    )
    assert outcome.matches[0].match_id == scenario["match_id"]
    assert _count(db, "matches") == 1


def test_zero_candidates_creates_normally(db: psycopg.Connection):
    p1 = _player(db, "Player A")
    p2 = _player(db, "Player B")
    tournament_id = _tournament(db)
    _api_player_link(db, p1, "api-a")
    _api_player_link(db, p2, "api-b")
    _api_tournament_link(db, tournament_id, "tk-1")

    outcome = ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-new")], received_at=RECEIVED_AT
    )
    assert outcome.rows_ingested == 1
    assert _count(db, "matches") == 1
    linked = db.execute(
        "SELECT match_id FROM match_source_ids WHERE source_match_id = 'at-new'"
    ).fetchone()
    assert linked[0] == outcome.matches[0].match_id


def test_multiple_safe_candidates_fail_closed(db: psycopg.Connection):
    scenario = _scenario(db)
    # A second canonical match with identical sporting identity makes reuse ambiguous.
    _canonical_match(
        db,
        scenario["repository"],
        edition_id=scenario["edition_id"],
        p1=scenario["player_1_id"],
        p2=scenario["player_2_id"],
        source_match_id="sr-2",
    )
    outcome = ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-4")], received_at=RECEIVED_AT
    )
    assert outcome.rows_ingested == 0
    assert outcome.rows_rejected == 1
    assert _count(db, "matches") == 2
    assert db.execute(
        "SELECT count(*) FROM match_source_ids WHERE source_system='API_TENNIS' "
        "AND source_match_id='at-4'"
    ).fetchone()[0] == 0


def test_new_mapping_attaches_to_existing_canonical(db: psycopg.Connection):
    scenario = _scenario(db)
    ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-5")], received_at=RECEIVED_AT
    )
    row = db.execute(
        "SELECT m.match_id, m.source_system, m.source_match_id, m.match_version_id "
        "FROM match_source_ids m WHERE m.source_match_id = 'at-5'"
    ).fetchone()
    assert row[0] == scenario["match_id"]
    assert row[1] == "API_TENNIS"
    assert row[3] is not None


def test_retry_is_idempotent(db: psycopg.Connection):
    _scenario(db)
    first = ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-6")], received_at=RECEIVED_AT
    )
    second = ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-6")], received_at=RECEIVED_AT
    )
    assert first.matches[0].match_id == second.matches[0].match_id
    assert _count(db, "matches") == 1
    assert db.execute(
        "SELECT count(*) FROM match_source_ids WHERE source_match_id = 'at-6'"
    ).fetchone()[0] == 1


def test_source_id_conflict_converges_to_one_canonical(db: psycopg.Connection):
    scenario = _scenario(db)
    # Simulate another worker having already claimed the event for match A.
    db.execute(
        "INSERT INTO match_source_ids (match_id, source_system, source_match_id) "
        "VALUES (%s, 'API_TENNIS', 'at-7')",
        (scenario["match_id"],),
    )
    outcome = ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-7")], received_at=RECEIVED_AT
    )
    assert outcome.matches[0].match_id == scenario["match_id"]
    assert _count(db, "matches") == 1


def test_match_version_belongs_to_reused_canonical(db: psycopg.Connection):
    scenario = _scenario(db)
    ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-8")], received_at=RECEIVED_AT
    )
    row = db.execute(
        """
        SELECT mv.match_id, mvs.source_system
        FROM match_version_sources mvs
        JOIN match_versions mv ON mv.match_version_id = mvs.match_version_id
        WHERE mvs.source_system = 'API_TENNIS' AND mvs.source_match_id = 'at-8'
        """
    ).fetchone()
    assert row[0] == scenario["match_id"]


def test_no_reconciliation_row_created_for_prospective_reuse(db: psycopg.Connection):
    _scenario(db)
    ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-9")], received_at=RECEIVED_AT
    )
    assert _count(db, "cross_provider_match_reconciliations") == 0


def test_historical_source_mappings_unchanged(db: psycopg.Connection):
    _scenario(db)
    before = db.execute(
        "SELECT match_id, source_system, source_match_id FROM match_source_ids "
        "ORDER BY match_id, source_system, source_match_id"
    ).fetchall()
    ingest_api_tennis_source_a_fixtures(
        db, [_fixture(event_key="at-10")], received_at=RECEIVED_AT
    )
    after = [
        row
        for row in db.execute(
            "SELECT match_id, source_system, source_match_id FROM match_source_ids "
            "ORDER BY match_id, source_system, source_match_id"
        ).fetchall()
        if row[2] != "at-10"
    ]
    assert after == before
    assert ("SPORTRADAR", "sr-1") in {(r[1], r[2]) for r in after}


def test_evaluator_behavior_unchanged(db: psycopg.Connection):
    scenario = _scenario(db)
    # A distinct evidence canonical match with the same sporting identity.
    evidence = _canonical_match(
        db,
        scenario["repository"],
        edition_id=scenario["edition_id"],
        p1=scenario["player_1_id"],
        p2=scenario["player_2_id"],
        source_system="TENNIS_DATA",
        source_match_id="td-evidence",
    )
    db.execute(
        "INSERT INTO match_result_observations "
        "(match_id, outcome_status, winner_side, source_observed_at, as_of) "
        "VALUES (%s, 'COMPLETED', 1, %s, %s)",
        (evidence, OBSERVED, OBSERVED),
    )
    evaluation = cpr.evaluate_cross_provider_reconciliation(
        db, target_match_id=scenario["match_id"], evidence_match_id=evidence
    )
    assert evaluation.apply_safe is True
    assert evaluation.classification == cpr.CLASSIFICATION_EXACT


# --------------------------------------------- critical history-refresh path


def test_history_refresh_cannot_create_exact_v1_duplicate(db: psycopg.Connection):
    scenario = _scenario(db)
    outcome = refresh_history_chunk(
        db,
        api_key="secret",
        start_date=MATCH_DAY,
        end_date=MATCH_DAY,
        fetch_window=lambda _key, _start, _end: (RECEIVED_AT, [_fixture(event_key="at-h1")]),
    )
    assert outcome.fixtures_ingested == 1
    assert _count(db, "matches") == 1
    assert db.execute(
        "SELECT match_id FROM match_source_ids WHERE source_match_id = 'at-h1'"
    ).fetchone()[0] == scenario["match_id"]


def test_history_refresh_result_belongs_to_reused_canonical(db: psycopg.Connection):
    scenario = _scenario(db)
    refresh_history_chunk(
        db,
        api_key="secret",
        start_date=MATCH_DAY,
        end_date=MATCH_DAY,
        fetch_window=lambda _key, _start, _end: (RECEIVED_AT, [_fixture(event_key="at-h2")]),
    )
    row = db.execute(
        "SELECT match_id, outcome_status::text, winner_side FROM match_result_observations"
    ).fetchone()
    assert row == (scenario["match_id"], "COMPLETED", 1)
    assert _count(db, "cross_provider_match_reconciliations") == 0


def test_retry_of_history_refresh_stays_one_canonical(db: psycopg.Connection):
    _scenario(db)
    for event in ("at-h3", "at-h4"):
        refresh_history_chunk(
            db,
            api_key="secret",
            start_date=MATCH_DAY,
            end_date=MATCH_DAY,
            fetch_window=lambda _key, _start, _end, e=event: (RECEIVED_AT, [_fixture(event_key=e)]),
        )
    assert _count(db, "matches") == 1


def test_reuse_source_systems_is_api_tennis_only():
    from sports_edge_tennis.data import cross_provider_reconciliation as module

    assert module.PROSPECTIVE_REUSE_SOURCE_SYSTEMS == ("API_TENNIS",)


def test_existing_source_system_does_not_reuse(db: psycopg.Connection):
    # A non-API-Tennis source (TENNIS_DATA) must not run prospective cross-provider reuse.
    repository = _seed_run(db)
    p1 = _player(db, "Player A")
    p2 = _player(db, "Player B")
    tournament_id = _tournament(db)
    edition_id = _edition(db, tournament_id)
    _canonical_match(db, repository, edition_id=edition_id, p1=p1, p2=p2, source_match_id="sr-x")
    locator = repository.upsert_match_version(
        tournament_edition_id=edition_id,
        source_system="TENNIS_DATA",
        source_match_id="td-1",
        source_record_hash="hash:td-1",
        source_record_locator="td:1",
        source_file_name="test",
        source_version_hash="vhash:td-1",
        player_1_id=p1,
        player_2_id=p2,
        match_start_at=MATCH_START,
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        surface="HARD",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="SCHEDULED",
        source_observed_at=OBSERVED,
        as_of=OBSERVED,
    )
    assert locator.match_id != 1
    assert _count(db, "matches") == 2
