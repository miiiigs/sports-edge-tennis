from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.odds.contracts import (
    BookmakerMapping,
    LiveOddsError,
    MatchVersionContext,
    ProviderBookmakerMarket,
    ProviderObservation,
    ProviderSelection,
)
from sports_edge_tennis.odds.normalizer import build_quote
from sports_edge_tennis.odds.persistence import insert_bookmaker_mapping
from sports_edge_tennis.odds.reconciliation import (
    PINNAPI_SOURCE_SYSTEM,
    PinnapiEventEvidence,
    normalize_pinnapi_participant_name,
    reconcile_pinnapi_event,
)
from sports_edge_tennis.odds.service import ingest_provider_observation


def _utc(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_player(conn: psycopg.Connection, name: str, source_id: str | None = None) -> int:
    player_id = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,))
    if source_id is not None:
        conn.execute(
            """
            INSERT INTO player_source_ids (player_id, source_system, source_player_id)
            VALUES (%s, 'API_TENNIS', %s)
            """,
            (player_id, source_id),
        )
    return player_id


def _insert_candidate(
    conn: psycopg.Connection,
    *,
    player_1_name: str = "Zed Alpha",
    player_2_name: str = "Aaron Beta",
    player_1_source_id: str = "api-p1",
    player_2_source_id: str = "api-p2",
    tournament_name: str = "ATP Challenger Porto",
    tournament_source_id: str = "api-t1",
    match_start_at: datetime = _utc(21, 14, 30),
    source_observed_at: datetime = _utc(20, 10),
    tour_scope: str = "CHALLENGER",
    competition_tier: str = "CHALLENGER",
    status: str = "SCHEDULED",
) -> dict[str, int]:
    player_1_id = _insert_player(conn, player_1_name, player_1_source_id)
    player_2_id = _insert_player(conn, player_2_name, player_2_source_id)
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, %s, %s)
        RETURNING tournament_id
        """,
        (tournament_name, tour_scope, competition_tier),
    )
    conn.execute(
        """
        INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id)
        VALUES (%s, 'API_TENNIS', %s)
        """,
        (tournament_id, tournament_source_id),
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name, start_date)
        VALUES (%s, 2026, %s, %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, f"{tournament_name} 2026", match_start_at.date()),
    )
    match_id = _fetchval(conn, "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (edition_id,))
    version_id = _fetchval(
        conn,
        """
        INSERT INTO match_versions (
            match_id,
            player_1_id,
            player_2_id,
            match_start_at,
            tour_scope,
            competition_tier,
            round_code,
            best_of_sets,
            qualifying_flag,
            match_status,
            source_observed_at,
            as_of
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'R32', 3, FALSE, %s, %s, %s)
        RETURNING match_version_id
        """,
        (
            match_id,
            player_1_id,
            player_2_id,
            match_start_at,
            tour_scope,
            competition_tier,
            status,
            source_observed_at,
            source_observed_at,
        ),
    )
    conn.execute(
        """
        INSERT INTO match_source_ids (match_id, match_version_id, source_system, source_match_id)
        VALUES (%s, %s, 'API_TENNIS', %s)
        """,
        (match_id, version_id, f"api-event-{match_id}"),
    )
    return {
        "match_id": match_id,
        "match_version_id": version_id,
        "player_1_id": player_1_id,
        "player_2_id": player_2_id,
        "tournament_id": tournament_id,
    }


def _evidence(**overrides) -> PinnapiEventEvidence:
    payload = {
        "provider_event_id": "1635786677",
        "home_name": "Zed Alpha",
        "away_name": "Aaron Beta",
        "scheduled_start": _utc(21, 14, 30),
        "league_id": "321",
        "league_name": "ATP Challenger Porto",
        "parent_id": None,
        "event_type": "prematch",
    }
    payload.update(overrides)
    return PinnapiEventEvidence(**payload)


def _count_pinnapi_mappings(conn: psycopg.Connection) -> int:
    return conn.execute(
        "SELECT count(*) FROM match_source_ids WHERE source_system = %s",
        (PINNAPI_SOURCE_SYSTEM,),
    ).fetchone()[0]


def test_name_normalization_is_deterministic_and_not_fuzzy():
    assert normalize_pinnapi_participant_name("  JOAO   Fonseca ") == "joao fonseca"
    assert normalize_pinnapi_participant_name("Joao-Fonseca") == "joao fonseca"
    assert normalize_pinnapi_participant_name("Joao Fonseca (Total)") == "joao fonseca total"
    assert normalize_pinnapi_participant_name("Joao Fonseca (Games)") == "joao fonseca games"
    assert normalize_pinnapi_participant_name("Joao Fonseca (Sets)") == "joao fonseca sets"
    assert normalize_pinnapi_participant_name("Jon Fonseca") != normalize_pinnapi_participant_name("Joao Fonseca")


def test_unique_match_resolves_persists_mapping_and_orients_home_player_1(db: psycopg.Connection):
    candidate = _insert_candidate(db)

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(home_name="  zed   alpha  ", away_name="Aaron Beta"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "RESOLVED"
    assert result.match is not None
    assert result.match.match_id == candidate["match_id"]
    assert result.match.home_canonical_player_id == candidate["player_1_id"]
    assert result.match.away_canonical_player_id == candidate["player_2_id"]
    assert result.match.home_selection_side == "PLAYER_1"
    assert result.match.away_selection_side == "PLAYER_2"
    assert db.execute(
        """
        SELECT match_id, source_match_id
        FROM match_source_ids
        WHERE source_system = %s
        """,
        (PINNAPI_SOURCE_SYSTEM,),
    ).fetchone() == (candidate["match_id"], "1635786677")


def test_reversed_home_away_resolves_and_orients_home_player_2(db: psycopg.Connection):
    candidate = _insert_candidate(db)

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(home_name="Aaron Beta", away_name="Zed Alpha"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "RESOLVED"
    assert result.match is not None
    assert result.match.home_canonical_player_id == candidate["player_2_id"]
    assert result.match.away_canonical_player_id == candidate["player_1_id"]
    assert result.match.home_selection_side == "PLAYER_2"
    assert result.match.away_selection_side == "PLAYER_1"


@pytest.mark.parametrize("minute_delta", (30, -30))
def test_thirty_minute_boundary_may_resolve(db: psycopg.Connection, minute_delta: int):
    candidate = _insert_candidate(db)
    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(scheduled_start=_utc(21, 14, 30) + timedelta(minutes=minute_delta)),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "RESOLVED"


@pytest.mark.parametrize("minute_delta", (31, -31))
def test_thirty_one_minutes_does_not_auto_map(db: psycopg.Connection, minute_delta: int):
    candidate = _insert_candidate(db)
    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(scheduled_start=_utc(21, 14, 30) + timedelta(minutes=minute_delta)),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "UNMATCHED"
    assert _count_pinnapi_mappings(db) == 0


def test_zero_candidates_and_participant_mismatch_write_no_mapping(db: psycopg.Connection):
    candidate = _insert_candidate(db)

    zero = reconcile_pinnapi_event(db, evidence=_evidence(), candidate_match_ids=[], as_of=_utc(20, 11))
    mismatch = reconcile_pinnapi_event(
        db,
        evidence=_evidence(home_name="Third Player"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert zero.status == "UNMATCHED"
    assert mismatch.status == "UNMATCHED"
    assert _count_pinnapi_mappings(db) == 0


def test_multiple_valid_candidates_are_identity_conflict_and_write_no_mapping(db: psycopg.Connection):
    first = _insert_candidate(db)
    second = _insert_candidate(
        db,
        tournament_source_id="api-t2",
        player_1_source_id="api-p3",
        player_2_source_id="api-p4",
    )

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(),
        candidate_match_ids=[first["match_id"], second["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "IDENTITY_CONFLICT"
    assert set(result.candidate_match_ids) == {first["match_id"], second["match_id"]}
    assert _count_pinnapi_mappings(db) == 0


def test_tournament_competition_conflict_writes_no_mapping(db: psycopg.Connection):
    candidate = _insert_candidate(
        db,
        tournament_name="ATP Vienna",
        tournament_source_id="api-atp",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
    )

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(league_name="ATP Challenger Porto"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "UNMATCHED"
    assert _count_pinnapi_mappings(db) == 0


def test_mapping_retry_is_idempotent_and_different_candidate_conflicts(db: psycopg.Connection):
    first = _insert_candidate(db)
    second = _insert_candidate(db, tournament_source_id="api-t2", player_1_source_id="api-p3", player_2_source_id="api-p4")

    resolved = reconcile_pinnapi_event(
        db,
        evidence=_evidence(),
        candidate_match_ids=[first["match_id"]],
        as_of=_utc(20, 11),
    )
    retry = reconcile_pinnapi_event(
        db,
        evidence=_evidence(scheduled_start=_utc(21, 17)),
        candidate_match_ids=[first["match_id"]],
        as_of=_utc(20, 11),
    )
    conflict = reconcile_pinnapi_event(
        db,
        evidence=_evidence(),
        candidate_match_ids=[second["match_id"]],
        as_of=_utc(20, 11),
    )

    assert resolved.status == "RESOLVED"
    assert retry.status == "RESOLVED"
    assert retry.match is not None and retry.match.match_id == first["match_id"]
    assert conflict.status == "IDENTITY_CONFLICT"
    assert _count_pinnapi_mappings(db) == 1


def test_existing_mapping_rejects_contradictory_provider_evidence(db: psycopg.Connection):
    candidate = _insert_candidate(db)
    reconcile_pinnapi_event(db, evidence=_evidence(), candidate_match_ids=[candidate["match_id"]], as_of=_utc(20, 11))

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(home_name="Third Player"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "IDENTITY_CONFLICT"
    assert result.reason == "existing_mapping_evidence_conflict"


def test_unresolved_or_duplicate_provider_sides_fail_closed(db: psycopg.Connection):
    candidate = _insert_candidate(db)
    unresolved = reconcile_pinnapi_event(
        db,
        evidence=_evidence(away_name="Third Player"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )
    duplicate = reconcile_pinnapi_event(
        db,
        evidence=_evidence(away_name="Zed Alpha"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert unresolved.status == "UNMATCHED"
    assert duplicate.status == "UNMATCHED"
    assert _count_pinnapi_mappings(db) == 0


def test_pinnapi_reconciliation_does_not_create_player_source_ids(db: psycopg.Connection):
    candidate = _insert_candidate(db)
    before = db.execute("SELECT count(*) FROM player_source_ids").fetchone()[0]

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "RESOLVED"
    assert db.execute("SELECT count(*) FROM player_source_ids").fetchone()[0] == before
    assert db.execute("SELECT count(*) FROM player_source_ids WHERE source_system = 'PINNAPI'").fetchone()[0] == 0


def test_reconciliation_uses_pit_version_and_not_future_version(db: psycopg.Connection):
    candidate = _insert_candidate(db, match_start_at=_utc(21, 14, 30), source_observed_at=_utc(20, 10))
    future_version_id = _fetchval(
        db,
        """
        INSERT INTO match_versions (
            match_id,
            player_1_id,
            player_2_id,
            match_start_at,
            tour_scope,
            competition_tier,
            round_code,
            best_of_sets,
            qualifying_flag,
            match_status,
            source_observed_at,
            as_of
        )
        VALUES (%s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', 'R32', 3, FALSE, 'SCHEDULED', %s, %s)
        RETURNING match_version_id
        """,
        (
            candidate["match_id"],
            candidate["player_1_id"],
            candidate["player_2_id"],
            _utc(21, 16),
            _utc(20, 12),
            _utc(20, 12),
        ),
    )

    before_future = reconcile_pinnapi_event(
        db,
        evidence=_evidence(scheduled_start=_utc(21, 14, 30)),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert before_future.status == "RESOLVED"
    assert before_future.match is not None
    assert before_future.match.match_version_id == candidate["match_version_id"]
    assert before_future.match.match_version_id != future_version_id


def test_provider_selection_identity_path_invariant():
    ProviderSelection("stable-player", Decimal("2.10"))
    ProviderSelection(None, Decimal("2.10"), canonical_player_id=101)

    with pytest.raises(LiveOddsError, match="exactly one identity path"):
        ProviderSelection("stable-player", Decimal("2.10"), canonical_player_id=101)
    with pytest.raises(LiveOddsError, match="exactly one identity path"):
        ProviderSelection(None, Decimal("2.10"))


def test_pre_resolved_selection_path_builds_quote_and_validates_membership():
    observation = ProviderObservation(
        provider_code="PINNAPI",
        provider_event_id="1635786677",
        provider_observation_key="offline-1",
        received_at=_utc(20, 11),
        payload={"offline": True},
        adapter_version="offline-test",
        markets=(),
    )
    market = ProviderBookmakerMarket(
        provider_bookmaker_key="pinnacle",
        provider_market_key="num_0_moneyline",
        market_type="MATCH_WINNER",
        market_status="OPEN",
        selections=(ProviderSelection(None, Decimal("2.10"), canonical_player_id=202),),
    )
    match = MatchVersionContext(
        match_id=1,
        match_version_id=2,
        player_1_id=101,
        player_2_id=202,
        match_start_at=_utc(21),
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
        match_status="SCHEDULED",
    )
    mapping = BookmakerMapping(
        provider_bookmaker_mapping_id=1,
        provider_code="PINNAPI",
        provider_bookmaker_key="pinnacle",
        mapping_version=1,
        bookmaker_code="PINNACLE",
        normalization_class="SHARP",
        effective_from=_utc(20),
    )

    quote = build_quote(
        observation=observation,
        raw_odds_observation_id=1,
        market=market,
        selection=market.selections[0],
        selection_player_id=market.selections[0].canonical_player_id,
        match=match,
        mapping=mapping,
    )
    assert quote.selection_type == "PLAYER_2"

    with pytest.raises(LiveOddsError, match="not a canonical match participant"):
        build_quote(
            observation=observation,
            raw_odds_observation_id=1,
            market=market,
            selection=ProviderSelection(None, Decimal("2.10"), canonical_player_id=303),
            selection_player_id=303,
            match=match,
            mapping=mapping,
        )


def test_child_event_via_parent_id_fails_closed(db: psycopg.Connection):
    candidate = _insert_candidate(db)

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(parent_id="1635786676", home_name="Zed Alpha (Games)", away_name="Aaron Beta (Games)"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "UNMATCHED"
    assert result.reason == "child_or_special_event"
    assert _count_pinnapi_mappings(db) == 0


def test_child_suffix_without_parent_id_fails_closed(db: psycopg.Connection):
    candidate = _insert_candidate(db)

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(home_name="Zed Alpha (Games)", away_name="Aaron Beta (Games)"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "UNMATCHED"
    assert result.reason == "child_or_special_event"
    assert _count_pinnapi_mappings(db) == 0


def test_sets_suffix_child_fails_closed(db: psycopg.Connection):
    candidate = _insert_candidate(db)

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(home_name="Zed Alpha (Sets)", away_name="Aaron Beta (Sets)"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "UNMATCHED"
    assert result.reason == "child_or_special_event"
    assert _count_pinnapi_mappings(db) == 0


def test_non_prematch_event_type_fails_closed(db: psycopg.Connection):
    candidate = _insert_candidate(db)

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(event_type="moneyline"),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "UNMATCHED"
    assert result.reason == "child_or_special_event"
    assert _count_pinnapi_mappings(db) == 0


def test_valid_top_level_event_still_resolves(db: psycopg.Connection):
    candidate = _insert_candidate(db)

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(),
        candidate_match_ids=[candidate["match_id"]],
        as_of=_utc(20, 11),
    )

    assert result.status == "RESOLVED"
    assert result.match is not None
    assert _count_pinnapi_mappings(db) == 1


def _insert_ingest_match(conn: psycopg.Connection, *, provider_code: str, provider_event_id: str) -> dict[str, int]:
    player_1_id = _insert_player(conn, "Ingest Player One")
    player_2_id = _insert_player(conn, "Ingest Player Two")
    tournament_id = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES (%s, 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
        (f"{provider_code} Open",),
    )
    edition_id = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, start_date) VALUES (%s, 2026, %s, %s) RETURNING tournament_edition_id",
        (tournament_id, f"{provider_code} Open 2026", _utc(1).date()),
    )
    match_id = _fetchval(conn, "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (edition_id,))
    version_id = _fetchval(
        conn,
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            round_code, best_of_sets, qualifying_flag, match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'R32', 3, FALSE, 'SCHEDULED', %s, %s)
        RETURNING match_version_id
        """,
        (match_id, player_1_id, player_2_id, _utc(21, 14), _utc(20, 10), _utc(20, 10)),
    )
    conn.execute(
        "INSERT INTO match_source_ids (match_id, match_version_id, source_system, source_match_id) VALUES (%s, %s, %s, %s)",
        (match_id, version_id, provider_code, provider_event_id),
    )
    return {"match_id": match_id, "match_version_id": version_id, "player_1_id": player_1_id, "player_2_id": player_2_id}


def _canonical_observation(provider_code: str, provider_event_id: str, canonical_player_id: int) -> ProviderObservation:
    selection = ProviderSelection(None, Decimal("2.10"), canonical_player_id=canonical_player_id)
    market = ProviderBookmakerMarket(
        provider_bookmaker_key="pinnacle",
        provider_market_key="mw",
        market_type="MATCH_WINNER",
        market_status="OPEN",
        selections=(selection,),
    )
    return ProviderObservation(
        provider_code=provider_code,
        provider_event_id=provider_event_id,
        provider_observation_key=f"obs-{provider_code}-{provider_event_id}",
        received_at=_utc(20, 11),
        payload={},
        adapter_version="offline-test",
        markets=(market,),
    )


def test_other_provider_canonical_player_path_is_rejected(db: psycopg.Connection):
    fixture = _insert_ingest_match(db, provider_code="SOME_OTHER_PROVIDER", provider_event_id="event-1")
    insert_bookmaker_mapping(
        db,
        provider_code="SOME_OTHER_PROVIDER",
        provider_bookmaker_key="pinnacle",
        mapping_version=1,
        bookmaker_code="PINNACLE",
        normalization_class="SHARP",
        effective_from=_utc(20, 10),
    )

    result = ingest_provider_observation(
        db,
        _canonical_observation("SOME_OTHER_PROVIDER", "event-1", fixture["player_1_id"]),
    )

    assert result.unknown_participant == 1
    assert result.normalized_inserted == 0
    assert db.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] == 0


def test_pinnapi_valid_canonical_player_is_accepted(db: psycopg.Connection):
    fixture = _insert_ingest_match(db, provider_code="PINNAPI", provider_event_id="event-1")
    insert_bookmaker_mapping(
        db,
        provider_code="PINNAPI",
        provider_bookmaker_key="pinnacle",
        mapping_version=1,
        bookmaker_code="PINNACLE",
        normalization_class="SHARP",
        effective_from=_utc(20, 10),
    )

    result = ingest_provider_observation(
        db,
        _canonical_observation("PINNAPI", "event-1", fixture["player_1_id"]),
    )

    assert result.normalized_inserted == 1
    assert db.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] == 1


def test_pinnapi_canonical_player_outside_match_is_rejected(db: psycopg.Connection):
    fixture = _insert_ingest_match(db, provider_code="PINNAPI", provider_event_id="event-1")
    outsider = _insert_player(db, "Ingest Outsider")
    insert_bookmaker_mapping(
        db,
        provider_code="PINNAPI",
        provider_bookmaker_key="pinnacle",
        mapping_version=1,
        bookmaker_code="PINNACLE",
        normalization_class="SHARP",
        effective_from=_utc(20, 10),
    )

    result = ingest_provider_observation(
        db,
        _canonical_observation("PINNAPI", "event-1", outsider),
    )

    assert result.normalized_inserted == 0
    assert result.failed_groups == 1
    assert db.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] == 0
