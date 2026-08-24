import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
UP_MIGRATION_PATH = REPO_ROOT / "db" / "migrations" / "0001_tennis_data_foundation.up.sql"
DOWN_MIGRATION_PATH = REPO_ROOT / "db" / "migrations" / "0001_tennis_data_foundation.down.sql"
POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL")


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)


def _run_psql(dsn: str, sql_path: Path) -> None:
    subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-f", str(sql_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _table_exists(conn: psycopg.Connection, table_name: str) -> bool:
    return (
        conn.execute("SELECT to_regclass(%s)", (f"public.{table_name}",)).fetchone()[0]
        is not None
    )


def _public_table_names(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
        """
    ).fetchall()
    return [row[0] for row in rows]


def _enum_type_names(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.typname
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
          AND t.typtype = 'e'
        ORDER BY t.typname
        """
    ).fetchall()
    return [row[0] for row in rows]


def _reset_db(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        TRUNCATE TABLE
            data_health_checks,
            decision_clv,
            decision_settlements,
            decision_agents,
            decisions,
            predictions,
            rating_snapshots,
            odds_snapshots,
            match_stat_observations,
            match_result_observations,
            match_version_sources,
            match_source_ids,
            match_versions,
            matches,
            tournament_editions,
            tournament_source_ids,
            tournaments,
            identity_resolution_candidates,
            identity_resolution_events,
            player_aliases,
            player_source_ids,
            players,
            ingest_runs
        RESTART IDENTITY CASCADE
        """
    )


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_player(conn: psycopg.Connection, canonical_name: str) -> int:
    return _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (canonical_name,),
    )


def _insert_tournament_edition(conn: psycopg.Connection) -> int:
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
        ("Sample Open",),
    )
    return _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (
            tournament_id,
            season_year,
            edition_name,
            surface,
            start_date,
            end_date
        )
        VALUES (%s, 2026, %s, 'HARD', DATE '2026-08-24', DATE '2026-08-30')
        RETURNING tournament_edition_id
        """,
        (tournament_id, "Sample Open 2026"),
    )


def _insert_match_fixture(
    conn: psycopg.Connection,
    *,
    best_of_sets: int = 3,
    player_1_id: int | None = None,
    player_2_id: int | None = None,
    source_observed_at: datetime | None = None,
    as_of: datetime | None = None,
) -> dict[str, int]:
    observed_at = source_observed_at or _utc(8, 0)
    valid_at = as_of or observed_at
    p1_id = player_1_id or _insert_player(conn, "Player One")
    p2_id = player_2_id or _insert_player(conn, "Player Two")
    edition_id = _insert_tournament_edition(conn)
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    match_version_id = _fetchval(
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
        VALUES (
            %s,
            %s,
            %s,
            %s,
            'ATP_MAIN',
            'ATP_250',
            'HARD',
            'R32',
            %s,
            FALSE,
            'SCHEDULED',
            %s,
            %s
        )
        RETURNING match_version_id
        """,
        (match_id, p1_id, p2_id, _utc(12, 0), best_of_sets, observed_at, valid_at),
    )
    return {
        "edition_id": edition_id,
        "match_id": match_id,
        "match_version_id": match_version_id,
        "player_1_id": p1_id,
        "player_2_id": p2_id,
    }


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    if not POSTGRES_DSN:
        pytest.skip("PostgreSQL DSN not configured")
    if shutil.which("psql") is None:
        pytest.skip("psql is required for PostgreSQL integration tests")
    return POSTGRES_DSN


@pytest.fixture(scope="session")
def migrated_postgres(postgres_dsn: str) -> str:
    _run_psql(postgres_dsn, DOWN_MIGRATION_PATH)
    _run_psql(postgres_dsn, UP_MIGRATION_PATH)
    try:
        yield postgres_dsn
    finally:
        _run_psql(postgres_dsn, DOWN_MIGRATION_PATH)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert _public_table_names(conn) == []
            assert _enum_type_names(conn) == []
        _run_psql(postgres_dsn, UP_MIGRATION_PATH)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert _table_exists(conn, "players")
            assert _table_exists(conn, "match_version_sources")
        _run_psql(postgres_dsn, DOWN_MIGRATION_PATH)


@pytest.fixture()
def db(migrated_postgres: str) -> psycopg.Connection:
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        _reset_db(conn)
        yield conn
        _reset_db(conn)


def test_postgres_migration_bootstraps_expected_tables(db: psycopg.Connection):
    for table_name in (
        "players",
        "identity_resolution_candidates",
        "match_version_sources",
        "odds_snapshots",
        "rating_snapshots",
        "predictions",
        "decisions",
    ):
        assert _table_exists(db, table_name)


def test_identity_resolution_constraints_are_fail_closed(db: psycopg.Connection):
    ambiguous_player_1 = _insert_player(db, "Ambiguous One")
    ambiguous_player_2 = _insert_player(db, "Ambiguous Two")

    duplicate_source_player = _insert_player(db, "Source Player")
    db.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id)
        VALUES (%s, 'SACKMANN', 'player-123')
        """,
        (duplicate_source_player,),
    )
    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO player_source_ids (player_id, source_system, source_player_id)
            VALUES (%s, 'SACKMANN', 'player-123')
            """,
            (_insert_player(db, "Conflicting Source Player"),),
        )

    _insert_player(db, "Shared Name")
    _insert_player(db, "Shared Name")

    with pytest.raises(psycopg.Error):
        with db.transaction():
            event_id = _fetchval(
                db,
                """
                INSERT INTO identity_resolution_events (
                    source_system,
                    source_record_id,
                    raw_player_name,
                    normalized_player_name,
                    resolution_status,
                    source_observed_at,
                    as_of
                )
                VALUES (%s, %s, %s, %s, 'AMBIGUOUS', %s, %s)
                RETURNING identity_resolution_event_id
                """,
                (
                    "TENNIS_DATA",
                    "amb-0",
                    "Player Ambiguous",
                    "player ambiguous",
                    _utc(8, 15),
                    _utc(8, 15),
                ),
            )
            assert event_id > 0

    with db.transaction():
        event_id = _fetchval(
            db,
            """
            INSERT INTO identity_resolution_events (
                source_system,
                source_record_id,
                raw_player_name,
                normalized_player_name,
                resolution_status,
                source_observed_at,
                as_of
            )
            VALUES (%s, %s, %s, %s, 'AMBIGUOUS', %s, %s)
            RETURNING identity_resolution_event_id
            """,
            (
                "TENNIS_DATA",
                "amb-1",
                "Player Ambiguous",
                "player ambiguous",
                _utc(8, 20),
                _utc(8, 20),
            ),
        )
        db.execute(
            """
            INSERT INTO identity_resolution_candidates (identity_resolution_event_id, player_id)
            VALUES (%s, %s), (%s, %s)
            """,
            (event_id, ambiguous_player_1, event_id, ambiguous_player_2),
        )

    ambiguous_count = db.execute(
        "SELECT count(*) FROM identity_resolution_candidates WHERE identity_resolution_event_id = %s",
        (event_id,),
    ).fetchone()[0]
    assert ambiguous_count == 2

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO identity_resolution_candidates (identity_resolution_event_id, player_id)
            VALUES (%s, %s)
            """,
            (event_id, 999999),
        )

    with db.transaction():
        resolved_event_id = _fetchval(
            db,
            """
            INSERT INTO identity_resolution_events (
                source_system,
                source_record_id,
                raw_player_name,
                normalized_player_name,
                resolution_status,
                resolved_player_id,
                source_observed_at,
                as_of
            )
            VALUES (%s, %s, %s, %s, 'RESOLVED', %s, %s, %s)
            RETURNING identity_resolution_event_id
            """,
            (
                "TENNIS_DATA",
                "resolved-1",
                "Player Resolved",
                "player resolved",
                ambiguous_player_1,
                _utc(8, 25),
                _utc(8, 25),
            ),
        )
        assert resolved_event_id > 0

    with pytest.raises(psycopg.Error):
        with db.transaction():
            unmatched_event_id = _fetchval(
                db,
                """
                INSERT INTO identity_resolution_events (
                    source_system,
                    source_record_id,
                    raw_player_name,
                    normalized_player_name,
                    resolution_status,
                    source_observed_at,
                    as_of
                )
                VALUES (%s, %s, %s, %s, 'UNMATCHED', %s, %s)
                RETURNING identity_resolution_event_id
                """,
                (
                    "TENNIS_DATA",
                    "unmatched-1",
                    "Player Missing",
                    "player missing",
                    _utc(8, 30),
                    _utc(8, 30),
                ),
            )
            db.execute(
                """
                INSERT INTO identity_resolution_candidates (identity_resolution_event_id, player_id)
                VALUES (%s, %s)
                """,
                (unmatched_event_id, ambiguous_player_1),
            )


def test_match_versions_best_of_and_player_constraints(db: psycopg.Connection):
    base_player = _insert_player(db, "Same Player")
    edition_id = _insert_tournament_edition(db)
    match_id = _fetchval(
        db,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO match_versions (
                match_id,
                player_1_id,
                player_2_id,
                match_start_at,
                tour_scope,
                competition_tier,
                surface,
                best_of_sets,
                match_status,
                source_observed_at,
                as_of
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                'ATP_MAIN',
                'ATP_250',
                'HARD',
                3,
                'SCHEDULED',
                %s,
                %s
            )
            """,
            (match_id, base_player, base_player, _utc(12, 0), _utc(9, 0), _utc(9, 0)),
        )

    bo3_fixture = _insert_match_fixture(db, best_of_sets=3)
    bo5_fixture = _insert_match_fixture(
        db,
        best_of_sets=5,
        player_1_id=_insert_player(db, "Five Setter One"),
        player_2_id=_insert_player(db, "Five Setter Two"),
        source_observed_at=_utc(9, 10),
        as_of=_utc(9, 10),
    )
    assert bo3_fixture["match_version_id"] > 0
    assert bo5_fixture["match_version_id"] > 0

    with pytest.raises(psycopg.Error):
        _insert_match_fixture(
            db,
            best_of_sets=1,
            player_1_id=_insert_player(db, "Invalid Best Of One"),
            player_2_id=_insert_player(db, "Invalid Best Of Two"),
            source_observed_at=_utc(9, 20),
            as_of=_utc(9, 20),
        )


def test_rating_snapshot_identity_uses_postgres_null_semantics(db: psycopg.Connection):
    player_id = _insert_player(db, "Ratings Player")

    db.execute(
        """
        INSERT INTO rating_snapshots (
            player_id,
            rating_metric,
            surface,
            rating_value,
            model_version,
            feature_version,
            as_of
        )
        VALUES (%s, 'ELO', NULL, 1650.0, 'm1', 'f1', %s)
        """,
        (player_id, _utc(10, 0)),
    )

    db.execute(
        """
        INSERT INTO rating_snapshots (
            player_id,
            rating_metric,
            surface,
            rating_value,
            model_version,
            feature_version,
            as_of
        )
        VALUES (%s, 'ELO', 'HARD', 1700.0, 'm1', 'f1', %s)
        """,
        (player_id, _utc(10, 0)),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO rating_snapshots (
                player_id,
                rating_metric,
                surface,
                rating_value,
                model_version,
                feature_version,
                as_of
            )
            VALUES (%s, 'ELO', NULL, 1660.0, 'm1', 'f1', %s)
            """,
            (player_id, _utc(10, 0)),
        )

    db.execute(
        """
        INSERT INTO rating_snapshots (
            player_id,
            rating_metric,
            surface,
            rating_value,
            model_version,
            feature_version,
            as_of
        )
        VALUES (%s, 'ELO', NULL, 1660.0, 'm1', 'f1', %s)
        """,
        (player_id, _utc(10, 5)),
    )


def test_match_stat_provenance_allows_row_fanout_and_later_observations(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)

    db.execute(
        """
        INSERT INTO match_stat_observations (
            match_id,
            player_id,
            source_system,
            source_record_id,
            observation_kind,
            service_points_played,
            first_serve_points_won,
            second_serve_points_won,
            source_observed_at,
            as_of
        )
        VALUES
            (%s, %s, 'SACKMANN', 'match-stat-1', 'RAW_SOURCE', 60, 30, 15, %s, %s),
            (%s, %s, 'SACKMANN', 'match-stat-1', 'RAW_SOURCE', 58, 28, 14, %s, %s)
        """,
        (
            fixture["match_id"],
            fixture["player_1_id"],
            _utc(9, 0),
            _utc(9, 0),
            fixture["match_id"],
            fixture["player_2_id"],
            _utc(9, 0),
            _utc(9, 0),
        ),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO match_stat_observations (
                match_id,
                player_id,
                source_system,
                source_record_id,
                observation_kind,
                service_points_played,
                first_serve_points_won,
                second_serve_points_won,
                source_observed_at,
                as_of
            )
            VALUES (%s, %s, 'SACKMANN', 'match-stat-1', 'RAW_SOURCE', 60, 30, 15, %s, %s)
            """,
            (fixture["match_id"], fixture["player_1_id"], _utc(9, 0), _utc(9, 0)),
        )

    db.execute(
        """
        INSERT INTO match_stat_observations (
            match_id,
            player_id,
            source_system,
            source_record_id,
            observation_kind,
            service_points_played,
            first_serve_points_won,
            second_serve_points_won,
            source_observed_at,
            as_of
        )
        VALUES (%s, %s, 'SACKMANN', 'match-stat-1', 'RAW_SOURCE', 61, 31, 15, %s, %s)
        """,
        (fixture["match_id"], fixture["player_1_id"], _utc(9, 5), _utc(9, 5)),
    )


def test_match_stat_hash_fallback_allows_row_fanout_and_later_observations(
    db: psycopg.Connection,
):
    fixture = _insert_match_fixture(db)

    db.execute(
        """
        INSERT INTO match_stat_observations (
            match_id,
            player_id,
            source_system,
            source_record_hash,
            observation_kind,
            service_points_played,
            first_serve_points_won,
            second_serve_points_won,
            source_observed_at,
            as_of
        )
        VALUES
            (%s, %s, 'TENNIS_DATA', 'SE_SOURCE_HASH_V1:match-stat-1', 'RAW_SOURCE', 60, 30, 15, %s, %s),
            (%s, %s, 'TENNIS_DATA', 'SE_SOURCE_HASH_V1:match-stat-1', 'RAW_SOURCE', 58, 28, 14, %s, %s)
        """,
        (
            fixture["match_id"],
            fixture["player_1_id"],
            _utc(9, 10),
            _utc(9, 10),
            fixture["match_id"],
            fixture["player_2_id"],
            _utc(9, 10),
            _utc(9, 10),
        ),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO match_stat_observations (
                match_id,
                player_id,
                source_system,
                source_record_hash,
                observation_kind,
                service_points_played,
                first_serve_points_won,
                second_serve_points_won,
                source_observed_at,
                as_of
            )
            VALUES (%s, %s, 'TENNIS_DATA', 'SE_SOURCE_HASH_V1:match-stat-1', 'RAW_SOURCE', 60, 30, 15, %s, %s)
            """,
            (fixture["match_id"], fixture["player_1_id"], _utc(9, 10), _utc(9, 10)),
        )

    db.execute(
        """
        INSERT INTO match_stat_observations (
            match_id,
            player_id,
            source_system,
            source_record_hash,
            observation_kind,
            service_points_played,
            first_serve_points_won,
            second_serve_points_won,
            source_observed_at,
            as_of
        )
        VALUES (%s, %s, 'TENNIS_DATA', 'SE_SOURCE_HASH_V1:match-stat-1', 'RAW_SOURCE', 61, 31, 15, %s, %s)
        """,
        (fixture["match_id"], fixture["player_1_id"], _utc(9, 15), _utc(9, 15)),
    )


def test_odds_snapshot_provenance_allows_multi_selection_snapshots(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)

    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id,
            source_system,
            bookmaker_code,
            market_type,
            selection_type,
            decimal_price,
            booksum,
            source_snapshot_id,
            source_observed_at,
            as_of
        )
        VALUES
            (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.100000, 1.04000000, 'snap-1', %s, %s),
            (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_2', 1.780000, 1.04000000, 'snap-1', %s, %s)
        """,
        (
            fixture["match_id"],
            _utc(9, 0),
            _utc(9, 0),
            fixture["match_id"],
            _utc(9, 0),
            _utc(9, 0),
        ),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO odds_snapshots (
                match_id,
                source_system,
                bookmaker_code,
                market_type,
                selection_type,
                decimal_price,
                booksum,
                source_snapshot_id,
                source_observed_at,
                as_of
            )
            VALUES (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.100000, 1.04000000, 'snap-1', %s, %s)
            """,
            (fixture["match_id"], _utc(9, 0), _utc(9, 0)),
        )

    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id,
            source_system,
            bookmaker_code,
            market_type,
            selection_type,
            decimal_price,
            booksum,
            source_snapshot_id,
            source_observed_at,
            as_of
        )
        VALUES (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.050000, 1.03000000, 'snap-1', %s, %s)
        """,
        (fixture["match_id"], _utc(9, 5), _utc(9, 5)),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO odds_snapshots (
                match_id,
                source_system,
                bookmaker_code,
                market_type,
                selection_type,
                decimal_price,
                booksum,
                source_snapshot_id,
                source_observed_at,
                as_of
            )
            VALUES (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 1.000000, 1.00000000, 'snap-2', %s, %s)
            """,
            (fixture["match_id"], _utc(9, 10), _utc(9, 10)),
        )


def test_odds_hash_fallback_allows_multi_selection_snapshots(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)

    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id,
            source_system,
            bookmaker_code,
            market_type,
            selection_type,
            decimal_price,
            booksum,
            source_record_hash,
            source_observed_at,
            as_of
        )
        VALUES
            (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.100000, 1.04000000, 'SE_SOURCE_HASH_V1:odds-1', %s, %s),
            (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_2', 1.780000, 1.04000000, 'SE_SOURCE_HASH_V1:odds-1', %s, %s)
        """,
        (
            fixture["match_id"],
            _utc(9, 20),
            _utc(9, 20),
            fixture["match_id"],
            _utc(9, 20),
            _utc(9, 20),
        ),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO odds_snapshots (
                match_id,
                source_system,
                bookmaker_code,
                market_type,
                selection_type,
                decimal_price,
                booksum,
                source_record_hash,
                source_observed_at,
                as_of
            )
            VALUES (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.100000, 1.04000000, 'SE_SOURCE_HASH_V1:odds-1', %s, %s)
            """,
            (fixture["match_id"], _utc(9, 20), _utc(9, 20)),
        )

    db.execute(
        """
        INSERT INTO odds_snapshots (
            match_id,
            source_system,
            bookmaker_code,
            market_type,
            selection_type,
            decimal_price,
            booksum,
            source_record_hash,
            source_observed_at,
            as_of
        )
        VALUES (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.050000, 1.03000000, 'SE_SOURCE_HASH_V1:odds-1', %s, %s)
        """,
        (fixture["match_id"], _utc(9, 25), _utc(9, 25)),
    )

def test_predictions_allow_pre_lock_persistence_without_probability_leakage(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    as_of = _utc(10, 0)
    locked_at = as_of + timedelta(hours=2)

    for probability in (0, 0.5, 1):
        db.execute(
            """
            INSERT INTO predictions (
                match_id,
                agent_code,
                prediction_status,
                selection_side,
                win_probability,
                model_version,
                feature_version,
                variant_id,
                as_of,
                prediction_locked_at,
                created_at
            )
            VALUES (
                %s,
                'AGENT_1_SURFACE_ELO',
                'PREDICTED',
                1,
                %s,
                'm1',
                'f1',
                %s,
                %s,
                %s,
                %s
            )
            """,
            (
                fixture["match_id"],
                probability,
                f"prob-{probability}",
                as_of + timedelta(minutes=int(probability * 10)),
                locked_at + timedelta(minutes=int(probability * 10)),
                as_of + timedelta(minutes=30 + int(probability * 10)),
            ),
        )

    db.execute(
        """
        INSERT INTO predictions (
            match_id,
            agent_code,
            prediction_status,
            model_version,
            feature_version,
            variant_id,
            as_of,
            prediction_locked_at,
            created_at
        )
        VALUES (
            %s,
            'AGENT_2_HIERARCHICAL_MARKOV',
            'ABSTAINED',
            'm1',
            'f1',
            'abstained-ok',
            %s,
            %s,
            %s
        )
        """,
        (fixture["match_id"], as_of, locked_at, as_of + timedelta(minutes=5)),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO predictions (
                match_id,
                agent_code,
                prediction_status,
                selection_side,
                win_probability,
                model_version,
                feature_version,
                variant_id,
                as_of,
                prediction_locked_at,
                created_at
            )
            VALUES (
                %s,
                'AGENT_3_INTRANSITIVITY',
                'PREDICTED',
                1,
                -0.01,
                'm1',
                'f1',
                'negative',
                %s,
                %s,
                %s
            )
            """,
            (fixture["match_id"], as_of, locked_at, as_of + timedelta(minutes=10)),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO predictions (
                match_id,
                agent_code,
                prediction_status,
                selection_side,
                win_probability,
                model_version,
                feature_version,
                variant_id,
                as_of,
                prediction_locked_at,
                created_at
            )
            VALUES (
                %s,
                'AGENT_3_INTRANSITIVITY',
                'PREDICTED',
                1,
                1.01,
                'm1',
                'f1',
                'gt-one',
                %s,
                %s,
                %s
            )
            """,
            (fixture["match_id"], as_of, locked_at, as_of + timedelta(minutes=11)),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO predictions (
                match_id,
                agent_code,
                prediction_status,
                selection_side,
                win_probability,
                model_version,
                feature_version,
                variant_id,
                as_of,
                prediction_locked_at,
                created_at
            )
            VALUES (
                %s,
                'AGENT_3_INTRANSITIVITY',
                'ABSTAINED',
                1,
                0.50,
                'm1',
                'f1',
                'bad-abstained',
                %s,
                %s,
                %s
            )
            """,
            (fixture["match_id"], as_of, locked_at, as_of + timedelta(minutes=12)),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO predictions (
                match_id,
                agent_code,
                prediction_status,
                selection_side,
                win_probability,
                model_version,
                feature_version,
                variant_id,
                as_of,
                prediction_locked_at,
                created_at
            )
            VALUES (
                %s,
                'AGENT_3_INTRANSITIVITY',
                'PREDICTED',
                1,
                0.55,
                'm1',
                'f1',
                'created-before-as-of',
                %s,
                %s,
                %s
            )
            """,
            (fixture["match_id"], as_of, locked_at, as_of - timedelta(seconds=1)),
        )


def test_decisions_enforce_status_reason_and_signal_integrity(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    as_of = _utc(10, 30)
    locked_at = as_of + timedelta(hours=1)

    db.execute(
        """
        INSERT INTO decisions (
            match_id,
            signal_type,
            decision_status,
            reason_code,
            market_type,
            selection_type,
            selection_side,
            consensus_probability,
            consensus_fair_odds,
            market_decimal_price,
            sharp_reference_price,
            price_source_system,
            price_bookmaker_code,
            devig_method,
            devig_fair_odds,
            edge_pct,
            min_acceptable_odds,
            active_agent_count,
            model_version,
            feature_version,
            variant_id,
            as_of,
            prediction_locked_at,
            created_at
        )
        VALUES (
            %s,
            'TYPE_A_MODEL_EDGE',
            'BET',
            'NONE',
            'MATCH_WINNER',
            'PLAYER_1',
            1,
            0.550000,
            1.818182,
            2.100000,
            1.950000,
            'ODDS_API',
            'PINNACLE',
            'power',
            1.910000,
            0.155000,
            1.920000,
            2,
            'm1',
            'f1',
            'bet-ok',
            %s,
            %s,
            %s
        )
        """,
        (fixture["match_id"], as_of, locked_at, as_of + timedelta(minutes=10)),
    )

    db.execute(
        """
        INSERT INTO decisions (
            match_id,
            signal_type,
            decision_status,
            reason_code,
            market_type,
            active_agent_count,
            abstaining_agent_count,
            model_version,
            feature_version,
            variant_id,
            as_of,
            prediction_locked_at,
            created_at
        )
        VALUES (
            %s,
            'TYPE_B_PRICE_DISPERSION',
            'NO_BET',
            'PRICE_FAILURE',
            'MATCH_WINNER',
            2,
            1,
            'm1',
            'f1',
            'no-bet-ok',
            %s,
            %s,
            %s
        )
        """,
        (fixture["match_id"], as_of + timedelta(minutes=1), locked_at + timedelta(minutes=1), as_of + timedelta(minutes=11)),
    )

    db.execute(
        """
        INSERT INTO decisions (
            match_id,
            signal_type,
            decision_status,
            reason_code,
            market_type,
            selection_type,
            selection_side,
            consensus_probability,
            consensus_fair_odds,
            market_decimal_price,
            sharp_reference_price,
            price_source_system,
            price_bookmaker_code,
            devig_method,
            devig_fair_odds,
            edge_pct,
            min_acceptable_odds,
            active_agent_count,
            model_version,
            feature_version,
            variant_id,
            as_of,
            prediction_locked_at,
            created_at
        )
        VALUES (
            %s,
            'TYPE_A_MODEL_EDGE',
            'VETOED',
            'VETO_TRIGGERED',
            'MATCH_WINNER',
            'PLAYER_2',
            2,
            0.510000,
            1.960784,
            2.040000,
            1.980000,
            'ODDS_API',
            'PINNACLE',
            'power',
            1.990000,
            0.040000,
            2.000000,
            2,
            'm1',
            'f1',
            'veto-ok',
            %s,
            %s,
            %s
        )
        """,
        (fixture["match_id"], as_of + timedelta(minutes=2), locked_at + timedelta(minutes=2), as_of + timedelta(minutes=12)),
    )

    db.execute(
        """
        INSERT INTO decisions (
            match_id,
            signal_type,
            decision_status,
            reason_code,
            market_type,
            selection_type,
            selection_side,
            line_value,
            consensus_probability,
            consensus_fair_odds,
            market_decimal_price,
            sharp_reference_price,
            price_source_system,
            price_bookmaker_code,
            devig_method,
            devig_fair_odds,
            edge_pct,
            min_acceptable_odds,
            active_agent_count,
            model_version,
            feature_version,
            variant_id,
            as_of,
            prediction_locked_at,
            created_at
        )
        VALUES (
            %s,
            'TYPE_B_PRICE_DISPERSION',
            'BET',
            'NONE',
            'GAME_HANDICAP',
            'SIDE_1',
            1,
            -2.500,
            0.540000,
            1.851852,
            2.020000,
            1.950000,
            'ODDS_API',
            'PINNACLE',
            'power',
            1.970000,
            0.090000,
            1.930000,
            1,
            'm1',
            'f1',
            'side-1-ok',
            %s,
            %s,
            %s
        )
        """,
        (fixture["match_id"], as_of + timedelta(minutes=3), locked_at + timedelta(minutes=3), as_of + timedelta(minutes=13)),
    )

    db.execute(
        """
        INSERT INTO decisions (
            match_id,
            signal_type,
            decision_status,
            reason_code,
            market_type,
            selection_type,
            selection_side,
            line_value,
            consensus_probability,
            consensus_fair_odds,
            market_decimal_price,
            sharp_reference_price,
            price_source_system,
            price_bookmaker_code,
            devig_method,
            devig_fair_odds,
            edge_pct,
            min_acceptable_odds,
            active_agent_count,
            model_version,
            feature_version,
            variant_id,
            as_of,
            prediction_locked_at,
            created_at
        )
        VALUES (
            %s,
            'TYPE_B_PRICE_DISPERSION',
            'BET',
            'NONE',
            'GAME_HANDICAP',
            'SIDE_2',
            2,
            2.500,
            0.520000,
            1.923077,
            2.040000,
            1.980000,
            'ODDS_API',
            'PINNACLE',
            'power',
            1.990000,
            0.060000,
            1.950000,
            1,
            'm1',
            'f1',
            'side-2-ok',
            %s,
            %s,
            %s
        )
        """,
        (fixture["match_id"], as_of + timedelta(minutes=4), locked_at + timedelta(minutes=4), as_of + timedelta(minutes=14)),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decisions (
                match_id,
                signal_type,
                decision_status,
                reason_code,
                market_type,
                active_agent_count,
                model_version,
                feature_version,
                variant_id,
                as_of,
                prediction_locked_at,
                created_at
            )
            VALUES (
                %s,
                'TYPE_A_MODEL_EDGE',
                'VETOED',
                'PRICE_FAILURE',
                'MATCH_WINNER',
                1,
                'm1',
                'f1',
                'bad-veto',
                %s,
                %s,
                %s
            )
            """,
            (fixture["match_id"], as_of, locked_at, as_of + timedelta(minutes=1)),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decisions (
                match_id,
                signal_type,
                decision_status,
                reason_code,
                market_type,
                selection_type,
                selection_side,
                line_value,
                consensus_probability,
                consensus_fair_odds,
                market_decimal_price,
                sharp_reference_price,
                price_source_system,
                price_bookmaker_code,
                devig_method,
                devig_fair_odds,
                edge_pct,
                min_acceptable_odds,
                active_agent_count,
                model_version,
                feature_version,
                variant_id,
                as_of,
                prediction_locked_at,
                created_at
            )
            VALUES (
                %s,
                'TYPE_B_PRICE_DISPERSION',
                'BET',
                'NONE',
                'GAME_HANDICAP',
                'SIDE_1',
                2,
                -2.500,
                0.540000,
                1.851852,
                2.020000,
                1.950000,
                'ODDS_API',
                'PINNACLE',
                'power',
                1.970000,
                0.090000,
                1.930000,
                1,
                'm1',
                'f1',
                'side-1-bad',
                %s,
                %s,
                %s
            )
            """,
            (fixture["match_id"], as_of + timedelta(minutes=5), locked_at + timedelta(minutes=5), as_of + timedelta(minutes=15)),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decisions (
                match_id,
                signal_type,
                decision_status,
                reason_code,
                market_type,
                selection_type,
                selection_side,
                line_value,
                consensus_probability,
                consensus_fair_odds,
                market_decimal_price,
                sharp_reference_price,
                price_source_system,
                price_bookmaker_code,
                devig_method,
                devig_fair_odds,
                edge_pct,
                min_acceptable_odds,
                active_agent_count,
                model_version,
                feature_version,
                variant_id,
                as_of,
                prediction_locked_at,
                created_at
            )
            VALUES (
                %s,
                'TYPE_B_PRICE_DISPERSION',
                'BET',
                'NONE',
                'GAME_HANDICAP',
                'SIDE_2',
                1,
                2.500,
                0.520000,
                1.923077,
                2.040000,
                1.980000,
                'ODDS_API',
                'PINNACLE',
                'power',
                1.990000,
                0.060000,
                1.950000,
                1,
                'm1',
                'f1',
                'side-2-bad',
                %s,
                %s,
                %s
            )
            """,
            (fixture["match_id"], as_of + timedelta(minutes=6), locked_at + timedelta(minutes=6), as_of + timedelta(minutes=16)),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decisions (
                match_id,
                signal_type,
                decision_status,
                reason_code,
                market_type,
                selection_type,
                selection_side,
                active_agent_count,
                model_version,
                feature_version,
                variant_id,
                as_of,
                prediction_locked_at,
                created_at
            )
            VALUES (
                %s,
                'TYPE_A_MODEL_EDGE',
                'NO_BET',
                'VETO_TRIGGERED',
                'MATCH_WINNER',
                'PLAYER_1',
                1,
                1,
                'm1',
                'f1',
                'bad-no-bet',
                %s,
                %s,
                %s
            )
            """,
            (fixture["match_id"], as_of, locked_at, as_of + timedelta(minutes=1)),
        )


def test_match_version_sources_preserve_append_only_lineage(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)

    db.execute(
        """
        INSERT INTO match_source_ids (
            match_id,
            match_version_id,
            source_system,
            source_match_id,
            source_record_locator,
            source_file_name,
            source_version_hash
        )
        VALUES (%s, %s, 'SACKMANN', 'match-123', '/2026/file.csv#12', '2026.csv', 'source-v1')
        """,
        (fixture["match_id"], fixture["match_version_id"]),
    )

    db.execute(
        """
        INSERT INTO match_version_sources (
            match_version_id,
            source_system,
            source_match_id,
            source_record_locator,
            source_file_name,
            source_version_hash,
            source_observed_at,
            as_of
        )
        VALUES
            (%s, 'SACKMANN', 'match-123', '/2026/file.csv#12', '2026.csv', 'source-v1', %s, %s),
            (%s, 'SACKMANN', 'match-123', '/2026/file.csv#13', '2026.csv', 'source-v2', %s, %s)
        """,
        (
            fixture["match_version_id"],
            _utc(8, 0),
            _utc(8, 0),
            fixture["match_version_id"],
            _utc(8, 5),
            _utc(8, 5),
        ),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO match_version_sources (
                match_version_id,
                source_system,
                source_match_id,
                source_record_locator,
                source_file_name,
                source_version_hash,
                source_observed_at,
                as_of
            )
            VALUES (%s, 'SACKMANN', 'match-123', '/2026/file.csv#13', '2026.csv', 'source-v2', %s, %s)
            """,
            (fixture["match_version_id"], _utc(8, 5), _utc(8, 5)),
        )
