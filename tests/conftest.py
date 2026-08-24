import os
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
UP_MIGRATION_PATH = REPO_ROOT / "db" / "migrations" / "0001_tennis_data_foundation.up.sql"
DOWN_MIGRATION_PATH = REPO_ROOT / "db" / "migrations" / "0001_tennis_data_foundation.down.sql"
POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL")


def _run_psql(dsn: str, sql_path: Path) -> None:
    subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-f", str(sql_path)],
        check=True,
        capture_output=True,
        text=True,
    )


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


@pytest.fixture()
def db(migrated_postgres: str) -> psycopg.Connection:
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        _reset_db(conn)
        yield conn
        _reset_db(conn)
