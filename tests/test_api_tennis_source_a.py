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
    assert records[0].source_observed_at == _received_at()
    assert records[0].as_of == records[0].source_observed_at


def test_parse_api_tennis_source_a_uses_receive_time_and_ignores_raw_timestamps():
    fixture = _fixture(
        source_observed_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
        event_updated_at="2025-01-01T00:00:00Z",
        event_update="2025-01-01T00:00:00Z",
    )

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
        SELECT psi.source_player_id, psi.player_id, p.canonical_name
        FROM player_source_ids psi
        JOIN players p ON p.player_id = psi.player_id
        WHERE psi.source_system = %s
        ORDER BY psi.source_player_id
        """,
        (SOURCE_SYSTEM,),
    ).fetchall()
    assert [(row[0], row[2]) for row in player_rows] == [
        ("104925", "Facundo Mena"),
        ("381122", "Gustavo Heide"),
    ]
    player_id_by_source = {row[0]: row[1] for row in player_rows}

    source_match = db.execute(
        """
        SELECT source_match_id, source_system
        FROM match_source_ids
        WHERE match_id = %s
        """,
        (ingested.match_id,),
    ).fetchone()
    assert source_match == ("7789001", SOURCE_SYSTEM)

    tournament_source = db.execute(
        """
        SELECT source_tournament_id, source_system
        FROM tournament_source_ids
        WHERE source_tournament_id = %s
        """,
        ("4901",),
    ).fetchone()
    assert tournament_source == ("4901", SOURCE_SYSTEM)

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
        player_id_by_source["381122"],
        player_id_by_source["104925"],
        "CHALLENGER",
        "CHALLENGER",
        "SCHEDULED",
        _received_at(),
        _received_at(),
        "ATP Challenger Porto",
    )
    assert ingested.player_1_id == player_id_by_source["381122"]
    assert ingested.player_2_id == player_id_by_source["104925"]


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


def test_ingest_api_tennis_source_a_rejects_same_event_with_different_player_1(
    db: psycopg.Connection,
):
    first = ingest_api_tennis_source_a_fixtures(db, [_fixture()], received_at=_received_at())
    conflicting = ingest_api_tennis_source_a_fixtures(
        db,
        [
            _fixture(
                first_player_key="999999",
                event_first_player="Carlos Alcaraz",
                event_update="2026-07-20T11:15:00Z",
            )
        ],
        received_at=datetime(2026, 7, 20, 11, 30, tzinfo=UTC),
    )

    assert conflicting.status == "FAILED"
    assert conflicting.rows_ingested == 0
    assert conflicting.rejected_rows == (
        {
            "event_key": "7789001",
            "reason": "event_identity_conflict:player_1",
        },
    )
    assert db.execute("SELECT count(*) FROM match_versions").fetchone()[0] == 1
    unchanged = db.execute(
        "SELECT player_1_id, player_2_id FROM match_versions WHERE match_id = %s",
        (first.matches[0].match_id,),
    ).fetchone()
    assert unchanged == (first.matches[0].player_1_id, first.matches[0].player_2_id)


def test_ingest_api_tennis_source_a_rejects_reversed_player_order(db: psycopg.Connection):
    ingest_api_tennis_source_a_fixtures(db, [_fixture()], received_at=_received_at())

    conflicting = ingest_api_tennis_source_a_fixtures(
        db,
        [
            _fixture(
                first_player_key="104925",
                second_player_key="381122",
                event_first_player="Facundo Mena",
                event_second_player="Gustavo Heide",
            )
        ],
        received_at=datetime(2026, 7, 20, 11, 30, tzinfo=UTC),
    )

    assert conflicting.status == "FAILED"
    assert conflicting.rows_ingested == 0
    assert conflicting.rejected_rows == (
        {
            "event_key": "7789001",
            "reason": "event_identity_conflict:player_1",
        },
    )
    assert db.execute("SELECT count(*) FROM match_versions").fetchone()[0] == 1


def test_ingest_api_tennis_source_a_rejects_same_event_with_different_tournament(
    db: psycopg.Connection,
):
    ingest_api_tennis_source_a_fixtures(db, [_fixture()], received_at=_received_at())

    conflicting = ingest_api_tennis_source_a_fixtures(
        db,
        [_fixture(tournament_key="7777", tournament_name="ATP Challenger Milan")],
        received_at=datetime(2026, 7, 20, 11, 30, tzinfo=UTC),
    )

    assert conflicting.status == "FAILED"
    assert conflicting.rows_ingested == 0
    assert conflicting.rejected_rows == (
        {
            "event_key": "7789001",
            "reason": "event_identity_conflict:tournament",
        },
    )
    assert db.execute("SELECT count(*) FROM match_versions").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM tournaments").fetchone()[0] == 1


def test_ingest_api_tennis_source_a_persists_receive_time_despite_raw_timestamp_fields(
    db: psycopg.Connection,
):
    received_at = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
    outcome = ingest_api_tennis_source_a_fixtures(
        db,
        [
            _fixture(
                source_observed_at="2025-01-01T00:00:00Z",
                updated_at="2025-01-01T00:00:00Z",
                event_updated_at="2025-01-01T00:00:00Z",
                event_update="2025-01-01T00:00:00Z",
            )
        ],
        received_at=received_at,
    )

    persisted = db.execute(
        "SELECT source_observed_at, as_of FROM match_versions WHERE match_id = %s",
        (outcome.matches[0].match_id,),
    ).fetchone()
    assert persisted == (received_at, received_at)


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
            _received_at(),
        ),
        (
            datetime(2026, 7, 21, 15, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 11, 30, tzinfo=UTC),
        ),
    ]


def test_ingest_api_tennis_source_a_status_update_keeps_match_and_adds_version(
    db: psycopg.Connection,
):
    first = ingest_api_tennis_source_a_fixtures(db, [_fixture()], received_at=_received_at())
    second = ingest_api_tennis_source_a_fixtures(
        db,
        [_fixture(event_status="Postponed")],
        received_at=datetime(2026, 7, 20, 11, 30, tzinfo=UTC),
    )

    assert first.matches[0].match_id == second.matches[0].match_id
    assert first.matches[0].match_version_id != second.matches[0].match_version_id
    statuses = db.execute(
        """
        SELECT match_status::text
        FROM match_versions
        WHERE match_id = %s
        ORDER BY source_observed_at
        """,
        (first.matches[0].match_id,),
    ).fetchall()
    assert statuses == [("SCHEDULED",), ("POSTPONED",)]


def _correct_tournament(db: psycopg.Connection, source_tournament_id: str, canonical_id: int) -> None:
    db.execute(
        "INSERT INTO tournament_identity_corrections "
        "(source_system, source_tournament_id, canonical_tournament_id, reason) "
        "VALUES (%s, %s, %s, 'REVIEWED')",
        (SOURCE_SYSTEM, source_tournament_id, canonical_id),
    )


def _shanghai_fixture(**overrides):
    return _fixture(
        tournament_key="1667",
        tournament_name="ATP Shanghai",
        event_type_type="Atp Singles",
        event_surface="Hard",
        event_round="R64",
        **overrides,
    )


def _canonical_shanghai_with_duplicate_mapping(db: psycopg.Connection) -> tuple[int, int]:
    canonical = db.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Shanghai Masters', 'ATP_MAIN', 'MASTERS_1000') RETURNING tournament_id"
    ).fetchone()[0]
    duplicate = db.execute(
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Shanghai', 'ATP_MAIN', 'ATP_TOUR') RETURNING tournament_id"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id) "
        "VALUES (%s, %s, '1667')",
        (duplicate, SOURCE_SYSTEM),
    )
    return canonical, duplicate


def _match_tournament(db: psycopg.Connection) -> int:
    return db.execute(
        "SELECT te.tournament_id FROM match_source_ids msi "
        "JOIN matches m ON m.match_id = msi.match_id "
        "JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id "
        "WHERE msi.source_system = %s AND msi.source_match_id = '7789001'",
        (SOURCE_SYSTEM,),
    ).fetchone()[0]


def test_ingest_accepts_updates_for_event_created_under_corrected_tournament(
    db: psycopg.Connection,
):
    # Production regression (ATP Shanghai 2026): matches created after the
    # reviewed correction live under the corrected canonical tournament, while
    # tournament_source_ids still carries the historical duplicate mapping.
    # Later observations (status/result updates) must not be rejected.
    canonical, _duplicate = _canonical_shanghai_with_duplicate_mapping(db)
    _correct_tournament(db, "1667", canonical)

    first = ingest_api_tennis_source_a_fixtures(
        db, [_shanghai_fixture()], received_at=_received_at()
    )
    assert first.rows_ingested == 1
    assert _match_tournament(db) == canonical

    update = ingest_api_tennis_source_a_fixtures(
        db,
        [_shanghai_fixture(event_status="Finished", event_update="2026-07-21T16:45:00Z")],
        received_at=datetime(2026, 7, 21, 17, 0, tzinfo=UTC),
    )

    assert update.rejected_rows == ()
    assert update.rows_ingested == 1
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM match_versions").fetchone()[0] == 2
    latest_status = db.execute(
        "SELECT match_status::text FROM match_versions ORDER BY match_version_id DESC LIMIT 1"
    ).fetchone()[0]
    assert latest_status == "COMPLETED"


def test_ingest_accepts_updates_for_event_created_before_tournament_correction(
    db: psycopg.Connection,
):
    canonical, duplicate = _canonical_shanghai_with_duplicate_mapping(db)
    first = ingest_api_tennis_source_a_fixtures(
        db, [_shanghai_fixture()], received_at=_received_at()
    )
    assert first.rows_ingested == 1
    assert _match_tournament(db) == duplicate

    _correct_tournament(db, "1667", canonical)
    update = ingest_api_tennis_source_a_fixtures(
        db,
        [_shanghai_fixture(event_status="Finished", event_update="2026-07-21T16:45:00Z")],
        received_at=datetime(2026, 7, 21, 17, 0, tzinfo=UTC),
    )

    assert update.rejected_rows == ()
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    # Historical lineage is preserved; the match is not moved between tournaments.
    assert _match_tournament(db) == duplicate


def test_ingest_still_rejects_unrelated_tournament_when_correction_exists(
    db: psycopg.Connection,
):
    canonical, _duplicate = _canonical_shanghai_with_duplicate_mapping(db)
    _correct_tournament(db, "1667", canonical)
    ingest_api_tennis_source_a_fixtures(db, [_shanghai_fixture()], received_at=_received_at())

    conflicting = ingest_api_tennis_source_a_fixtures(
        db,
        [_fixture(tournament_key="7777", tournament_name="ATP Challenger Milan")],
        received_at=datetime(2026, 7, 20, 11, 30, tzinfo=UTC),
    )

    assert conflicting.rejected_rows == (
        {"event_key": "7789001", "reason": "event_identity_conflict:tournament"},
    )
    assert db.execute("SELECT count(*) FROM match_versions").fetchone()[0] == 1


def test_corrected_lineage_still_rejects_changed_player(db: psycopg.Connection):
    canonical, _duplicate = _canonical_shanghai_with_duplicate_mapping(db)
    _correct_tournament(db, "1667", canonical)
    ingest_api_tennis_source_a_fixtures(db, [_shanghai_fixture()], received_at=_received_at())

    conflicting = ingest_api_tennis_source_a_fixtures(
        db,
        [
            _shanghai_fixture(
                first_player_key="999999",
                event_first_player="Carlos Alcaraz",
                event_update="2026-07-20T11:15:00Z",
            )
        ],
        received_at=datetime(2026, 7, 20, 11, 30, tzinfo=UTC),
    )

    assert conflicting.rejected_rows == (
        {"event_key": "7789001", "reason": "event_identity_conflict:player_1"},
    )
    assert db.execute("SELECT count(*) FROM match_versions").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1


def test_correction_from_another_source_system_does_not_widen_identity(
    db: psycopg.Connection,
):
    # Only a reviewed correction for this provider's own tournament id counts.
    first = ingest_api_tennis_source_a_fixtures(db, [_fixture()], received_at=_received_at())
    existing_tournament = _match_tournament(db)
    db.execute(
        "INSERT INTO tournament_identity_corrections "
        "(source_system, source_tournament_id, canonical_tournament_id, reason) "
        "VALUES ('SPORTRADAR', '7777', %s, 'REVIEWED')",
        (existing_tournament,),
    )

    conflicting = ingest_api_tennis_source_a_fixtures(
        db,
        [_fixture(tournament_key="7777", tournament_name="ATP Challenger Milan")],
        received_at=datetime(2026, 7, 20, 11, 30, tzinfo=UTC),
    )

    assert first.rows_ingested == 1
    assert conflicting.rejected_rows == (
        {"event_key": "7789001", "reason": "event_identity_conflict:tournament"},
    )
    assert db.execute("SELECT count(*) FROM match_versions").fetchone()[0] == 1
