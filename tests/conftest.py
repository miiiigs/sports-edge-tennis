import os
import shutil
import subprocess
import json
import sys
from pathlib import Path

import psycopg
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
MANIFEST_PATH = REPO_ROOT / "db" / "migrations" / "manifest.json"
POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL")

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _run_psql(dsn: str, sql_path: Path) -> None:
    subprocess.run(
        ["psql", "-d", dsn, "-v", "ON_ERROR_STOP=1", "-f", str(sql_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _migration_entries() -> list[dict[str, str]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return manifest["migrations"]


def _run_manifest_migrations(dsn: str, direction: str) -> None:
    entries = _migration_entries()
    if direction == "down":
        entries = list(reversed(entries))
    for entry in entries:
        _run_psql(dsn, REPO_ROOT / Path(entry[direction]))


def _reset_public_schema(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")


def _reset_db(conn: psycopg.Connection) -> None:
    if conn.execute("SELECT to_regclass('public.ingest_runs')").fetchone()[0] is None:
        return
    conn.execute(
        """
        TRUNCATE TABLE
            data_health_checks,
            decision_clv,
            decision_settlements,
            decision_agents,
            decisions,
            predictions,
            decision_policy_versions,
            agent_config_versions,
            prediction_runs,
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
    _reset_public_schema(postgres_dsn)
    _run_manifest_migrations(postgres_dsn, "up")
    try:
        yield postgres_dsn
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            _reset_db(conn)
        _reset_public_schema(postgres_dsn)


@pytest.fixture()
def db(migrated_postgres: str) -> psycopg.Connection:
    with psycopg.connect(migrated_postgres, autocommit=True) as conn:
        _reset_db(conn)
        yield conn
        _reset_db(conn)
