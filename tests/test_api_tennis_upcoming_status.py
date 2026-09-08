from datetime import UTC, datetime

from sports_edge_tennis.data.normalize import normalize_person_lookup
from sports_edge_tennis.data.sources.api_tennis_live import parse_api_tennis_fixtures
from sports_edge_tennis.live.api_tennis_fixture_compat import (
    _prelink_api_tennis_players_to_history,
    ingest_api_tennis_source_a_fixtures_with_upcoming_status,
    normalize_api_tennis_upcoming_status,
)


def _received_at() -> datetime:
    return datetime(2026, 9, 8, 0, 0, tzinfo=UTC)


def _fixture(**overrides):
    payload = {
        "event_key": "900001",
        "tournament_key": "4901",
        "tournament_name": "ATP Challenger Test",
        "event_type_type": "Challenger Men Singles",
        "event_date": "2026-09-08",
        "event_time": "12:00",
        "event_status": "",
        "event_live": "0",
        "event_winner": None,
        "event_final_result": "-",
        "event_first_player": "Player One",
        "event_second_player": "Player Two",
        "first_player_key": "101",
        "second_player_key": "202",
        "event_surface": "Hard",
    }
    payload.update(overrides)
    return payload


def _seed_historical_alias(db, name: str) -> int:
    player_id = db.execute(
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (name,),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO player_aliases (
            player_id,
            source_system,
            alias,
            normalized_alias
        )
        VALUES (%s, 'TENNIS_DATA', %s, %s)
        """,
        (player_id, name, normalize_person_lookup(name)),
    )
    return player_id


def test_safe_blank_future_fixture_is_promoted_to_scheduled():
    normalized = normalize_api_tennis_upcoming_status(
        [_fixture()],
        received_at=_received_at(),
    )

    assert normalized[0]["event_status"] == "Not Started"
    records, rejected = parse_api_tennis_fixtures(
        normalized,
        received_at=_received_at(),
    )
    assert rejected == []
    assert len(records) == 1
    assert records[0].match_status == "SCHEDULED"


def test_blank_status_remains_fail_closed_when_fixture_is_live():
    normalized = normalize_api_tennis_upcoming_status(
        [_fixture(event_live="1")],
        received_at=_received_at(),
    )

    assert normalized[0]["event_status"] == ""
    records, rejected = parse_api_tennis_fixtures(normalized, received_at=_received_at())
    assert records == []
    assert rejected[0]["reason"] == "missing_required_field:event_status"


def test_blank_status_remains_fail_closed_when_winner_or_result_exists():
    normalized = normalize_api_tennis_upcoming_status(
        [
            _fixture(event_key="900002", event_winner="First Player"),
            _fixture(event_key="900003", event_final_result="2 - 0"),
        ],
        received_at=_received_at(),
    )

    assert [row["event_status"] for row in normalized] == ["", ""]


def test_blank_status_remains_fail_closed_for_non_future_fixture():
    normalized = normalize_api_tennis_upcoming_status(
        [_fixture(event_date="2026-09-07")],
        received_at=_received_at(),
    )

    assert normalized[0]["event_status"] == ""


def test_explicit_provider_status_is_never_overwritten():
    normalized = normalize_api_tennis_upcoming_status(
        [_fixture(event_status="Finished", event_final_result="2 - 0")],
        received_at=_received_at(),
    )

    assert normalized[0]["event_status"] == "Finished"


def test_unique_historical_alias_is_linked_before_source_a_ingest(db):
    historical_player_id = _seed_historical_alias(db, "Player One")

    outcome = ingest_api_tennis_source_a_fixtures_with_upcoming_status(
        db,
        [_fixture()],
        received_at=_received_at(),
    )

    assert outcome.status == "COMPLETED"
    assert outcome.rows_ingested == 1
    assert outcome.matches[0].player_1_id == historical_player_id
    mapping = db.execute(
        """
        SELECT player_id
        FROM player_source_ids
        WHERE source_system = 'API_TENNIS'
          AND source_player_id = '101'
        """
    ).fetchone()
    assert mapping == (historical_player_id,)


def test_ambiguous_historical_alias_is_not_guessed(db):
    _seed_historical_alias(db, "Player One")
    _seed_historical_alias(db, "Player One")

    linked = _prelink_api_tennis_players_to_history(db, [_fixture()])

    assert linked == 0
    assert db.execute(
        """
        SELECT count(*)
        FROM player_source_ids
        WHERE source_system = 'API_TENNIS'
          AND source_player_id = '101'
        """
    ).fetchone()[0] == 0


def test_existing_api_tennis_mapping_is_never_rewritten(db):
    historical_player_id = _seed_historical_alias(db, "Player One")
    legacy_player_id = db.execute(
        "INSERT INTO players (canonical_name) VALUES ('Player One') RETURNING player_id"
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id)
        VALUES (%s, 'API_TENNIS', '101')
        """,
        (legacy_player_id,),
    )

    linked = _prelink_api_tennis_players_to_history(db, [_fixture()])

    assert linked == 0
    mapping = db.execute(
        """
        SELECT player_id
        FROM player_source_ids
        WHERE source_system = 'API_TENNIS'
          AND source_player_id = '101'
        """
    ).fetchone()
    assert mapping == (legacy_player_id,)
    assert mapping != (historical_player_id,)
