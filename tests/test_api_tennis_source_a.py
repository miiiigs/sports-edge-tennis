from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.data.sources.api_tennis_live import (
    SOURCE_SYSTEM,
    ingest_api_tennis_source_a_fixtures,
    parse_api_tennis_fixtures,
)


def _received_at() -> datetime:
    return datetime(2026, 7, 20, 10, 30, tzinfo=UTC)


def _fixture(**overrides):
    payload = {
        "event_key": "7789001",
        "tournament_key": "4901",
        "tournament_name": "ATP Challenger Porto",
        "event_type_type": "Atp Challenger Men Singles",
        "event_date": "2026-07-21",
        "event_time": "14:30",
        "event_status": "Not Started",
        "event_first_player": "Gustavo Heide",
        "event_second_player": "Facundo Mena",
        "first_player_key": "381122",
        "second_player_key": "104925",
        "event_surface": "Clay",
        "event_round": "SF",
        "event_update": "2026-07-20T09:15:00Z",
    }
    payload.update(overrides)
    return payload


def test_parse_api_tennis_source_a_accepts_atp_and_challenger_mens_singles():
    records, rejected = parse_api_tennis_fixtures(
        [
            _fixture(),
            _fixture(
                event_key="7789002",
                tournament_key="6000",
                tournament_name="Queen's Club",
                event_type_type="Atp Singles",
                first_player_key="100",
                second_player_key="200",
            ),
        ],
        received_at=_received_at(),
    )

    assert rejected == []
    assert [record.tour_scope for record in records] == ["CHALLENGER", "ATP_MAIN"]
    assert [record.competition_tier for record in records] == ["CHALLENGER", "ATP_TOUR"]
    assert records[0].source_observed_at == datetime(2026, 7, 20, 9, 15, tzinfo=UTC)
    assert records[0].as_of == records[0].source_observed_at


def test_parse_api_tennis_source_a_uses_receive_time_when_provider_timestamp_missing():
    fixture = _fixture()
    fixture.pop("event_update")

    records, rejected = parse_api_tennis_fixtures([fixture], received_at=_received_at())

    assert rejected == []
    assert records[0].source_observed_at == _received_at()
    assert records[0].as_of == _received_at()


def test_parse_api_tennis_source_a_rejects_out_of_scope_and_incomplete_fixtures():
    missing_event_key = _fixture()
    missing_event_key.pop("event_key")
    missing_player_key = _fixture()
    missing_player_key.pop("first_player_key")
    records, rejected = parse_api_tennis_fixtures(
        [
            missing_event_key,
            missing_player_key,
            _fixture(event_type_type="Wta Singles"),
            _fixture(event_type_type="Atp Doubles"),
            _fixture(event_type_type="Atp Mixed Doubles"),
            _fixture(event_type_type="Atp Legends"),
            _fixture(second_player_key="381122"),
            _fixture(event_date="tomorrow"),
        ],
        received_at=_received_at(),
    )

    assert records == []
    assert [row["reason"] for row in rejected] == [
        "missing_required_field:event_key",
        "missing_required_field:first_player_key",
        "out_of_scope_event_type",
        "out_of_scope_event_type",
        "out_of_scope_event_type",
        "unsupported_event_type",
        "duplicate_player_source_id",
        "invalid_event_date",
    ]


def test_ingest_api_tennis_source_a_persists_source_ids_and_player_order(db: psycopg.Connection):
    outcome = ingest_api_tennis_source_a_fixtures(db, [_fixture()], received_at=_received_at())

    assert outcome.status == "COMPLETED"
    assert outcome.rows_seen == 1
    assert outcome.rows_ingested == 1
    assert outcome.rows_rejected == 0
    ingested = outcome.matches[0]

    player_rows = db.execute(
        """
        SELECT psi.source_player_id, p.canonical_name
        FROM player_source_ids psi
        JOIN players p ON p.player_id = psi.player_id
        WHERE psi.source_system = %s
        ORDER BY psi.source_player_id
        """,
        (SOURCE_SYSTEM,),
    ).fetchall()
    assert player_rows == [("104925", "Facundo Mena"), ("381122", "Gustavo Heide")]

    source_match = db.execute(
        """
        SELECT source_match_id, source_system
        FROM match_source_ids
        WHERE match_id = %s
        """,
        (ingested.match_id,),
    ).fetchone()
    assert source_match == ("7789001", SOURCE_SYSTEM)

    version = db.execute(
        """
        SELECT
            mv.player_1_id,
            mv.player_2_id,
            mv.tour_scope::text,
            mv.competition_tier::text,
            mv.match_status::text,
            mv.source_observed_at,
            mv.as_of,
            t.canonical_name
        FROM match_versions mv
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE mv.match_id = %s
        """,
        (ingested.match_id,),
    ).fetchone()
    assert version == (
        ingested.player_1_id,
        ingested.player_2_id,
        "CHALLENGER",
        "CHALLENGER",
        "SCHEDULED",
        datetime(2026, 7, 20, 9, 15, tzinfo=UTC),
        datetime(2026, 7, 20, 9, 15, tzinfo=UTC),
        "ATP Challenger Porto",
    )


def test_ingest_api_tennis_source_a_replay_is_idempotent_for_core_entities(db: psycopg.Connection):
    fixture = _fixture()

    first = ingest_api_tennis_source_a_fixtures(db, [fixture], received_at=_received_at())
    second = ingest_api_tennis_source_a_fixtures(db, [fixture], received_at=_received_at())

    assert first.matches[0].match_id == second.matches[0].match_id
    assert first.matches[0].match_version_id == second.matches[0].match_version_id
    assert db.execute("SELECT count(*) FROM players").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM tournaments").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM match_versions").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM match_source_ids").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM match_version_sources").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM ingest_runs").fetchone()[0] == 2


def test_ingest_api_tennis_source_a_empty_input_is_noop(db: psycopg.Connection):
    outcome = ingest_api_tennis_source_a_fixtures(db, [], received_at=_received_at())

    assert outcome.status == "COMPLETED"
    assert outcome.rows_seen == 0
    assert outcome.rows_ingested == 0
    assert outcome.rows_rejected == 0
    assert db.execute("SELECT count(*) FROM ingest_runs").fetchone()[0] == 0


def test_ingest_api_tennis_source_a_reschedule_keeps_match_and_adds_version(
    db: psycopg.Connection,
):
    first = ingest_api_tennis_source_a_fixtures(db, [_fixture()], received_at=_received_at())
    second = ingest_api_tennis_source_a_fixtures(
        db,
        [
            _fixture(
                event_time="15:00",
                event_update="2026-07-20T11:15:00Z",
            )
        ],
        received_at=datetime(2026, 7, 20, 11, 30, tzinfo=UTC),
    )

    assert first.matches[0].match_id == second.matches[0].match_id
    assert first.matches[0].match_version_id != second.matches[0].match_version_id
    versions = db.execute(
        """
        SELECT match_start_at, source_observed_at
        FROM match_versions
        WHERE match_id = %s
        ORDER BY source_observed_at
        """,
        (first.matches[0].match_id,),
    ).fetchall()
    assert versions == [
        (
            datetime(2026, 7, 21, 14, 30, tzinfo=UTC),
            datetime(2026, 7, 20, 9, 15, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 21, 15, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 11, 15, tzinfo=UTC),
        ),
    ]
