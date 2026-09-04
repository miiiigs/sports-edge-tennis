from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

import sports_edge_tennis.odds.service as odds_service
from sports_edge_tennis.odds.contracts import (
    LiveOddsIntegrityError,
    ProviderBookmakerMarket,
    ProviderObservation,
    ProviderSelection,
)
from sports_edge_tennis.odds.normalizer import build_quote
from sports_edge_tennis.odds.persistence import (
    insert_bookmaker_mapping,
    insert_live_odds_snapshot,
    load_pit_match_version,
    persist_raw_observation,
    resolve_provider_event,
    select_bookmaker_mapping,
)
from sports_edge_tennis.odds.service import ingest_provider_observation


def _utc(day: int, hour: int = 12, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2025, 9, day, hour, minute, second, tzinfo=timezone.utc)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_player(conn: psycopg.Connection, provider_code: str, provider_player_id: str, name: str) -> int:
    player_id = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id", (name,))
    conn.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id)
        VALUES (%s, %s, %s)
        """,
        (player_id, provider_code, provider_player_id),
    )
    return player_id


def _insert_match(
    conn: psycopg.Connection,
    *,
    provider_code: str = "PROVIDER_A",
    provider_event_id: str = "event-1",
    player_1_provider_id: str = "p1",
    player_2_provider_id: str = "p2",
    observed_at: datetime = _utc(1),
    match_start_at: datetime = _utc(20),
) -> dict[str, int]:
    player_1_id = _insert_player(conn, provider_code, player_1_provider_id, f"{provider_code} Player One")
    player_2_id = _insert_player(conn, provider_code, player_2_provider_id, f"{provider_code} Player Two")
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
        (f"{provider_code} Open",),
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name, surface, start_date, end_date)
        VALUES (%s, 2025, %s, 'HARD', '2025-09-01', '2025-09-20')
        RETURNING tournament_edition_id
        """,
        (tournament_id, f"{provider_code} Open 2025"),
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
            surface,
            round_code,
            best_of_sets,
            qualifying_flag,
            match_status,
            source_observed_at,
            as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, 'SCHEDULED', %s, %s)
        RETURNING match_version_id
        """,
        (match_id, player_1_id, player_2_id, match_start_at, observed_at, observed_at),
    )
    conn.execute(
        """
        INSERT INTO match_source_ids (match_id, match_version_id, source_system, source_match_id)
        VALUES (%s, %s, %s, %s)
        """,
        (match_id, version_id, provider_code, provider_event_id),
    )
    return {"match_id": match_id, "match_version_id": version_id, "player_1_id": player_1_id, "player_2_id": player_2_id}


def _market(
    *,
    book: str = "pinnacle",
    key: str = "mw",
    status: str = "OPEN",
    selections: tuple[ProviderSelection, ...] | None = None,
) -> ProviderBookmakerMarket:
    return ProviderBookmakerMarket(
        provider_bookmaker_key=book,
        provider_market_key=key,
        market_type="MATCH_WINNER",
        market_status=status,  # type: ignore[arg-type]
        selections=selections or (
            ProviderSelection("p1", Decimal("2.10")),
            ProviderSelection("p2", Decimal("1.80")),
        ),
    )


def _observation(
    *,
    provider_code: str = "PROVIDER_A",
    provider_event_id: str = "event-1",
    observation_key: str = "response-1",
    provider_observed_at: datetime | None = _utc(5, 9),
    received_at: datetime = _utc(5, 9, 1),
    markets: tuple[ProviderBookmakerMarket, ...] | None = None,
    payload: dict | None = None,
) -> ProviderObservation:
    return ProviderObservation(
        provider_code=provider_code,
        provider_event_id=provider_event_id,
        provider_observation_key=observation_key,
        provider_observed_at=provider_observed_at,
        received_at=received_at,
        provider_request_id=f"request-{observation_key}",
        provider_response_id=observation_key,
        payload=payload or {"api_key": "secret", "prices": [{"p1": "2.10", "p2": "1.80"}]},
        adapter_version="adapter-test-v1",
        markets=markets or (_market(),),
    )


def _insert_ingest_run(conn: psycopg.Connection, provider_code: str = "PROVIDER_A") -> int:
    return _fetchval(
        conn,
        """
        INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
        VALUES (%s, 'LIVE_ODDS', %s, 'RUNNING')
        RETURNING ingest_run_id
        """,
        (provider_code, _utc(5)),
    )


def _insert_raw(conn: psycopg.Connection, observation: ProviderObservation | None = None) -> int:
    observation = observation or _observation()
    return persist_raw_observation(
        conn,
        ingest_run_id=_insert_ingest_run(conn, observation.provider_code),
        observation=observation,
    ).raw_odds_observation_id


def _insert_mapping(
    conn: psycopg.Connection,
    *,
    provider_code: str = "PROVIDER_A",
    book_key: str = "pinnacle",
    version: int = 1,
    bookmaker_code: str = "PINNACLE",
    class_name: str = "SHARP",
    effective_from: datetime = _utc(1),
) -> int:
    return insert_bookmaker_mapping(
        conn,
        provider_code=provider_code,
        provider_bookmaker_key=book_key,
        mapping_version=version,
        bookmaker_code=bookmaker_code,
        normalization_class=class_name,
        effective_from=effective_from,
    )


def test_raw_observation_persistence_is_idempotent_redacted_and_append_only(db: psycopg.Connection):
    observation = _observation(payload={"api_key": "secret", "safe": {"token": "hidden", "price": "2.10"}})
    first = persist_raw_observation(db, ingest_run_id=_insert_ingest_run(db), observation=observation)
    second = persist_raw_observation(db, ingest_run_id=_insert_ingest_run(db), observation=observation)
    distinct_payload_same = _observation(observation_key="response-2", payload=observation.payload)
    third = persist_raw_observation(db, ingest_run_id=_insert_ingest_run(db), observation=distinct_payload_same)

    assert first.inserted is True
    assert second.inserted is False
    assert third.inserted is True
    assert first.raw_odds_observation_id == second.raw_odds_observation_id
    assert third.raw_odds_observation_id != first.raw_odds_observation_id
    stored = db.execute("SELECT payload_json::text, payload_hash FROM raw_odds_observations WHERE raw_odds_observation_id = %s", (first.raw_odds_observation_id,)).fetchone()
    assert "secret" not in stored[0]
    assert "hidden" not in stored[0]
    assert stored[1].startswith("SE_LIVE_ODDS_RAW_V1:")
    with pytest.raises(psycopg.Error):
        db.execute("UPDATE raw_odds_observations SET provider_event_id = 'changed' WHERE raw_odds_observation_id = %s", (first.raw_odds_observation_id,))
    with pytest.raises(psycopg.Error):
        db.execute("DELETE FROM raw_odds_observations WHERE raw_odds_observation_id = %s", (first.raw_odds_observation_id,))


@pytest.mark.parametrize(
    "mutation",
    (
        lambda obs: replace(obs, provider_event_id="event-2"),
        lambda obs: replace(obs, provider_request_id="request-2"),
        lambda obs: replace(obs, provider_response_id="response-2"),
        lambda obs: replace(obs, provider_observed_at=_utc(5, 8)),
        lambda obs: replace(obs, received_at=_utc(5, 9, 2)),
        lambda obs: replace(obs, payload={"api_key": "secret", "prices": [{"p1": "2.11", "p2": "1.80"}]}),
        lambda obs: replace(obs, adapter_version="adapter-test-v2"),
    ),
)
def test_raw_observation_retry_rejects_any_persisted_truth_drift(db: psycopg.Connection, mutation):
    observation = _observation(observation_key="raw-truth")
    first = persist_raw_observation(db, ingest_run_id=_insert_ingest_run(db), observation=observation)

    with pytest.raises(LiveOddsIntegrityError, match="raw observation retry payload differs"):
        persist_raw_observation(db, ingest_run_id=_insert_ingest_run(db), observation=mutation(observation))

    stored = db.execute(
        """
        SELECT provider_event_id, provider_request_id, provider_response_id, provider_observed_at,
               received_at, observation_time, payload_hash, adapter_version
        FROM raw_odds_observations
        WHERE raw_odds_observation_id = %s
        """,
        (first.raw_odds_observation_id,),
    ).fetchone()
    assert stored[:6] == (
        observation.provider_event_id,
        observation.provider_request_id,
        observation.provider_response_id,
        observation.provider_observed_at,
        observation.received_at,
        observation.observation_time,
    )
    assert stored[7] == observation.adapter_version


def test_event_mapping_uses_existing_unique_index_and_defensive_resolver(db: psycopg.Connection):
    fixture = _insert_match(db)
    assert resolve_provider_event(db, provider_code="PROVIDER_A", provider_event_id="event-1") == ("RESOLVED", fixture["match_id"])
    assert resolve_provider_event(db, provider_code="PROVIDER_A", provider_event_id="missing") == ("UNMATCHED", None)

    other = _insert_match(db, provider_code="PROVIDER_B", provider_event_id="event-1")
    assert other["match_id"] != fixture["match_id"]

    with pytest.raises(psycopg.Error):
        conn = db
        p1 = _insert_player(conn, "PROVIDER_A", "p3", "Duplicate One")
        p2 = _insert_player(conn, "PROVIDER_A", "p4", "Duplicate Two")
        tournament_id = _fetchval(
            conn,
            "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES ('Dup Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
        )
        edition_id = _fetchval(
            conn,
            "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) VALUES (%s, 2026, 'Dup Open 2026') RETURNING tournament_edition_id",
            (tournament_id,),
        )
        match_id = _fetchval(conn, "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (edition_id,))
        version_id = _fetchval(
            conn,
            """
            INSERT INTO match_versions (
                match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
                best_of_sets, match_status, source_observed_at, as_of
            )
            VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 3, 'SCHEDULED', %s, %s)
            RETURNING match_version_id
            """,
            (match_id, p1, p2, _utc(20), _utc(1), _utc(1)),
        )
        conn.execute(
            "INSERT INTO match_source_ids (match_id, match_version_id, source_system, source_match_id) VALUES (%s, %s, 'PROVIDER_A', 'event-1')",
            (match_id, version_id),
        )


def test_bookmaker_mapping_temporal_replay_and_append_only_guards(db: psycopg.Connection):
    v1 = _insert_mapping(db, class_name="OFFERED", effective_from=_utc(1))
    v2 = _insert_mapping(db, version=2, class_name="RAW_ONLY", effective_from=_utc(10))

    assert select_bookmaker_mapping(db, provider_code="PROVIDER_A", provider_bookmaker_key="pinnacle", observation_time=_utc(5)).provider_bookmaker_mapping_id == v1
    assert select_bookmaker_mapping(db, provider_code="PROVIDER_A", provider_bookmaker_key="pinnacle", observation_time=_utc(12)).provider_bookmaker_mapping_id == v2
    assert select_bookmaker_mapping(db, provider_code="PROVIDER_A", provider_bookmaker_key="pinnacle", observation_time=_utc(10)).provider_bookmaker_mapping_id == v2
    assert select_bookmaker_mapping(db, provider_code="PROVIDER_A", provider_bookmaker_key="pinnacle", observation_time=_utc(1, 0)) is None

    for kwargs in (
        {"version": 3, "effective_from": _utc(5)},
        {"version": 3, "effective_from": _utc(10)},
        {"version": 2, "effective_from": _utc(12)},
    ):
        with pytest.raises(psycopg.Error):
            _insert_mapping(db, **kwargs)

    with pytest.raises(psycopg.Error):
        db.execute("UPDATE provider_bookmaker_mappings SET bookmaker_code = 'OTHER' WHERE provider_bookmaker_mapping_id = %s", (v1,))
    with pytest.raises(psycopg.Error):
        db.execute("DELETE FROM provider_bookmaker_mappings WHERE provider_bookmaker_mapping_id = %s", (v1,))


def test_bookmaker_mapping_concurrent_next_version_conflict(postgres_dsn: str, db: psycopg.Connection):
    _insert_mapping(db, effective_from=_utc(1))
    conn1 = psycopg.connect(postgres_dsn, autocommit=False)
    conn2 = psycopg.connect(postgres_dsn, autocommit=False)
    errors: list[Exception] = []
    ready = threading.Event()

    def insert_competing() -> None:
        ready.wait(timeout=5)
        try:
            _insert_mapping(conn2, version=2, effective_from=_utc(2))
            conn2.commit()
        except Exception as exc:  # noqa: BLE001 - test captures expected database conflict
            errors.append(exc)
            conn2.rollback()

    try:
        _insert_mapping(conn1, version=2, effective_from=_utc(2))
        thread = threading.Thread(target=insert_competing)
        thread.start()
        ready.set()
        conn1.commit()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert errors
        count = db.execute(
            """
            SELECT count(*)
            FROM provider_bookmaker_mappings
            WHERE provider_code = 'PROVIDER_A'
              AND provider_bookmaker_key = 'pinnacle'
              AND mapping_version = 2
            """
        ).fetchone()[0]
        assert count == 1
    finally:
        conn1.close()
        conn2.close()


def test_live_provider_structural_guard_and_non_live_compatibility(db: psycopg.Connection):
    fixture = _insert_match(db)
    raw_id = _insert_raw(db)
    mapping_id = _insert_mapping(db)
    base = (fixture["match_id"], "PINNACLE", "MATCH_WINNER", "PLAYER_1", Decimal("2.10"), "snap-struct", _utc(5), _utc(5))

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO odds_snapshots (
                match_id, source_system, bookmaker_code, market_type, selection_type, decimal_price,
                source_snapshot_id, source_observed_at, as_of, odds_origin, provider_bookmaker_mapping_id
            )
            VALUES (%s, 'SHARP', %s, %s, %s, %s, %s, %s, %s, 'LIVE_PROVIDER', %s)
            """,
            (*base, mapping_id),
        )
    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO odds_snapshots (
                match_id, source_system, bookmaker_code, market_type, selection_type, decimal_price,
                source_snapshot_id, source_observed_at, as_of, odds_origin, raw_odds_observation_id
            )
            VALUES (%s, 'SHARP', %s, %s, %s, %s, 'snap-no-map', %s, %s, 'LIVE_PROVIDER', %s)
            """,
            (fixture["match_id"], "PINNACLE", "MATCH_WINNER", "PLAYER_1", Decimal("2.10"), _utc(5), _utc(5), raw_id),
        )
    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO odds_snapshots (
                match_id, source_system, bookmaker_code, market_type, selection_type, decimal_price,
                source_snapshot_id, source_observed_at, as_of, odds_origin, raw_odds_observation_id, provider_bookmaker_mapping_id
            )
            VALUES (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.10, 'snap-bad-source', %s, %s, 'LIVE_PROVIDER', %s, %s)
            """,
            (fixture["match_id"], _utc(5), _utc(5), raw_id, mapping_id),
        )

    for source, origin, snap in (
        ("SHARP", "LIVE_PROVIDER", "snap-sharp"),
        ("OFFERED", "LIVE_PROVIDER", "snap-offered"),
    ):
        db.execute(
            """
            INSERT INTO odds_snapshots (
                match_id, source_system, bookmaker_code, market_type, selection_type, decimal_price,
                source_snapshot_id, source_observed_at, as_of, odds_origin, raw_odds_observation_id, provider_bookmaker_mapping_id
            )
            VALUES (%s, %s, 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.10, %s, %s, %s, %s, %s, %s)
            """,
            (fixture["match_id"], source, snap, _utc(5), _utc(5), origin, raw_id, mapping_id),
        )

    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type, selection_type, decimal_price,
            source_snapshot_id, source_observed_at, as_of
        )
        VALUES (%s, 'TENNIS_DATA', 'B365', 'MATCH_WINNER', 'PLAYER_1', 2.20, 'historical-ok', %s, %s)
        """,
        (fixture["match_id"], _utc(5), _utc(5)),
    )
    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id, source_system, bookmaker_code, market_type, selection_type, decimal_price,
            source_snapshot_id, source_observed_at, as_of, odds_origin
        )
        VALUES (%s, 'SHARP', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.30, 'synthetic-ok', %s, %s, 'SYNTHETIC')
        """,
        (fixture["match_id"], _utc(5), _utc(5)),
    )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda quote, other_raw_id, other_mapping_id: replace(quote, decimal_price=Decimal("2.11")),
        lambda quote, other_raw_id, other_mapping_id: replace(quote, market_status="SUSPENDED", source_record_hash="status-drift"),
        lambda quote, other_raw_id, other_mapping_id: replace(quote, as_of=quote.as_of + timedelta(minutes=1), source_record_hash="as-of-drift"),
        lambda quote, other_raw_id, other_mapping_id: replace(quote, raw_odds_observation_id=other_raw_id),
        lambda quote, other_raw_id, other_mapping_id: replace(quote, provider_bookmaker_mapping_id=other_mapping_id),
    ),
)
def test_live_odds_snapshot_retry_rejects_any_normalized_truth_drift(db: psycopg.Connection, mutation):
    fixture = _insert_match(db)
    observation = _observation(observation_key="quote-truth")
    raw_id = _insert_raw(db, observation)
    other_raw_id = _insert_raw(db, _observation(observation_key="quote-truth-other-raw"))
    mapping_id = _insert_mapping(db)
    other_mapping_id = _insert_mapping(db, version=2, effective_from=_utc(2))
    mapping = select_bookmaker_mapping(
        db,
        provider_code=observation.provider_code,
        provider_bookmaker_key="pinnacle",
        observation_time=observation.observation_time,
    )
    match = load_pit_match_version(db, match_id=fixture["match_id"], observation_time=observation.observation_time)
    quote = build_quote(
        observation=observation,
        raw_odds_observation_id=raw_id,
        market=observation.markets[0],
        selection=observation.markets[0].selections[0],
        selection_player_id=fixture["player_1_id"],
        match=match,
        mapping=mapping,
    )
    assert mapping.provider_bookmaker_mapping_id == other_mapping_id
    original_quote = replace(quote, provider_bookmaker_mapping_id=mapping_id)

    assert insert_live_odds_snapshot(db, match_id=fixture["match_id"], quote=original_quote) is True
    with pytest.raises(LiveOddsIntegrityError, match="persisted odds snapshot truth differs"):
        insert_live_odds_snapshot(db, match_id=fixture["match_id"], quote=mutation(original_quote, other_raw_id, other_mapping_id))


def test_service_normalizes_sharp_offered_and_preserves_lineage(db: psycopg.Connection):
    fixture = _insert_match(db)
    sharp_mapping = _insert_mapping(db)
    offered_mapping = _insert_mapping(db, book_key="soft", bookmaker_code="SOFTBOOK", class_name="OFFERED")

    result = ingest_provider_observation(
        db,
        _observation(markets=(_market(), _market(book="soft", key="mw-soft", selections=(ProviderSelection("p2", Decimal("2.40")),)))),
    )

    assert result.normalized_inserted == 3
    rows = db.execute(
        """
        SELECT source_system, bookmaker_code, selection_type, raw_odds_observation_id, provider_bookmaker_mapping_id
        FROM odds_snapshots
        ORDER BY bookmaker_code, selection_type
        """
    ).fetchall()
    assert ("SHARP", "PINNACLE", "PLAYER_1", rows[0][3], sharp_mapping) in rows
    assert ("OFFERED", "SOFTBOOK", "PLAYER_2", rows[0][3], offered_mapping) in rows
    assert len({row[3] for row in rows}) == 1
    assert fixture["match_id"] == db.execute("SELECT DISTINCT match_id FROM odds_snapshots").fetchone()[0]


def test_service_raw_only_paths_and_group_isolation(db: psycopg.Connection):
    _insert_match(db)
    _insert_mapping(db, class_name="RAW_ONLY")
    _insert_mapping(db, book_key="soft", bookmaker_code="SOFTBOOK", class_name="OFFERED")

    result = ingest_provider_observation(
        db,
        _observation(
            observation_key="mixed",
            markets=(
                _market(book="pinnacle"),
                _market(book="unknown"),
                _market(book="soft", key="soft", selections=(ProviderSelection("p1", Decimal("2.25")),)),
                _market(book="soft", key="bad-player", selections=(ProviderSelection("missing", Decimal("2.25")),)),
            ),
        ),
    )

    assert result.raw_only_bookmaker == 1
    assert result.unknown_bookmaker == 1
    assert result.unknown_participant == 1
    assert result.normalized_inserted == 1
    assert db.execute("SELECT count(*) FROM raw_odds_observations").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] == 1


def test_service_reuses_persisted_raw_truth_and_rejects_event_drift(db: psycopg.Connection):
    event_1 = _insert_match(db, provider_event_id="event-1")
    event_2 = _insert_match(
        db,
        provider_event_id="event-2",
        player_1_provider_id="p3",
        player_2_provider_id="p4",
    )
    _insert_mapping(db)
    first = ingest_provider_observation(db, _observation(observation_key="stable-key", provider_event_id="event-1"))
    assert first.normalized_inserted == 2

    with pytest.raises(LiveOddsIntegrityError, match="raw observation retry payload differs"):
        ingest_provider_observation(db, _observation(observation_key="stable-key", provider_event_id="event-2"))

    assert db.execute("SELECT count(*) FROM odds_snapshots WHERE match_id = %s", (event_1["match_id"],)).fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM odds_snapshots WHERE match_id = %s", (event_2["match_id"],)).fetchone()[0] == 0
    assert db.execute(
        "SELECT provider_event_id FROM raw_odds_observations WHERE provider_observation_key = 'stable-key'"
    ).fetchone()[0] == "event-1"


def test_service_commits_prior_groups_when_later_group_fails(db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch):
    _insert_match(db)
    _insert_mapping(db, book_key="soft", bookmaker_code="SOFTBOOK", class_name="OFFERED")
    _insert_mapping(db, book_key="broken", bookmaker_code="BROKEN", class_name="OFFERED")
    real_insert = odds_service.insert_live_odds_snapshot

    def fail_broken_bookmaker(*args, **kwargs):
        if kwargs["quote"].bookmaker_code == "BROKEN":
            raise LiveOddsIntegrityError("forced group failure")
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(odds_service, "insert_live_odds_snapshot", fail_broken_bookmaker)

    with pytest.raises(LiveOddsIntegrityError, match="forced group failure"):
        ingest_provider_observation(
            db,
            _observation(
                observation_key="partial-hard-failure",
                markets=(
                    _market(book="soft", key="soft", selections=(ProviderSelection("p1", Decimal("2.25")),)),
                    _market(book="broken", key="broken", selections=(ProviderSelection("p2", Decimal("1.70")),)),
                ),
            ),
        )

    assert db.execute("SELECT count(*) FROM raw_odds_observations").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM odds_snapshots WHERE bookmaker_code = 'SOFTBOOK'").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM odds_snapshots WHERE bookmaker_code = 'BROKEN'").fetchone()[0] == 0
    assert db.execute("SELECT status::text FROM ingest_runs ORDER BY ingest_run_id DESC LIMIT 1").fetchone()[0] == "PARTIAL"


def test_service_event_and_mapping_absence_do_not_create_normalized_odds(db: psycopg.Connection):
    _insert_match(db)
    unmatched = ingest_provider_observation(db, _observation(provider_event_id="missing", observation_key="unmatched"))
    before_mapping = ingest_provider_observation(db, _observation(observation_key="no-mapping"))
    assert unmatched.unmatched_event == 1
    assert before_mapping.unknown_bookmaker == 1
    assert db.execute("SELECT count(*) FROM raw_odds_observations").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] == 0


def test_service_idempotency_price_movement_timestamps_and_provider_collision(db: psycopg.Connection):
    _insert_match(db, provider_code="PROVIDER_A", provider_event_id="event-1")
    _insert_match(db, provider_code="PROVIDER_B", provider_event_id="event-1")
    _insert_mapping(db, provider_code="PROVIDER_A")
    _insert_mapping(db, provider_code="PROVIDER_B")

    first = ingest_provider_observation(db, _observation(provider_code="PROVIDER_A", observation_key="same"))
    retry = ingest_provider_observation(db, _observation(provider_code="PROVIDER_A", observation_key="same"))
    same_price_new = ingest_provider_observation(db, _observation(provider_code="PROVIDER_A", observation_key="new-same-price"))
    moved = ingest_provider_observation(
        db,
        _observation(
            provider_code="PROVIDER_A",
            observation_key="moved",
            markets=(_market(selections=(ProviderSelection("p1", Decimal("1.90")), ProviderSelection("p2", Decimal("1.95")))),),
        ),
    )
    other_provider = ingest_provider_observation(db, _observation(provider_code="PROVIDER_B", observation_key="same"))

    assert first.normalized_inserted == 2
    assert retry.normalized_existing == 2
    assert same_price_new.normalized_inserted == 2
    assert moved.normalized_inserted == 2
    assert other_provider.normalized_inserted == 2
    assert db.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] == 8
    assert db.execute("SELECT count(DISTINCT source_snapshot_id) FROM odds_snapshots").fetchone()[0] == 4
    assert db.execute("SELECT count(DISTINCT source_snapshot_id) FROM odds_snapshots WHERE source_system = 'SHARP' AND bookmaker_code = 'PINNACLE'").fetchone()[0] == 4
    assert db.execute("SELECT bool_and(source_observed_at = %s) FROM odds_snapshots WHERE raw_odds_observation_id = 1", (_utc(5, 9),)).fetchone()[0]

    received = _observation(provider_code="PROVIDER_A", observation_key="receive-time", provider_observed_at=None, received_at=_utc(6, 10))
    ingest_provider_observation(db, received)
    assert db.execute(
        """
        SELECT observation_time_basis::text, observation_time
        FROM raw_odds_observations
        WHERE provider_observation_key = 'receive-time'
        """
    ).fetchone() == ("RECEIVE_TIME", _utc(6, 10))


def test_service_missing_leg_and_unsupported_status_do_not_fabricate_rows(db: psycopg.Connection):
    _insert_match(db)
    _insert_mapping(db)
    single_leg = ingest_provider_observation(
        db,
        _observation(observation_key="single", markets=(_market(selections=(ProviderSelection("p1", Decimal("2.10")),)),)),
    )
    bad_status = ingest_provider_observation(
        db,
        _observation(observation_key="bad-status", markets=(_market(status="HALTED"),)),
    )
    assert single_leg.normalized_inserted == 1
    assert bad_status.invalid_status == 1
    assert db.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0] == 1


def test_service_updates_ingest_run_reconciliation_status(db: psycopg.Connection):
    _insert_match(db)
    _insert_mapping(db)
    completed = ingest_provider_observation(db, _observation(observation_key="completed"))
    partial = ingest_provider_observation(db, _observation(observation_key="partial", markets=(_market(book="missing"),)))

    assert completed.terminal_status == "COMPLETED"
    assert partial.terminal_status == "PARTIAL"
    statuses = db.execute("SELECT status::text FROM ingest_runs ORDER BY ingest_run_id").fetchall()
    assert statuses[-2:] == [("COMPLETED",), ("PARTIAL",)]
