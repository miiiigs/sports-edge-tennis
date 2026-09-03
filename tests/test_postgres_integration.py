import os
import shutil
import subprocess
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import threading
import time

import psycopg
import pytest

from sports_edge_tennis.data.contracts import IngestCounters, SourceDocument
from sports_edge_tennis.data.pipeline import finish_ingest_run, start_ingest_run
from sports_edge_tennis.data.repository import HistoricalRepository


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "db" / "migrations" / "manifest.json"
POSTGRES_DSN = os.environ.get("TEST_POSTGRES_DSN") or os.environ.get("DATABASE_URL")


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)


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


def _table_exists(conn: psycopg.Connection, table_name: str) -> bool:
    return (
        conn.execute("SELECT to_regclass(%s)", (f"public.{table_name}",)).fetchone()[0]
        is not None
    )


def _column_exists(conn: psycopg.Connection, table_name: str, column_name: str) -> bool:
    return (
        conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                  AND column_name = %s
            )
            """,
            (table_name, column_name),
        ).fetchone()[0]
        is True
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


_TRUNCATE_TABLE_ORDER = (
    "data_health_checks",
    "decision_clv",
    "decision_settlements",
    "decision_agents",
    "decisions",
    "manager_consensus_results",
    "manager_policy_versions",
    "prediction_run_targets",
    "predictions",
    "decision_policy_versions",
    "agent_config_versions",
    "prediction_runs",
    "rating_snapshots",
    "odds_snapshots",
    "match_stat_observations",
    "match_result_observations",
    "match_version_sources",
    "match_source_ids",
    "match_versions",
    "matches",
    "tournament_editions",
    "tournament_source_ids",
    "tournaments",
    "identity_resolution_candidates",
    "identity_resolution_events",
    "player_aliases",
    "player_source_ids",
    "players",
    "ingest_runs",
)


def _reset_db(conn: psycopg.Connection) -> None:
    if conn.execute("SELECT to_regclass('public.ingest_runs')").fetchone()[0] is None:
        return
    existing_tables = [
        table for table in _TRUNCATE_TABLE_ORDER if _table_exists(conn, table)
    ]
    if not existing_tables:
        return
    conn.execute(
        "TRUNCATE TABLE "
        + ", ".join(existing_tables)
        + " RESTART IDENTITY CASCADE"
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


def _insert_prediction_run(
    conn: psycopg.Connection,
    *,
    run_status: str = "COMPLETED",
    as_of: datetime | None = None,
    started_at: datetime | None = None,
    prediction_finalized_at: datetime | None = None,
    ended_at: datetime | None = None,
    experiment_id: str | None = None,
    parent_prediction_run_id: int | None = None,
    parent_relation_type: str | None = None,
    prediction_contract_version: str = "legacy_prediction_ledger_v1",
) -> int:
    run_as_of = as_of or _utc(10, 0)
    run_started_at = started_at or run_as_of
    run_prediction_finalized_at = prediction_finalized_at
    run_ended_at = ended_at

    if run_status != "RUNNING":
        if run_prediction_finalized_at is None:
            run_prediction_finalized_at = run_started_at + timedelta(minutes=30)
        if run_ended_at is None:
            run_ended_at = run_prediction_finalized_at + timedelta(minutes=5)

    if _column_exists(conn, "prediction_runs", "prediction_contract_version"):
        return _fetchval(
            conn,
            """
            INSERT INTO prediction_runs (
                run_status,
                as_of,
                started_at,
                prediction_finalized_at,
                ended_at,
                experiment_id,
                parent_prediction_run_id,
                parent_relation_type,
                prediction_contract_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING prediction_run_id
            """,
            (
                run_status,
                run_as_of,
                run_started_at,
                run_prediction_finalized_at,
                run_ended_at,
                experiment_id,
                parent_prediction_run_id,
                parent_relation_type,
                prediction_contract_version,
            ),
        )

    if prediction_contract_version != "legacy_prediction_ledger_v1":
        pytest.fail(
            "_insert_prediction_run requested a non-legacy prediction contract "
            f"({prediction_contract_version!r}) but prediction_runs lacks the "
            "prediction_contract_version column at this migration state."
        )

    return _fetchval(
        conn,
        """
        INSERT INTO prediction_runs (
            run_status,
            as_of,
            started_at,
            prediction_finalized_at,
            ended_at,
            experiment_id,
            parent_prediction_run_id,
            parent_relation_type
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING prediction_run_id
        """,
        (
            run_status,
            run_as_of,
            run_started_at,
            run_prediction_finalized_at,
            run_ended_at,
            experiment_id,
            parent_prediction_run_id,
            parent_relation_type,
        ),
    )


def _insert_decision_policy_version(
    conn: psycopg.Connection,
    *,
    policy_version: str = "policy-v1",
    policy_key: str = "TENNIS_MANAGER",
    consensus_mode: str = "UNANIMOUS_ACTIVE_AGENTS",
    allow_agent3_abstention: bool = True,
    max_probability_spread: float = 0.12,
    min_active_agent_probability: float = 0.50,
    requires_data_sufficiency: bool = True,
    min_recent_matches_with_serve_stats: int = 12,
    odds_feed_max_staleness_minutes: int = 5,
    match_data_max_staleness_hours: int = 6,
    ratings_max_staleness_hours: int = 24,
    fixture_feed_max_staleness_hours: int = 24,
    atp_main_all_agents_min_edge_pct: float = 0.03,
    atp_main_agent3_abstain_min_edge_pct: float = 0.02,
    challenger_min_edge_pct: float = 0.04,
    default_devig_method: str = "POWER",
    sharp_reference_policy_code: str = "PINNACLE_SHARP",
    veto_policy_code: str = "TENNIS_VETO_V1",
    publication_requires_min_acceptable_odds: bool = True,
    publish_retractions_required: bool = True,
) -> int:
    return _fetchval(
        conn,
        """
        INSERT INTO decision_policy_versions (
            policy_key,
            policy_version,
            consensus_mode,
            allow_agent3_abstention,
            max_probability_spread,
            min_active_agent_probability,
            requires_data_sufficiency,
            min_recent_matches_with_serve_stats,
            odds_feed_max_staleness_minutes,
            match_data_max_staleness_hours,
            ratings_max_staleness_hours,
            fixture_feed_max_staleness_hours,
            atp_main_all_agents_min_edge_pct,
            atp_main_agent3_abstain_min_edge_pct,
            challenger_min_edge_pct,
            default_devig_method,
            sharp_reference_policy_code,
            veto_policy_code,
            publication_requires_min_acceptable_odds,
            publish_retractions_required
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING decision_policy_version_id
        """,
        (
            policy_key,
            policy_version,
            consensus_mode,
            allow_agent3_abstention,
            max_probability_spread,
            min_active_agent_probability,
            requires_data_sufficiency,
            min_recent_matches_with_serve_stats,
            odds_feed_max_staleness_minutes,
            match_data_max_staleness_hours,
            ratings_max_staleness_hours,
            fixture_feed_max_staleness_hours,
            atp_main_all_agents_min_edge_pct,
            atp_main_agent3_abstain_min_edge_pct,
            challenger_min_edge_pct,
            default_devig_method,
            sharp_reference_policy_code,
            veto_policy_code,
            publication_requires_min_acceptable_odds,
            publish_retractions_required,
        ),
    )


def _insert_agent_config_version(
    conn: psycopg.Connection,
    *,
    agent_code: str,
    config_version: str,
    model_version: str = "m1",
    feature_version: str = "f1",
    calibration_version: str | None = None,
    parameter_schema_version: str = "params-v1",
    config_payload: dict | list | str | None = None,
) -> int:
    payload = config_payload if config_payload is not None else {"config_version": config_version}
    return _fetchval(
        conn,
        """
        INSERT INTO agent_config_versions (
            agent_code,
            config_version,
            model_version,
            feature_version,
            calibration_version,
            parameter_schema_version,
            config_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING agent_config_version_id
        """,
        (
            agent_code,
            config_version,
            model_version,
            feature_version,
            calibration_version,
            parameter_schema_version,
            json.dumps(payload),
        ),
    )


def _insert_prediction(
    conn: psycopg.Connection,
    *,
    match_id: int,
    prediction_run_id: int,
    agent_config_version_id: int,
    agent_code: str,
    prediction_status: str = "PREDICTED",
    selection_side: int | None = None,
    win_probability: float | None = None,
    model_version: str = "m1",
    feature_version: str = "f1",
    experiment_id: str | None = None,
    walk_forward_fold: int | None = None,
    variant_id: str | None = "variant-v1",
    as_of: datetime | None = None,
    prediction_locked_at: datetime | None = None,
    created_at: datetime | None = None,
    probability_representation: str | None = None,
) -> int:
    return _insert_prediction_batch(
        conn,
        match_id=match_id,
        prediction_run_id=prediction_run_id,
        rows=[
            {
                "agent_config_version_id": agent_config_version_id,
                "agent_code": agent_code,
                "prediction_status": prediction_status,
                "selection_side": selection_side,
                "win_probability": win_probability,
                "model_version": model_version,
                "feature_version": feature_version,
                "experiment_id": experiment_id,
                "walk_forward_fold": walk_forward_fold,
                "variant_id": variant_id,
                "as_of": as_of,
                "prediction_locked_at": prediction_locked_at,
                "created_at": created_at,
                "probability_representation": probability_representation,
            }
        ],
    )[0]


def _insert_prediction_batch(
    conn: psycopg.Connection,
    *,
    match_id: int,
    prediction_run_id: int,
    rows: list[dict[str, object]],
    terminal_status: str = "COMPLETED",
    ended_at: datetime | None = None,
) -> list[int]:
    prepared_rows: list[tuple] = []
    locked_at_values: set[datetime] = set()

    for row in rows:
        row_as_of = row.get("as_of") or _utc(10, 0)
        assert isinstance(row_as_of, datetime)
        row_prediction_locked_at = row.get("prediction_locked_at") or (row_as_of + timedelta(hours=1))
        assert isinstance(row_prediction_locked_at, datetime)
        row_created_at = row.get("created_at") or (row_as_of + timedelta(minutes=10))
        assert isinstance(row_created_at, datetime)

        prediction_status = row.get("prediction_status", "PREDICTED")
        selection_side = row.get("selection_side")
        win_probability = row.get("win_probability")
        if prediction_status == "PREDICTED":
            selection_side = 1 if selection_side is None else selection_side
            win_probability = 0.55 if win_probability is None else win_probability
        else:
            selection_side = None
            win_probability = None
        probability_representation = row.get("probability_representation")

        locked_at_values.add(row_prediction_locked_at)
        prepared_rows.append(
            (
                match_id,
                prediction_run_id,
                row["agent_config_version_id"],
                row["agent_code"],
                prediction_status,
                selection_side,
                win_probability,
                row.get("model_version", "m1"),
                row.get("feature_version", "f1"),
                row.get("experiment_id"),
                row.get("walk_forward_fold"),
                row.get("variant_id", "variant-v1"),
                row_as_of,
                row_prediction_locked_at,
                row_created_at,
                probability_representation,
            )
        )

    if len(locked_at_values) != 1:
        raise ValueError("prediction batch must share one prediction_locked_at value")

    final_locked_at = next(iter(locked_at_values))
    terminal_ended_at = ended_at or (final_locked_at + timedelta(minutes=5))
    inserted_ids: list[int] = []

    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout = '2s'")
        conn.execute("SET LOCAL statement_timeout = '5s'")
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        conn.execute(
            """
            SELECT prediction_run_id
            FROM prediction_runs
            WHERE prediction_run_id = %s
            FOR UPDATE
            """,
            (prediction_run_id,),
        ).fetchone()

        for params in prepared_rows:
            inserted_ids.append(
                _fetchval(
                    conn,
                    """
                    INSERT INTO predictions (
                        match_id,
                        prediction_run_id,
                        agent_config_version_id,
                        agent_code,
                        prediction_status,
                        selection_side,
                        win_probability,
                        model_version,
                        feature_version,
                        experiment_id,
                        walk_forward_fold,
                        variant_id,
                        as_of,
                        prediction_locked_at,
                        created_at,
                        probability_representation
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    RETURNING prediction_id
                    """,
                    params,
                )
            )

        conn.execute(
            """
            UPDATE prediction_runs
            SET run_status = %s,
                prediction_finalized_at = %s,
                ended_at = %s
            WHERE prediction_run_id = %s
            """,
            (terminal_status, final_locked_at, terminal_ended_at, prediction_run_id),
        )

    return inserted_ids


def _make_source_document() -> SourceDocument:
    return SourceDocument(
        source_system="SACKMANN",
        source_name="Sample source",
        source_url=None,
        canonical_source=None,
        retrieval_source=None,
        archive_revision=None,
        upstream_revision=None,
        source_license=None,
        local_path=REPO_ROOT / "README.md",
        retrieved_at=_utc(6, 0),
        filename="sample.csv",
        sha256="a" * 64,
        size_bytes=123,
        dataset_type="matches",
        season=2026,
        source_revision="rev-1",
    )


def _insert_decision(
    conn: psycopg.Connection,
    *,
    match_id: int,
    prediction_run_id: int,
    decision_policy_version_id: int,
    signal_type: str = "TYPE_A_MODEL_EDGE",
    decision_status: str = "BET",
    reason_code: str = "NONE",
    market_type: str = "MATCH_WINNER",
    selection_type: str | None = "PLAYER_1",
    selection_side: int | None = 1,
    line_value: float | None = None,
    consensus_probability: float | None = 0.55,
    consensus_fair_odds: float | None = 1.818182,
    market_decimal_price: float | None = 2.10,
    sharp_reference_price: float | None = 1.95,
    price_source_system: str | None = "ODDS_API",
    price_bookmaker_code: str | None = "PINNACLE",
    devig_method: str | None = "power",
    devig_fair_odds: float | None = 1.91,
    edge_pct: float | None = 0.155,
    min_acceptable_odds: float | None = 1.92,
    active_agent_count: int = 2,
    abstaining_agent_count: int = 0,
    directional_disagreement: bool = False,
    probability_spread: float | None = None,
    model_version: str = "m1",
    feature_version: str = "f1",
    experiment_id: str | None = None,
    walk_forward_fold: int | None = None,
    variant_id: str | None = "decision-v1",
    as_of: datetime | None = None,
    prediction_locked_at: datetime | None = None,
    created_at: datetime | None = None,
    published_at: datetime | None = None,
) -> int:
    row_as_of = as_of or _utc(10, 0)
    row_prediction_locked_at = prediction_locked_at or (row_as_of + timedelta(hours=1))
    row_created_at = created_at or (row_as_of + timedelta(minutes=10))
    return _fetchval(
        conn,
        """
        INSERT INTO decisions (
            match_id,
            prediction_run_id,
            decision_policy_version_id,
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
            abstaining_agent_count,
            directional_disagreement,
            probability_spread,
            model_version,
            feature_version,
            experiment_id,
            walk_forward_fold,
            variant_id,
            as_of,
            prediction_locked_at,
            created_at,
            published_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING decision_id
        """,
        (
            match_id,
            prediction_run_id,
            decision_policy_version_id,
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
            abstaining_agent_count,
            directional_disagreement,
            probability_spread,
            model_version,
            feature_version,
            experiment_id,
            walk_forward_fold,
            variant_id,
            row_as_of,
            row_prediction_locked_at,
            row_created_at,
            published_at,
        ),
    )


def _insert_prediction_run_target(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    match_id: int,
) -> None:
    conn.execute(
        """
        INSERT INTO prediction_run_targets (prediction_run_id, match_id)
        VALUES (%s, %s)
        """,
        (prediction_run_id, match_id),
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


def test_postgres_migration_bootstraps_expected_tables(db: psycopg.Connection):
    for table_name in (
        "players",
        "identity_resolution_candidates",
        "match_version_sources",
        "odds_snapshots",
        "rating_snapshots",
        "prediction_runs",
        "decision_policy_versions",
        "agent_config_versions",
        "predictions",
        "decisions",
    ):
        assert _table_exists(db, table_name)


def test_forward_migration_lifecycle_reapplies_cleanly(postgres_dsn: str):
    entries = _migration_entries()
    up_0001 = REPO_ROOT / Path(entries[0]["up"])
    up_0002 = REPO_ROOT / Path(entries[1]["up"])
    up_0003 = REPO_ROOT / Path(entries[2]["up"])
    up_0004 = REPO_ROOT / Path(entries[3]["up"])
    down_0004 = REPO_ROOT / Path(entries[3]["down"])
    try:
        _reset_public_schema(postgres_dsn)
        _run_psql(postgres_dsn, up_0001)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            enum_labels = conn.execute(
                """
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
                WHERE typname = 'competition_tier'
                ORDER BY enumsortorder
                """
            ).fetchall()
            assert [label for (label,) in enum_labels] == [
                "GRAND_SLAM",
                "MASTERS_1000",
                "ATP_500",
                "ATP_250",
                "CHALLENGER",
            ]

        _run_psql(postgres_dsn, up_0002)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            enum_labels = conn.execute(
                """
                SELECT enumlabel
                FROM pg_enum
                JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
                WHERE typname = 'competition_tier'
                ORDER BY enumsortorder
                """
            ).fetchall()
            assert [label for (label,) in enum_labels] == [
                "GRAND_SLAM",
                "MASTERS_1000",
                "ATP_TOUR",
                "ATP_500",
                "ATP_250",
                "CHALLENGER",
            ]
            is_match_start_required = conn.execute(
                """
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'match_versions'
                  AND column_name = 'match_start_at'
                """
            ).fetchone()[0]
            assert is_match_start_required == "NO"

        _run_psql(postgres_dsn, up_0003)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            is_match_start_required = conn.execute(
                """
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'match_versions'
                  AND column_name = 'match_start_at'
                """
            ).fetchone()[0]
            assert is_match_start_required == "YES"

        _run_psql(postgres_dsn, up_0004)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert _table_exists(conn, "prediction_runs")
            assert _table_exists(conn, "decision_policy_versions")
            assert _table_exists(conn, "agent_config_versions")
            assert _column_exists(conn, "predictions", "prediction_run_id")
            assert _column_exists(conn, "predictions", "agent_config_version_id")
            assert _column_exists(conn, "decisions", "prediction_run_id")
            assert _column_exists(conn, "decisions", "decision_policy_version_id")
            assert _column_exists(conn, "decision_agents", "prediction_run_id")
            assert _column_exists(conn, "decision_agents", "match_id")
            assert _column_exists(conn, "decision_agents", "prediction_id")

        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            _reset_db(conn)
        _run_psql(postgres_dsn, down_0004)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert not _table_exists(conn, "prediction_runs")
            assert not _table_exists(conn, "decision_policy_versions")
            assert not _table_exists(conn, "agent_config_versions")
            assert not _column_exists(conn, "predictions", "prediction_run_id")
            assert not _column_exists(conn, "predictions", "agent_config_version_id")
            assert not _column_exists(conn, "decisions", "prediction_run_id")
            assert not _column_exists(conn, "decisions", "decision_policy_version_id")
            assert not _column_exists(conn, "decision_agents", "prediction_run_id")
            assert not _column_exists(conn, "decision_agents", "match_id")
            assert not _column_exists(conn, "decision_agents", "prediction_id")
            assert _table_exists(conn, "players")
            assert _table_exists(conn, "match_versions")

        _run_psql(postgres_dsn, up_0004)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert _table_exists(conn, "prediction_runs")
            assert _table_exists(conn, "decision_policy_versions")
            assert _table_exists(conn, "agent_config_versions")
    finally:
        _reset_public_schema(postgres_dsn)
        _run_manifest_migrations(postgres_dsn, "up")


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
    agent_1_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="agent-1-config-v1",
    )
    agent_2_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_2_HIERARCHICAL_MARKOV",
        config_version="agent-2-config-v1",
    )
    agent_3_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_3_INTRANSITIVITY",
        config_version="agent-3-config-v1",
    )

    for probability in (0, 0.5, 1):
        row_as_of = as_of + timedelta(minutes=int(probability * 10))
        row_locked_at = locked_at + timedelta(minutes=int(probability * 10))
        prediction_run_id = _insert_prediction_run(
            db,
            run_status="RUNNING",
            as_of=row_as_of,
            started_at=row_as_of,
        )
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=prediction_run_id,
            agent_config_version_id=agent_1_config_id,
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=probability,
            variant_id=f"prob-{probability}",
            as_of=row_as_of,
            prediction_locked_at=row_locked_at,
            created_at=row_as_of + timedelta(minutes=30),
        )

    abstained_run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=as_of,
        started_at=as_of,
    )
    _insert_prediction(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=abstained_run_id,
        agent_config_version_id=agent_2_config_id,
        agent_code="AGENT_2_HIERARCHICAL_MARKOV",
        prediction_status="ABSTAINED",
        variant_id="abstained-ok",
        as_of=as_of,
        prediction_locked_at=locked_at,
        created_at=as_of + timedelta(minutes=5),
    )

    with pytest.raises(psycopg.Error):
        negative_run_id = _insert_prediction_run(
            db,
            run_status="RUNNING",
            as_of=as_of,
            started_at=as_of,
        )
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=negative_run_id,
            agent_config_version_id=agent_3_config_id,
            agent_code="AGENT_3_INTRANSITIVITY",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=-0.01,
            variant_id="negative",
            as_of=as_of,
            prediction_locked_at=locked_at,
            created_at=as_of + timedelta(minutes=10),
        )

    with pytest.raises(psycopg.Error):
        gt_one_as_of = as_of + timedelta(minutes=1)
        gt_one_locked_at = locked_at + timedelta(minutes=1)
        gt_one_run_id = _insert_prediction_run(
            db,
            run_status="RUNNING",
            as_of=gt_one_as_of,
            started_at=gt_one_as_of,
        )
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=gt_one_run_id,
            agent_config_version_id=agent_3_config_id,
            agent_code="AGENT_3_INTRANSITIVITY",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=1.01,
            variant_id="gt-one",
            as_of=gt_one_as_of,
            prediction_locked_at=gt_one_locked_at,
            created_at=as_of + timedelta(minutes=11),
        )

    with pytest.raises(psycopg.Error):
        invalid_abstained_as_of = as_of + timedelta(minutes=2)
        invalid_abstained_locked_at = locked_at + timedelta(minutes=2)
        invalid_abstained_run_id = _insert_prediction_run(
            db,
            run_status="COMPLETED",
            as_of=invalid_abstained_as_of,
            started_at=invalid_abstained_as_of,
            prediction_finalized_at=invalid_abstained_locked_at,
            ended_at=invalid_abstained_locked_at + timedelta(minutes=5),
        )
        db.execute(
            """
            INSERT INTO predictions (
                match_id,
                prediction_run_id,
                agent_config_version_id,
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
            VALUES (%s, %s, %s, 'AGENT_3_INTRANSITIVITY', 'ABSTAINED', 1, 0.50, 'm1', 'f1', 'bad-abstained', %s, %s, %s)
            """,
            (
                fixture["match_id"],
                invalid_abstained_run_id,
                agent_3_config_id,
                invalid_abstained_as_of,
                invalid_abstained_locked_at,
                as_of + timedelta(minutes=12),
            ),
        )

    with pytest.raises(psycopg.Error):
        created_before_as_of = as_of + timedelta(minutes=3)
        created_before_locked_at = locked_at + timedelta(minutes=3)
        created_before_run_id = _insert_prediction_run(
            db,
            run_status="RUNNING",
            as_of=created_before_as_of,
            started_at=created_before_as_of,
        )
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=created_before_run_id,
            agent_config_version_id=agent_3_config_id,
            agent_code="AGENT_3_INTRANSITIVITY",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=0.55,
            variant_id="created-before-as-of",
            as_of=created_before_as_of,
            prediction_locked_at=created_before_locked_at,
            created_at=as_of - timedelta(seconds=1),
        )


def test_decisions_enforce_status_reason_and_signal_integrity(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    as_of = _utc(10, 30)
    locked_at = as_of + timedelta(hours=1)
    prediction_run_id = _insert_prediction_run(
        db,
        as_of=as_of,
        started_at=as_of,
        prediction_finalized_at=locked_at,
        ended_at=locked_at + timedelta(minutes=5),
    )
    decision_policy_version_id = _insert_decision_policy_version(
        db,
        policy_version="decision-constraints-v1",
    )

    def _insert_vetoed_selection_case(
        *,
        selection_type: str,
        selection_side: int | None,
        market_type: str,
        line_value: float | None,
        variant_id: str,
        minute_offset: int,
        signal_type: str = "TYPE_A_MODEL_EDGE",
    ) -> None:
        decision_as_of = as_of + timedelta(minutes=minute_offset)
        decision_created_at = as_of + timedelta(minutes=minute_offset + 10)
        _insert_decision(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=prediction_run_id,
            decision_policy_version_id=decision_policy_version_id,
            signal_type=signal_type,
            decision_status="VETOED",
            reason_code="VETO_TRIGGERED",
            market_type=market_type,
            selection_type=selection_type,
            selection_side=selection_side,
            line_value=line_value,
            consensus_probability=0.510000,
            consensus_fair_odds=1.960784,
            market_decimal_price=2.040000,
            sharp_reference_price=1.980000,
            price_source_system="ODDS_API",
            price_bookmaker_code="PINNACLE",
            devig_method="power",
            devig_fair_odds=1.990000,
            edge_pct=0.040000,
            min_acceptable_odds=2.000000,
            active_agent_count=2,
            variant_id=variant_id,
            as_of=decision_as_of,
            prediction_locked_at=locked_at,
            created_at=decision_created_at,
        )

    _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=prediction_run_id,
        decision_policy_version_id=decision_policy_version_id,
        signal_type="TYPE_A_MODEL_EDGE",
        decision_status="BET",
        reason_code="NONE",
        market_type="MATCH_WINNER",
        selection_type="PLAYER_1",
        selection_side=1,
        consensus_probability=0.550000,
        consensus_fair_odds=1.818182,
        market_decimal_price=2.100000,
        sharp_reference_price=1.950000,
        price_source_system="ODDS_API",
        price_bookmaker_code="PINNACLE",
        devig_method="power",
        devig_fair_odds=1.910000,
        edge_pct=0.155000,
        min_acceptable_odds=1.920000,
        active_agent_count=2,
        variant_id="bet-ok",
        as_of=as_of,
        prediction_locked_at=locked_at,
        created_at=as_of + timedelta(minutes=10),
    )

    _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=prediction_run_id,
        decision_policy_version_id=decision_policy_version_id,
        signal_type="TYPE_B_PRICE_DISPERSION",
        decision_status="NO_BET",
        reason_code="PRICE_FAILURE",
        market_type="MATCH_WINNER",
        selection_type=None,
        selection_side=None,
        line_value=None,
        consensus_probability=None,
        consensus_fair_odds=None,
        market_decimal_price=None,
        sharp_reference_price=None,
        price_source_system=None,
        price_bookmaker_code=None,
        devig_method=None,
        devig_fair_odds=None,
        edge_pct=None,
        min_acceptable_odds=None,
        active_agent_count=2,
        abstaining_agent_count=1,
        variant_id="no-bet-ok",
        as_of=as_of + timedelta(minutes=1),
        prediction_locked_at=locked_at,
        created_at=as_of + timedelta(minutes=11),
    )

    _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=prediction_run_id,
        decision_policy_version_id=decision_policy_version_id,
        signal_type="TYPE_A_MODEL_EDGE",
        decision_status="VETOED",
        reason_code="VETO_TRIGGERED",
        market_type="MATCH_WINNER",
        selection_type="PLAYER_2",
        selection_side=2,
        consensus_probability=0.510000,
        consensus_fair_odds=1.960784,
        market_decimal_price=2.040000,
        sharp_reference_price=1.980000,
        price_source_system="ODDS_API",
        price_bookmaker_code="PINNACLE",
        devig_method="power",
        devig_fair_odds=1.990000,
        edge_pct=0.040000,
        min_acceptable_odds=2.000000,
        active_agent_count=2,
        variant_id="veto-ok",
        as_of=as_of + timedelta(minutes=2),
        prediction_locked_at=locked_at,
        created_at=as_of + timedelta(minutes=12),
    )

    _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=prediction_run_id,
        decision_policy_version_id=decision_policy_version_id,
        signal_type="TYPE_B_PRICE_DISPERSION",
        decision_status="BET",
        reason_code="NONE",
        market_type="GAME_HANDICAP",
        selection_type="SIDE_1",
        selection_side=1,
        line_value=-2.500,
        consensus_probability=0.540000,
        consensus_fair_odds=1.851852,
        market_decimal_price=2.020000,
        sharp_reference_price=1.950000,
        price_source_system="ODDS_API",
        price_bookmaker_code="PINNACLE",
        devig_method="power",
        devig_fair_odds=1.970000,
        edge_pct=0.090000,
        min_acceptable_odds=1.930000,
        active_agent_count=1,
        variant_id="side-1-ok",
        as_of=as_of + timedelta(minutes=3),
        prediction_locked_at=locked_at,
        created_at=as_of + timedelta(minutes=13),
    )

    _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=prediction_run_id,
        decision_policy_version_id=decision_policy_version_id,
        signal_type="TYPE_B_PRICE_DISPERSION",
        decision_status="BET",
        reason_code="NONE",
        market_type="GAME_HANDICAP",
        selection_type="SIDE_2",
        selection_side=2,
        line_value=2.500,
        consensus_probability=0.520000,
        consensus_fair_odds=1.923077,
        market_decimal_price=2.040000,
        sharp_reference_price=1.980000,
        price_source_system="ODDS_API",
        price_bookmaker_code="PINNACLE",
        devig_method="power",
        devig_fair_odds=1.990000,
        edge_pct=0.060000,
        min_acceptable_odds=1.950000,
        active_agent_count=1,
        variant_id="side-2-ok",
        as_of=as_of + timedelta(minutes=4),
        prediction_locked_at=locked_at,
        created_at=as_of + timedelta(minutes=14),
    )

    with pytest.raises(psycopg.Error):
        _insert_decision(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=prediction_run_id,
            decision_policy_version_id=decision_policy_version_id,
            signal_type="TYPE_A_MODEL_EDGE",
            decision_status="VETOED",
            reason_code="PRICE_FAILURE",
            market_type="MATCH_WINNER",
            selection_type=None,
            selection_side=None,
            consensus_probability=None,
            consensus_fair_odds=None,
            market_decimal_price=None,
            sharp_reference_price=None,
            price_source_system=None,
            price_bookmaker_code=None,
            devig_method=None,
            devig_fair_odds=None,
            edge_pct=None,
            min_acceptable_odds=None,
            active_agent_count=1,
            variant_id="bad-veto",
            as_of=as_of,
            prediction_locked_at=locked_at,
            created_at=as_of + timedelta(minutes=1),
        )

    with pytest.raises(psycopg.Error):
        _insert_decision(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=prediction_run_id,
            decision_policy_version_id=decision_policy_version_id,
            signal_type="TYPE_B_PRICE_DISPERSION",
            decision_status="BET",
            reason_code="NONE",
            market_type="GAME_HANDICAP",
            selection_type="SIDE_1",
            selection_side=2,
            line_value=-2.500,
            consensus_probability=0.540000,
            consensus_fair_odds=1.851852,
            market_decimal_price=2.020000,
            sharp_reference_price=1.950000,
            price_source_system="ODDS_API",
            price_bookmaker_code="PINNACLE",
            devig_method="power",
            devig_fair_odds=1.970000,
            edge_pct=0.090000,
            min_acceptable_odds=1.930000,
            active_agent_count=1,
            variant_id="side-1-bad",
            as_of=as_of + timedelta(minutes=5),
            prediction_locked_at=locked_at,
            created_at=as_of + timedelta(minutes=15),
        )

    with pytest.raises(psycopg.Error):
        _insert_decision(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=prediction_run_id,
            decision_policy_version_id=decision_policy_version_id,
            signal_type="TYPE_B_PRICE_DISPERSION",
            decision_status="BET",
            reason_code="NONE",
            market_type="GAME_HANDICAP",
            selection_type="SIDE_2",
            selection_side=1,
            line_value=2.500,
            consensus_probability=0.520000,
            consensus_fair_odds=1.923077,
            market_decimal_price=2.040000,
            sharp_reference_price=1.980000,
            price_source_system="ODDS_API",
            price_bookmaker_code="PINNACLE",
            devig_method="power",
            devig_fair_odds=1.990000,
            edge_pct=0.060000,
            min_acceptable_odds=1.950000,
            active_agent_count=1,
            variant_id="side-2-bad",
            as_of=as_of + timedelta(minutes=6),
            prediction_locked_at=locked_at,
            created_at=as_of + timedelta(minutes=16),
        )

    with pytest.raises(psycopg.Error):
        _insert_vetoed_selection_case(
            selection_type="PLAYER_1",
            selection_side=2,
            market_type="MATCH_WINNER",
            line_value=None,
            variant_id="player-1-bad",
            minute_offset=7,
        )

    with pytest.raises(psycopg.Error):
        _insert_vetoed_selection_case(
            selection_type="PLAYER_2",
            selection_side=1,
            market_type="MATCH_WINNER",
            line_value=None,
            variant_id="player-2-bad",
            minute_offset=8,
        )

    with pytest.raises(psycopg.Error):
        _insert_vetoed_selection_case(
            selection_type="PLAYER_1",
            selection_side=None,
            market_type="MATCH_WINNER",
            line_value=None,
            variant_id="player-1-null",
            minute_offset=9,
        )

    with pytest.raises(psycopg.Error):
        _insert_vetoed_selection_case(
            selection_type="PLAYER_2",
            selection_side=None,
            market_type="MATCH_WINNER",
            line_value=None,
            variant_id="player-2-null",
            minute_offset=10,
        )

    with pytest.raises(psycopg.Error):
        _insert_vetoed_selection_case(
            selection_type="SIDE_1",
            selection_side=None,
            market_type="GAME_HANDICAP",
            line_value=-2.500,
            variant_id="side-1-null",
            minute_offset=11,
            signal_type="TYPE_B_PRICE_DISPERSION",
        )

    with pytest.raises(psycopg.Error):
        _insert_vetoed_selection_case(
            selection_type="SIDE_2",
            selection_side=None,
            market_type="GAME_HANDICAP",
            line_value=2.500,
            variant_id="side-2-null",
            minute_offset=12,
            signal_type="TYPE_B_PRICE_DISPERSION",
        )

    with pytest.raises(psycopg.Error):
        _insert_decision(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=prediction_run_id,
            decision_policy_version_id=decision_policy_version_id,
            signal_type="TYPE_A_MODEL_EDGE",
            decision_status="NO_BET",
            reason_code="VETO_TRIGGERED",
            market_type="MATCH_WINNER",
            selection_type="PLAYER_1",
            selection_side=1,
            consensus_probability=None,
            consensus_fair_odds=None,
            market_decimal_price=None,
            sharp_reference_price=None,
            price_source_system=None,
            price_bookmaker_code=None,
            devig_method=None,
            devig_fair_odds=None,
            edge_pct=None,
            min_acceptable_odds=None,
            active_agent_count=1,
            variant_id="bad-no-bet",
            as_of=as_of,
            prediction_locked_at=locked_at,
            created_at=as_of + timedelta(minutes=1),
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


def test_0004_legacy_fail_fast_rolls_back_transactionally(postgres_dsn: str):
    entries = _migration_entries()
    up_0004 = REPO_ROOT / Path(entries[3]["up"])

    try:
        _reset_public_schema(postgres_dsn)
        for entry in entries[:3]:
            _run_psql(postgres_dsn, REPO_ROOT / Path(entry["up"]))

        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            fixture = _insert_match_fixture(conn)
            as_of = _utc(10, 0)
            locked_at = as_of + timedelta(hours=1)
            _fetchval(
                conn,
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
                    0.55,
                    'm1',
                    'f1',
                    'legacy-prediction',
                    %s,
                    %s,
                    %s
                )
                RETURNING prediction_id
                """,
                (fixture["match_id"], as_of, locked_at, as_of + timedelta(minutes=10)),
            )
            decision_id = _fetchval(
                conn,
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
                    0.55,
                    1.818182,
                    2.10,
                    1.95,
                    'ODDS_API',
                    'PINNACLE',
                    'power',
                    1.91,
                    0.15,
                    1.92,
                    1,
                    'm1',
                    'f1',
                    'legacy-decision',
                    %s,
                    %s,
                    %s
                )
                RETURNING decision_id
                """,
                (fixture["match_id"], as_of, locked_at, as_of + timedelta(minutes=20)),
            )
            conn.execute(
                """
                INSERT INTO decision_agents (
                    decision_id,
                    agent_code,
                    participation_status,
                    proposed_selection_side,
                    proposed_probability,
                    created_at
                )
                VALUES (%s, 'AGENT_1_SURFACE_ELO', 'ACTIVE', 1, 0.55, %s)
                """,
                (decision_id, as_of + timedelta(minutes=20)),
            )

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            _run_psql(postgres_dsn, up_0004)
        combined_output = (exc_info.value.stdout or "") + (exc_info.value.stderr or "")
        assert "cannot be reconstructed safely" in combined_output

        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert conn.execute("SELECT count(*) FROM predictions").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM decision_agents").fetchone()[0] == 1
            assert not _table_exists(conn, "prediction_runs")
            assert not _table_exists(conn, "decision_policy_versions")
            assert not _table_exists(conn, "agent_config_versions")
            assert not _column_exists(conn, "predictions", "prediction_run_id")
            assert not _column_exists(conn, "decisions", "decision_policy_version_id")
            assert not _column_exists(conn, "decision_agents", "prediction_id")
    finally:
        _reset_public_schema(postgres_dsn)
        _run_manifest_migrations(postgres_dsn, "up")


def test_prediction_runs_enforce_lifecycle_and_origin_constraints(db: psycopg.Connection):
    running_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(9, 0),
        started_at=_utc(9, 0),
        prediction_finalized_at=_utc(9, 30),
        ended_at=None,
    )
    completed_id = _insert_prediction_run(
        db,
        run_status="COMPLETED",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
        prediction_finalized_at=_utc(10, 20),
        ended_at=_utc(10, 25),
    )
    failed_id = _insert_prediction_run(
        db,
        run_status="FAILED",
        as_of=_utc(11, 0),
        started_at=_utc(11, 0),
        prediction_finalized_at=_utc(11, 15),
        ended_at=_utc(11, 16),
    )
    cancelled_id = _insert_prediction_run(
        db,
        run_status="CANCELLED",
        as_of=_utc(12, 0),
        started_at=_utc(12, 0),
        prediction_finalized_at=_utc(12, 10),
        ended_at=_utc(12, 12),
    )
    retry_child_id = _insert_prediction_run(
        db,
        run_status="COMPLETED",
        as_of=_utc(13, 0),
        started_at=_utc(13, 0),
        prediction_finalized_at=_utc(13, 15),
        ended_at=_utc(13, 16),
        parent_prediction_run_id=completed_id,
        parent_relation_type="RETRY",
    )
    recalculation_child_id = _insert_prediction_run(
        db,
        run_status="COMPLETED",
        as_of=_utc(14, 0),
        started_at=_utc(14, 0),
        prediction_finalized_at=_utc(14, 15),
        ended_at=_utc(14, 16),
        parent_prediction_run_id=failed_id,
        parent_relation_type="RECALCULATION",
    )

    assert running_id > 0
    assert completed_id > 0
    assert failed_id > 0
    assert cancelled_id > 0
    assert retry_child_id > 0
    assert recalculation_child_id > 0
    assert db.execute(
        "SELECT count(*) FROM predictions WHERE prediction_run_id = %s",
        (completed_id,),
    ).fetchone()[0] == 0

    with pytest.raises(psycopg.Error):
        _insert_prediction_run(
            db,
            run_status="RUNNING",
            as_of=_utc(15, 0),
            started_at=_utc(15, 0),
            prediction_finalized_at=_utc(15, 10),
            ended_at=_utc(15, 11),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO prediction_runs (
                run_status,
                as_of,
                started_at,
                prediction_finalized_at,
                ended_at
            )
            VALUES ('COMPLETED', %s, %s, %s, NULL)
            """,
            (_utc(15, 30), _utc(15, 30), _utc(15, 45)),
        )

    with pytest.raises(psycopg.Error):
        _insert_prediction_run(
            db,
            run_status="COMPLETED",
            as_of=_utc(16, 0),
            started_at=_utc(16, 10),
            prediction_finalized_at=_utc(16, 5),
            ended_at=_utc(16, 11),
        )

    with pytest.raises(psycopg.Error):
        _insert_prediction_run(
            db,
            run_status="COMPLETED",
            as_of=_utc(16, 30),
            started_at=_utc(16, 30),
            prediction_finalized_at=_utc(16, 45),
            ended_at=_utc(16, 40),
        )

    with pytest.raises(psycopg.Error):
        _insert_prediction_run(
            db,
            run_status="COMPLETED",
            as_of=_utc(17, 0),
            started_at=_utc(17, 0),
            prediction_finalized_at=_utc(17, 10),
            ended_at=_utc(17, 20),
            parent_prediction_run_id=completed_id,
            parent_relation_type="SUPERSEDES",
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE prediction_runs
            SET parent_prediction_run_id = prediction_run_id,
                parent_relation_type = 'RETRY'
            WHERE prediction_run_id = %s
            """,
            (completed_id,),
        )


def test_decision_policy_versions_enforce_tennis_constraints(db: psycopg.Connection):
    valid_id = _insert_decision_policy_version(db, policy_version="policy-v1")
    assert valid_id > 0

    with pytest.raises(psycopg.Error):
        _insert_decision_policy_version(db, policy_version="policy-v1")

    with pytest.raises(psycopg.Error):
        _insert_decision_policy_version(
            db,
            policy_version="non-unanimous",
            consensus_mode="MAJORITY",
        )

    with pytest.raises(psycopg.Error):
        _insert_decision_policy_version(
            db,
            policy_version="no-agent3-abstention",
            allow_agent3_abstention=False,
        )

    with pytest.raises(psycopg.Error):
        _insert_decision_policy_version(
            db,
            policy_version="bad-spread",
            max_probability_spread=1.1,
        )

    with pytest.raises(psycopg.Error):
        _insert_decision_policy_version(
            db,
            policy_version="negative-threshold",
            min_recent_matches_with_serve_stats=-1,
        )

    with pytest.raises(psycopg.Error):
        _insert_decision_policy_version(
            db,
            policy_version="negative-edge",
            challenger_min_edge_pct=-0.01,
        )

    with pytest.raises(psycopg.Error):
        _insert_decision_policy_version(
            db,
            policy_version="bad-devig",
            default_devig_method="AVERAGE",
        )


def test_agent_config_versions_enforce_identity_and_payload_constraints(db: psycopg.Connection):
    valid_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="agent-1-config-v1",
        calibration_version=None,
        config_payload={"surface": "HARD"},
    )
    assert valid_id > 0

    with pytest.raises(psycopg.Error):
        _insert_agent_config_version(
            db,
            agent_code="AGENT_1_SURFACE_ELO",
            config_version="agent-1-config-v1",
        )

    with pytest.raises(psycopg.Error):
        _insert_agent_config_version(
            db,
            agent_code="AGENT_2_HIERARCHICAL_MARKOV",
            config_version="non-object-json",
            config_payload=["bad"],
        )

    with pytest.raises(psycopg.Error):
        _insert_agent_config_version(
            db,
            agent_code="AGENT_2_HIERARCHICAL_MARKOV",
            config_version="blank-config-version",
            model_version="",
        )

    with pytest.raises(psycopg.Error):
        _insert_agent_config_version(
            db,
            agent_code="AGENT_3_INTRANSITIVITY",
            config_version="blank-feature-version",
            feature_version="",
        )

    with pytest.raises(psycopg.Error):
        _insert_agent_config_version(
            db,
            agent_code="AGENT_3_INTRANSITIVITY",
            config_version="blank-parameter-schema",
            parameter_schema_version="",
        )


def test_predictions_enforce_agent_config_alignment(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    as_of = _utc(10, 0)
    locked_at = _utc(11, 0)
    prediction_run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=as_of,
        started_at=as_of,
    )
    correct_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="agent-1-correct",
    )
    wrong_agent_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_2_HIERARCHICAL_MARKOV",
        config_version="agent-2-config",
    )
    wrong_model_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="agent-1-model-mismatch",
        model_version="m2",
    )
    wrong_feature_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="agent-1-feature-mismatch",
        feature_version="f2",
    )

    valid_prediction_id = _insert_prediction(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=prediction_run_id,
        agent_config_version_id=correct_config_id,
        agent_code="AGENT_1_SURFACE_ELO",
        prediction_status="PREDICTED",
        selection_side=1,
        win_probability=0.55,
        variant_id="aligned",
        as_of=as_of,
        prediction_locked_at=locked_at,
        created_at=as_of + timedelta(minutes=10),
    )
    assert valid_prediction_id > 0

    with pytest.raises(psycopg.Error):
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=_insert_prediction_run(
                db,
                run_status="RUNNING",
                as_of=as_of + timedelta(minutes=5),
                started_at=as_of + timedelta(minutes=5),
            ),
            agent_config_version_id=wrong_agent_config_id,
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=0.57,
            variant_id="wrong-agent",
            as_of=as_of + timedelta(minutes=5),
            prediction_locked_at=locked_at + timedelta(minutes=5),
            created_at=as_of + timedelta(minutes=15),
        )

    with pytest.raises(psycopg.Error):
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=_insert_prediction_run(
                db,
                run_status="RUNNING",
                as_of=as_of + timedelta(minutes=10),
                started_at=as_of + timedelta(minutes=10),
            ),
            agent_config_version_id=wrong_model_config_id,
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=0.58,
            variant_id="wrong-model",
            model_version="m1",
            feature_version="f1",
            as_of=as_of + timedelta(minutes=10),
            prediction_locked_at=locked_at + timedelta(minutes=10),
            created_at=as_of + timedelta(minutes=20),
        )

    with pytest.raises(psycopg.Error):
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=_insert_prediction_run(
                db,
                run_status="RUNNING",
                as_of=as_of + timedelta(minutes=15),
                started_at=as_of + timedelta(minutes=15),
            ),
            agent_config_version_id=wrong_feature_config_id,
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=0.59,
            variant_id="wrong-feature",
            model_version="m1",
            feature_version="f1",
            as_of=as_of + timedelta(minutes=15),
            prediction_locked_at=locked_at + timedelta(minutes=15),
            created_at=as_of + timedelta(minutes=25),
        )


def test_predictions_enforce_run_match_agent_identity(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="identity-config",
    )
    run_1_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
    )
    _insert_prediction(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_1_id,
        agent_config_version_id=config_id,
        agent_code="AGENT_1_SURFACE_ELO",
        prediction_status="PREDICTED",
        selection_side=1,
        win_probability=0.55,
        variant_id="first-in-run",
        as_of=_utc(10, 0),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 10),
    )

    with pytest.raises(psycopg.Error):
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=run_1_id,
            agent_config_version_id=config_id,
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=2,
            win_probability=0.45,
            variant_id="second-variant-same-run",
            as_of=_utc(10, 0),
            prediction_locked_at=_utc(11, 0),
            created_at=_utc(10, 11),
        )

    run_2_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 30),
        started_at=_utc(10, 30),
    )
    _insert_prediction(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_2_id,
        agent_config_version_id=config_id,
        agent_code="AGENT_1_SURFACE_ELO",
        prediction_status="PREDICTED",
        selection_side=2,
        win_probability=0.45,
        variant_id="same-agent-different-run",
        as_of=_utc(10, 30),
        prediction_locked_at=_utc(11, 30),
        created_at=_utc(10, 40),
    )

    assert db.execute("SELECT count(*) FROM predictions").fetchone()[0] == 2


def test_predictions_enforce_run_as_of_and_finalization_alignment(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="alignment-config",
    )
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
    )

    _insert_prediction(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        agent_config_version_id=config_id,
        agent_code="AGENT_1_SURFACE_ELO",
        prediction_status="PREDICTED",
        selection_side=1,
        win_probability=0.55,
        variant_id="aligned",
        as_of=_utc(10, 0),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 10),
    )

    with pytest.raises(psycopg.Error):
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=_insert_prediction_run(
                db,
                run_status="RUNNING",
                as_of=_utc(12, 0),
                started_at=_utc(12, 0),
            ),
            agent_config_version_id=config_id,
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=0.56,
            variant_id="bad-as-of",
            as_of=_utc(12, 1),
            prediction_locked_at=_utc(13, 0),
            created_at=_utc(12, 10),
        )

    with pytest.raises(psycopg.Error):
        invalid_run_id = _insert_prediction_run(
            db,
            run_status="RUNNING",
            as_of=_utc(14, 0),
            started_at=_utc(14, 0),
        )
        _insert_prediction_batch(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=invalid_run_id,
            rows=[
                {
                    "agent_config_version_id": config_id,
                    "agent_code": "AGENT_1_SURFACE_ELO",
                    "prediction_status": "PREDICTED",
                    "selection_side": 1,
                    "win_probability": 0.57,
                    "variant_id": "bad-finalization",
                    "as_of": _utc(14, 0),
                    "prediction_locked_at": _utc(15, 1),
                    "created_at": _utc(14, 10),
                }
            ],
            ended_at=_utc(15, 0),
        )


def test_decisions_allow_multiple_rows_for_same_match_and_prediction_run(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    prediction_run_id = _insert_prediction_run(
        db,
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
        prediction_finalized_at=_utc(11, 0),
        ended_at=_utc(11, 5),
    )
    decision_policy_version_id = _insert_decision_policy_version(
        db,
        policy_version="multi-decision-policy",
    )

    _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=prediction_run_id,
        decision_policy_version_id=decision_policy_version_id,
        decision_status="BET",
        reason_code="NONE",
        market_type="MATCH_WINNER",
        selection_type="PLAYER_1",
        selection_side=1,
        consensus_probability=0.55,
        consensus_fair_odds=1.82,
        market_decimal_price=2.12,
        sharp_reference_price=1.95,
        price_source_system="ODDS_API",
        price_bookmaker_code="PINNACLE",
        devig_method="power",
        devig_fair_odds=1.91,
        edge_pct=0.15,
        min_acceptable_odds=1.92,
        active_agent_count=2,
        variant_id="decision-one",
        as_of=_utc(10, 5),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 15),
    )
    _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=prediction_run_id,
        decision_policy_version_id=decision_policy_version_id,
        decision_status="NO_BET",
        reason_code="PRICE_FAILURE",
        signal_type="TYPE_B_PRICE_DISPERSION",
        market_type="MATCH_WINNER",
        selection_type=None,
        selection_side=None,
        line_value=None,
        consensus_probability=None,
        consensus_fair_odds=None,
        market_decimal_price=None,
        sharp_reference_price=None,
        price_source_system=None,
        price_bookmaker_code=None,
        devig_method=None,
        devig_fair_odds=None,
        edge_pct=None,
        min_acceptable_odds=None,
        active_agent_count=2,
        abstaining_agent_count=1,
        variant_id="decision-two",
        as_of=_utc(10, 20),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 30),
    )

    rows = db.execute(
        """
        SELECT decision_id, prediction_run_id, match_id
        FROM decisions
        WHERE prediction_run_id = %s
        ORDER BY decision_id
        """,
        (prediction_run_id,),
    ).fetchall()
    assert len(rows) == 2
    assert {row[1] for row in rows} == {prediction_run_id}
    assert {row[2] for row in rows} == {fixture["match_id"]}


def test_decision_agents_enforce_composite_lineage_keys(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    other_fixture = _insert_match_fixture(
        db,
        player_1_id=_insert_player(db, "Other One"),
        player_2_id=_insert_player(db, "Other Two"),
        source_observed_at=_utc(8, 20),
        as_of=_utc(8, 20),
    )
    run_1_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
    )
    run_2_id = _insert_prediction_run(
        db,
        as_of=_utc(12, 0),
        started_at=_utc(12, 0),
        prediction_finalized_at=_utc(13, 0),
        ended_at=_utc(13, 5),
    )
    policy_id = _insert_decision_policy_version(db, policy_version="lineage-policy")
    agent_1_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="lineage-agent-1",
    )

    prediction_id = _insert_prediction(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_1_id,
        agent_config_version_id=agent_1_config_id,
        agent_code="AGENT_1_SURFACE_ELO",
        prediction_status="PREDICTED",
        selection_side=1,
        win_probability=0.55,
        variant_id="lineage-prediction",
        as_of=_utc(10, 0),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 10),
    )
    decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_1_id,
        decision_policy_version_id=policy_id,
        decision_status="BET",
        reason_code="NONE",
        market_type="MATCH_WINNER",
        selection_type="PLAYER_1",
        selection_side=1,
        consensus_probability=0.55,
        consensus_fair_odds=1.82,
        market_decimal_price=2.12,
        sharp_reference_price=1.95,
        price_source_system="ODDS_API",
        price_bookmaker_code="PINNACLE",
        devig_method="power",
        devig_fair_odds=1.91,
        edge_pct=0.15,
        min_acceptable_odds=1.92,
        active_agent_count=1,
        variant_id="lineage-decision",
        as_of=_utc(10, 5),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 15),
    )

    db.execute(
        """
        INSERT INTO decision_agents (
            decision_id,
            agent_code,
            participation_status,
            prediction_run_id,
            match_id,
            prediction_id,
            proposed_selection_side,
            proposed_probability,
            created_at
        )
        VALUES (%s, 'AGENT_1_SURFACE_ELO', 'ACTIVE', %s, %s, %s, 1, 0.55, %s)
        """,
        (decision_id, run_1_id, fixture["match_id"], prediction_id, _utc(10, 20)),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_2_HIERARCHICAL_MARKOV', 'INACTIVE', %s, %s, NULL, NULL, NULL, %s)
            """,
            (decision_id, run_2_id, fixture["match_id"], _utc(10, 21)),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_2_HIERARCHICAL_MARKOV', 'INACTIVE', %s, %s, NULL, NULL, NULL, %s)
            """,
            (decision_id, run_1_id, other_fixture["match_id"], _utc(10, 22)),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_2_HIERARCHICAL_MARKOV', 'ACTIVE', %s, %s, %s, 1, 0.55, %s)
            """,
            (decision_id, run_1_id, fixture["match_id"], prediction_id, _utc(10, 23)),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_3_INTRANSITIVITY', 'ACTIVE', %s, %s, 999999, 1, 0.55, %s)
            """,
            (decision_id, run_1_id, fixture["match_id"], _utc(10, 24)),
        )


def test_decision_agents_active_rows_require_predicted_lineage_and_audit_copies(
    db: psycopg.Connection,
):
    fixture = _insert_match_fixture(db)
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
    )
    policy_id = _insert_decision_policy_version(db, policy_version="active-policy")
    agent_1_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="active-agent-1",
    )
    agent_2_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_2_HIERARCHICAL_MARKOV",
        config_version="active-agent-2",
    )

    predicted_prediction_id, abstained_prediction_id = _insert_prediction_batch(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        rows=[
            {
                "agent_config_version_id": agent_1_config_id,
                "agent_code": "AGENT_1_SURFACE_ELO",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": 0.61,
                "variant_id": "predicted-for-active",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
                "created_at": _utc(10, 10),
            },
            {
                "agent_config_version_id": agent_2_config_id,
                "agent_code": "AGENT_2_HIERARCHICAL_MARKOV",
                "prediction_status": "ABSTAINED",
                "variant_id": "abstained-for-active",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
                "created_at": _utc(10, 10),
            },
        ],
    )

    good_decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="active-decision-good",
        as_of=_utc(10, 5),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 15),
    )
    db.execute(
        """
        INSERT INTO decision_agents (
            decision_id,
            agent_code,
            participation_status,
            prediction_run_id,
            match_id,
            prediction_id,
            proposed_selection_side,
            proposed_probability,
            created_at
        )
        VALUES (%s, 'AGENT_1_SURFACE_ELO', 'ACTIVE', %s, %s, %s, 1, 0.61, %s)
        """,
        (good_decision_id, run_id, fixture["match_id"], predicted_prediction_id, _utc(10, 20)),
    )

    status_mismatch_decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="active-decision-status-mismatch",
        as_of=_utc(10, 6),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 16),
    )
    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_2_HIERARCHICAL_MARKOV', 'ACTIVE', %s, %s, %s, 1, 0.50, %s)
            """,
            (
                status_mismatch_decision_id,
                run_id,
                fixture["match_id"],
                abstained_prediction_id,
                _utc(10, 21),
            ),
        )

    wrong_side_decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="active-decision-wrong-side",
        as_of=_utc(10, 7),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 17),
    )
    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_1_SURFACE_ELO', 'ACTIVE', %s, %s, %s, 2, 0.61, %s)
            """,
            (wrong_side_decision_id, run_id, fixture["match_id"], predicted_prediction_id, _utc(10, 22)),
        )

    wrong_probability_decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="active-decision-wrong-probability",
        as_of=_utc(10, 8),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 18),
    )
    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_1_SURFACE_ELO', 'ACTIVE', %s, %s, %s, 1, 0.60, %s)
            """,
            (
                wrong_probability_decision_id,
                run_id,
                fixture["match_id"],
                predicted_prediction_id,
                _utc(10, 23),
            ),
        )


def test_decision_agents_abstained_rows_require_abstained_prediction(
    db: psycopg.Connection,
):
    fixture = _insert_match_fixture(db)
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
    )
    policy_id = _insert_decision_policy_version(db, policy_version="abstained-policy")
    agent_1_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="abstained-agent-1",
    )
    agent_2_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_2_HIERARCHICAL_MARKOV",
        config_version="abstained-agent-2",
    )

    predicted_prediction_id, abstained_prediction_id = _insert_prediction_batch(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        rows=[
            {
                "agent_config_version_id": agent_1_config_id,
                "agent_code": "AGENT_1_SURFACE_ELO",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": 0.60,
                "variant_id": "predicted-for-abstained",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
                "created_at": _utc(10, 10),
            },
            {
                "agent_config_version_id": agent_2_config_id,
                "agent_code": "AGENT_2_HIERARCHICAL_MARKOV",
                "prediction_status": "ABSTAINED",
                "variant_id": "abstained-good",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
                "created_at": _utc(10, 10),
            },
        ],
    )

    good_decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="abstained-decision-good",
        as_of=_utc(10, 5),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 15),
    )
    db.execute(
        """
        INSERT INTO decision_agents (
            decision_id,
            agent_code,
            participation_status,
            prediction_run_id,
            match_id,
            prediction_id,
            proposed_selection_side,
            proposed_probability,
            created_at
        )
        VALUES (%s, 'AGENT_2_HIERARCHICAL_MARKOV', 'ABSTAINED', %s, %s, %s, NULL, NULL, %s)
        """,
        (good_decision_id, run_id, fixture["match_id"], abstained_prediction_id, _utc(10, 20)),
    )

    wrong_status_decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="abstained-decision-status-mismatch",
        as_of=_utc(10, 6),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 16),
    )
    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_1_SURFACE_ELO', 'ABSTAINED', %s, %s, %s, NULL, NULL, %s)
            """,
            (
                wrong_status_decision_id,
                run_id,
                fixture["match_id"],
                predicted_prediction_id,
                _utc(10, 21),
            ),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_2_HIERARCHICAL_MARKOV', 'ABSTAINED', %s, %s, %s, 1, NULL, %s)
            """,
            (
                _insert_decision(
                    db,
                    match_id=fixture["match_id"],
                    prediction_run_id=run_id,
                    decision_policy_version_id=policy_id,
                    variant_id="abstained-decision-non-null-side",
                    as_of=_utc(10, 7),
                    prediction_locked_at=_utc(11, 0),
                    created_at=_utc(10, 17),
                ),
                run_id,
                fixture["match_id"],
                abstained_prediction_id,
                _utc(10, 22),
            ),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_2_HIERARCHICAL_MARKOV', 'ABSTAINED', %s, %s, %s, NULL, 0.40, %s)
            """,
            (
                _insert_decision(
                    db,
                    match_id=fixture["match_id"],
                    prediction_run_id=run_id,
                    decision_policy_version_id=policy_id,
                    variant_id="abstained-decision-non-null-probability",
                    as_of=_utc(10, 8),
                    prediction_locked_at=_utc(11, 0),
                    created_at=_utc(10, 18),
                ),
                run_id,
                fixture["match_id"],
                abstained_prediction_id,
                _utc(10, 23),
            ),
        )


def test_decision_agents_inactive_rows_require_null_prediction_and_proposals(
    db: psycopg.Connection,
):
    fixture = _insert_match_fixture(db)
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
    )
    policy_id = _insert_decision_policy_version(db, policy_version="inactive-policy")
    agent_1_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="inactive-agent-1",
    )
    prediction_id = _insert_prediction(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        agent_config_version_id=agent_1_config_id,
        agent_code="AGENT_1_SURFACE_ELO",
        prediction_status="PREDICTED",
        selection_side=1,
        win_probability=0.55,
        variant_id="inactive-prediction",
        as_of=_utc(10, 0),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 10),
    )

    good_decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="inactive-decision-good",
        as_of=_utc(10, 5),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 15),
    )
    db.execute(
        """
        INSERT INTO decision_agents (
            decision_id,
            agent_code,
            participation_status,
            prediction_run_id,
            match_id,
            prediction_id,
            proposed_selection_side,
            proposed_probability,
            created_at
        )
        VALUES (%s, 'AGENT_3_INTRANSITIVITY', 'INACTIVE', %s, %s, NULL, NULL, NULL, %s)
        """,
        (good_decision_id, run_id, fixture["match_id"], _utc(10, 20)),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_1_SURFACE_ELO', 'INACTIVE', %s, %s, %s, NULL, NULL, %s)
            """,
            (
                _insert_decision(
                    db,
                    match_id=fixture["match_id"],
                    prediction_run_id=run_id,
                    decision_policy_version_id=policy_id,
                    variant_id="inactive-decision-bad-link",
                    as_of=_utc(10, 6),
                    prediction_locked_at=_utc(11, 0),
                    created_at=_utc(10, 16),
                ),
                run_id,
                fixture["match_id"],
                prediction_id,
                _utc(10, 21),
            ),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                created_at
            )
            VALUES (%s, 'AGENT_2_HIERARCHICAL_MARKOV', 'INACTIVE', %s, %s, NULL, 1, NULL, %s)
            """,
            (
                _insert_decision(
                    db,
                    match_id=fixture["match_id"],
                    prediction_run_id=run_id,
                    decision_policy_version_id=policy_id,
                    variant_id="inactive-decision-bad-side",
                    as_of=_utc(10, 7),
                    prediction_locked_at=_utc(11, 0),
                    created_at=_utc(10, 17),
                ),
                run_id,
                fixture["match_id"],
                _utc(10, 22),
            ),
        )


def test_no_bet_decision_retains_linked_predictions(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
    )
    policy_id = _insert_decision_policy_version(db, policy_version="no-bet-policy")
    agent_1_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="no-bet-agent-1",
    )
    agent_2_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_2_HIERARCHICAL_MARKOV",
        config_version="no-bet-agent-2",
    )

    predicted_prediction_id, abstained_prediction_id = _insert_prediction_batch(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        rows=[
            {
                "agent_config_version_id": agent_1_config_id,
                "agent_code": "AGENT_1_SURFACE_ELO",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": 0.58,
                "variant_id": "no-bet-predicted",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
                "created_at": _utc(10, 10),
            },
            {
                "agent_config_version_id": agent_2_config_id,
                "agent_code": "AGENT_2_HIERARCHICAL_MARKOV",
                "prediction_status": "ABSTAINED",
                "variant_id": "no-bet-abstained",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
                "created_at": _utc(10, 10),
            },
        ],
    )
    decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        signal_type="TYPE_B_PRICE_DISPERSION",
        decision_status="NO_BET",
        reason_code="PRICE_FAILURE",
        market_type="MATCH_WINNER",
        selection_type=None,
        selection_side=None,
        line_value=None,
        consensus_probability=None,
        consensus_fair_odds=None,
        market_decimal_price=None,
        sharp_reference_price=None,
        price_source_system=None,
        price_bookmaker_code=None,
        devig_method=None,
        devig_fair_odds=None,
        edge_pct=None,
        min_acceptable_odds=None,
        active_agent_count=1,
        abstaining_agent_count=1,
        variant_id="no-bet-decision",
        as_of=_utc(10, 5),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 15),
    )

    db.execute(
        """
        INSERT INTO decision_agents (
            decision_id,
            agent_code,
            participation_status,
            prediction_run_id,
            match_id,
            prediction_id,
            proposed_selection_side,
            proposed_probability,
            created_at
        )
        VALUES
            (%s, 'AGENT_1_SURFACE_ELO', 'ACTIVE', %s, %s, %s, 1, 0.58, %s),
            (%s, 'AGENT_2_HIERARCHICAL_MARKOV', 'ABSTAINED', %s, %s, %s, NULL, NULL, %s)
        """,
        (
            decision_id,
            run_id,
            fixture["match_id"],
            predicted_prediction_id,
            _utc(10, 20),
            decision_id,
            run_id,
            fixture["match_id"],
            abstained_prediction_id,
            _utc(10, 20),
        ),
    )

    assert db.execute("SELECT count(*) FROM predictions").fetchone()[0] == 2
    linked_rows = db.execute(
        """
        SELECT da.agent_code, p.prediction_status
        FROM decision_agents da
        LEFT JOIN predictions p
          ON p.prediction_id = da.prediction_id
         AND p.prediction_run_id = da.prediction_run_id
         AND p.match_id = da.match_id
         AND p.agent_code = da.agent_code
        WHERE da.decision_id = %s
        ORDER BY da.agent_code
        """,
        (decision_id,),
    ).fetchall()
    assert linked_rows == [
        ("AGENT_1_SURFACE_ELO", "PREDICTED"),
        ("AGENT_2_HIERARCHICAL_MARKOV", "ABSTAINED"),
    ]


def test_0004_down_migration_rejects_populated_ted1_state(postgres_dsn: str):
    entries = _migration_entries()
    down_0004 = REPO_ROOT / Path(entries[3]["down"])

    try:
        _reset_public_schema(postgres_dsn)
        _run_manifest_migrations(postgres_dsn, "up")

        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            _insert_prediction_run(
                conn,
                as_of=_utc(10, 0),
                started_at=_utc(10, 0),
                prediction_finalized_at=_utc(11, 0),
                ended_at=_utc(11, 5),
            )

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            _run_psql(postgres_dsn, down_0004)
        combined_output = (exc_info.value.stdout or "") + (exc_info.value.stderr or "")
        assert "requires T-ED1 lineage tables and columns to be empty" in combined_output

        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert _table_exists(conn, "prediction_runs")
            assert conn.execute("SELECT count(*) FROM prediction_runs").fetchone()[0] == 1
    finally:
        _reset_public_schema(postgres_dsn)
        _run_manifest_migrations(postgres_dsn, "up")


def test_0005_upgrade_from_populated_0004_and_reapplies_cleanly(postgres_dsn: str):
    entries = _migration_entries()
    up_0001 = REPO_ROOT / Path(entries[0]["up"])
    up_0002 = REPO_ROOT / Path(entries[1]["up"])
    up_0003 = REPO_ROOT / Path(entries[2]["up"])
    up_0004 = REPO_ROOT / Path(entries[3]["up"])
    up_0005 = REPO_ROOT / Path(entries[4]["up"])
    down_0005 = REPO_ROOT / Path(entries[4]["down"])

    try:
        _reset_public_schema(postgres_dsn)
        _run_psql(postgres_dsn, up_0001)
        _run_psql(postgres_dsn, up_0002)
        _run_psql(postgres_dsn, up_0003)
        _run_psql(postgres_dsn, up_0004)

        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            fixture = _insert_match_fixture(conn)
            policy_id = _insert_decision_policy_version(conn, policy_version="upgrade-policy")
            config_id = _insert_agent_config_version(
                conn,
                agent_code="AGENT_1_SURFACE_ELO",
                config_version="upgrade-config",
            )
            run_id = _insert_prediction_run(
                conn,
                run_status="COMPLETED",
                as_of=_utc(10, 0),
                started_at=_utc(10, 0),
                prediction_finalized_at=_utc(11, 0),
                ended_at=_utc(11, 5),
            )
            prediction_id = _fetchval(
                conn,
                """
                INSERT INTO predictions (
                    match_id,
                    prediction_run_id,
                    agent_config_version_id,
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
                VALUES (%s, %s, %s, 'AGENT_1_SURFACE_ELO', 'PREDICTED', 1, 0.55, 'm1', 'f1', 'upgrade-prediction', %s, %s, %s)
                RETURNING prediction_id
                """,
                (
                    fixture["match_id"],
                    run_id,
                    config_id,
                    _utc(10, 0),
                    _utc(11, 0),
                    _utc(10, 10),
                ),
            )
            decision_id = _insert_decision(
                conn,
                match_id=fixture["match_id"],
                prediction_run_id=run_id,
                decision_policy_version_id=policy_id,
                variant_id="upgrade-decision",
                as_of=_utc(10, 5),
                prediction_locked_at=_utc(11, 0),
                created_at=_utc(10, 15),
            )
            conn.execute(
                """
                INSERT INTO decision_agents (
                    decision_id,
                    agent_code,
                    participation_status,
                    prediction_run_id,
                    match_id,
                    prediction_id,
                    proposed_selection_side,
                    proposed_probability,
                    created_at
                )
                VALUES (%s, 'AGENT_1_SURFACE_ELO', 'ACTIVE', %s, %s, %s, 1, 0.55, %s)
                """,
                (decision_id, run_id, fixture["match_id"], prediction_id, _utc(10, 20)),
            )

        _run_psql(postgres_dsn, up_0005)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert conn.execute(
                """
                SELECT condeferrable, condeferred
                FROM pg_constraint
                WHERE conname = 'fk_predictions_prediction_run_finalization'
                """
            ).fetchone() == (True, False)
            assert conn.execute("SELECT count(*) FROM predictions").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM decision_agents").fetchone()[0] == 1

        _run_psql(postgres_dsn, down_0005)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert conn.execute(
                """
                SELECT condeferrable, condeferred
                FROM pg_constraint
                WHERE conname = 'fk_predictions_prediction_run_finalization'
                """
            ).fetchone() == (False, False)

        _run_psql(postgres_dsn, up_0005)
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            assert conn.execute(
                """
                SELECT condeferrable, condeferred
                FROM pg_constraint
                WHERE conname = 'fk_predictions_prediction_run_finalization'
                """
            ).fetchone() == (True, False)
    finally:
        _reset_public_schema(postgres_dsn)
        _run_manifest_migrations(postgres_dsn, "up")


def test_0005_prediction_runs_enforce_controlled_lifecycle(db: psycopg.Connection):
    running_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(9, 0),
        started_at=_utc(9, 0),
    )
    db.execute(
        """
        UPDATE prediction_runs
        SET prediction_finalized_at = %s
        WHERE prediction_run_id = %s
        """,
        (_utc(9, 30), running_id),
    )
    assert db.execute(
        """
        SELECT run_status, prediction_finalized_at, ended_at
        FROM prediction_runs
        WHERE prediction_run_id = %s
        """,
        (running_id,),
    ).fetchone() == ("RUNNING", _utc(9, 30), None)

    db.execute(
        """
        UPDATE prediction_runs
        SET run_status = 'COMPLETED',
            prediction_finalized_at = %s,
            ended_at = %s
        WHERE prediction_run_id = %s
        """,
        (_utc(9, 30), _utc(9, 35), running_id),
    )

    failed_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
    )
    db.execute(
        """
        UPDATE prediction_runs
        SET run_status = 'FAILED',
            ended_at = %s
        WHERE prediction_run_id = %s
        """,
        (_utc(10, 20), failed_id),
    )

    cancelled_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(11, 0),
        started_at=_utc(11, 0),
    )
    db.execute(
        """
        UPDATE prediction_runs
        SET run_status = 'CANCELLED',
            prediction_finalized_at = %s,
            ended_at = %s
        WHERE prediction_run_id = %s
        """,
        (_utc(11, 10), _utc(11, 12), cancelled_id),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE prediction_runs
            SET experiment_id = 'rewritten'
            WHERE prediction_run_id = %s
            """,
            (running_id,),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE prediction_runs
            SET prediction_finalized_at = %s
            WHERE prediction_run_id = %s
            """,
            (_utc(9, 31), running_id),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE prediction_runs
            SET run_status = 'FAILED'
            WHERE prediction_run_id = %s
            """,
            (running_id,),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            DELETE FROM prediction_runs
            WHERE prediction_run_id = %s
            """,
            (failed_id,),
        )


def test_0005_decisions_allow_one_way_publication_only(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    run_id = _insert_prediction_run(
        db,
        run_status="COMPLETED",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
        prediction_finalized_at=_utc(11, 0),
        ended_at=_utc(11, 5),
    )
    policy_id = _insert_decision_policy_version(db, policy_version="publication-policy")
    decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="publication-decision",
        published_at=None,
    )

    db.execute(
        """
        UPDATE decisions
        SET published_at = %s
        WHERE decision_id = %s
        """,
        (_utc(12, 0), decision_id),
    )
    assert db.execute(
        "SELECT published_at FROM decisions WHERE decision_id = %s",
        (decision_id,),
    ).fetchone()[0] == _utc(12, 0)

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE decisions
            SET published_at = %s
            WHERE decision_id = %s
            """,
            (_utc(12, 5), decision_id),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE decisions
            SET decision_status = 'NO_BET'
            WHERE decision_id = %s
            """,
            (decision_id,),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            DELETE FROM decisions
            WHERE decision_id = %s
            """,
            (decision_id,),
        )


def test_0005_ingest_runs_allow_only_one_terminal_update_and_runtime_finish_path(
    db: psycopg.Connection,
):
    ingest_run_id = start_ingest_run(
        db,
        source_system="SACKMANN",
        source_dataset="matches",
        document=_make_source_document(),
    )
    finish_ingest_run(
        db,
        ingest_run_id=ingest_run_id,
        counters=IngestCounters(
            rows_seen=10,
            rows_inserted=8,
            rows_updated=1,
            rows_rejected=1,
            rows_unmatched=0,
        ),
        status="COMPLETED",
        error_summary=[{"kind": "row_rejected", "count": 1}],
    )
    finished_row = db.execute(
        """
        SELECT status, rows_seen, rows_inserted, rows_updated, rows_rejected, rows_unmatched, finished_at
        FROM ingest_runs
        WHERE ingest_run_id = %s
        """,
        (ingest_run_id,),
    ).fetchone()
    assert finished_row[0:6] == ("COMPLETED", 10, 8, 1, 1, 0)
    assert finished_row[6] is not None

    with pytest.raises(psycopg.Error):
        finish_ingest_run(
            db,
            ingest_run_id=ingest_run_id,
            counters=IngestCounters(rows_seen=11),
            status="FAILED",
            error_summary=[{"kind": "late_rewrite"}],
        )

    running_id = _fetchval(
        db,
        """
        INSERT INTO ingest_runs (
            source_system,
            source_dataset,
            started_at,
            status,
            source_version,
            source_file_name,
            source_file_hash
        )
        VALUES ('ODDS_API', 'prices', %s, 'RUNNING', 'v1', 'odds.csv', 'b')
        RETURNING ingest_run_id
        """,
        (_utc(13, 0),),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE ingest_runs
            SET rows_seen = 5
            WHERE ingest_run_id = %s
            """,
            (running_id,),
        )

    db.execute(
        """
        UPDATE ingest_runs
        SET finished_at = %s,
            status = 'PARTIAL',
            rows_seen = 5,
            rows_inserted = 4,
            rows_updated = 0,
            rows_rejected = 1,
            rows_unmatched = 0,
            error_summary = '[{"kind":"partial"}]'::jsonb
        WHERE ingest_run_id = %s
        """,
        (_utc(13, 10), running_id),
    )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE ingest_runs
            SET source_file_name = 'rewritten.csv'
            WHERE ingest_run_id = %s
            """,
            (running_id,),
        )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            DELETE FROM ingest_runs
            WHERE ingest_run_id = %s
            """,
            (running_id,),
        )


def test_0005_mutable_reference_tables_remain_writable(db: psycopg.Connection):
    ingest_run_id = _fetchval(
        db,
        """
        INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
        VALUES ('SACKMANN', 'matches', %s, 'RUNNING')
        RETURNING ingest_run_id
        """,
        (_utc(8, 0),),
    )
    repository = HistoricalRepository(db, ingest_run_id)

    player_id = _insert_player(db, "Mutable Alias Player")
    repository.ensure_player_alias(player_id, "SACKMANN", "Mutable Alias Player", _utc(8, 0))
    repository.ensure_player_alias(player_id, "SACKMANN", "Mutable Alias Player", _utc(9, 0))
    assert db.execute(
        """
        SELECT first_observed_at, last_observed_at
        FROM player_aliases
        WHERE player_id = %s AND source_system = 'SACKMANN'
        """,
        (player_id,),
    ).fetchone() == (_utc(8, 0), _utc(9, 0))

    tournament_id = _fetchval(
        db,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Mutable Open', 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
        tuple(),
    )
    edition_id = _fetchval(
        db,
        """
        INSERT INTO tournament_editions (
            tournament_id,
            season_year,
            edition_name,
            surface,
            start_date,
            end_date
        )
        VALUES (%s, 2026, 'Mutable Open 2026', NULL, NULL, NULL)
        RETURNING tournament_edition_id
        """,
        (tournament_id,),
    )
    resolved_edition_id = repository.get_or_create_tournament_edition(
        tournament_id=tournament_id,
        season_year=2026,
        edition_name="Mutable Open 2026",
        surface="CLAY",
        start_date=date(2026, 8, 24),
        end_date=date(2026, 8, 31),
    )
    assert resolved_edition_id == edition_id
    assert db.execute(
        """
        SELECT surface, start_date, end_date
        FROM tournament_editions
        WHERE tournament_edition_id = %s
        """,
        (edition_id,),
    ).fetchone() == ("CLAY", date(2026, 8, 24), date(2026, 8, 31))


def test_0005_append_only_tables_reject_update_and_delete(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    player_source_id = _fetchval(
        db,
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id)
        VALUES (%s, 'SACKMANN', 'player-source-1')
        RETURNING player_source_id
        """,
        (fixture["player_1_id"],),
    )
    with db.transaction():
        event_id = _fetchval(
            db,
            """
            INSERT INTO identity_resolution_events (
                source_system,
                raw_player_name,
                normalized_player_name,
                resolution_status,
                source_observed_at,
                as_of
            )
            VALUES ('SACKMANN', 'Ambiguous Player', 'ambiguous player', 'AMBIGUOUS', %s, %s)
            RETURNING identity_resolution_event_id
            """,
            (_utc(8, 0), _utc(8, 0)),
        )
        candidate_id_1 = _fetchval(
            db,
            """
            INSERT INTO identity_resolution_candidates (identity_resolution_event_id, player_id)
            VALUES (%s, %s)
            RETURNING identity_resolution_candidate_id
            """,
            (event_id, fixture["player_1_id"]),
        )
        candidate_id_2 = _fetchval(
            db,
            """
            INSERT INTO identity_resolution_candidates (identity_resolution_event_id, player_id)
            VALUES (%s, %s)
            RETURNING identity_resolution_candidate_id
            """,
            (event_id, fixture["player_2_id"]),
        )
    tournament_id = _fetchval(
        db,
        """
        SELECT tournament_id
        FROM tournament_editions
        WHERE tournament_edition_id = %s
        """,
        (fixture["edition_id"],),
    )
    tournament_source_id = _fetchval(
        db,
        """
        INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id)
        VALUES (%s, 'SACKMANN', 'tournament-source-1')
        RETURNING tournament_source_id
        """,
        (tournament_id,),
    )
    match_source_id = _fetchval(
        db,
        """
        INSERT INTO match_source_ids (match_id, match_version_id, source_system, source_match_id)
        VALUES (%s, %s, 'SACKMANN', 'match-source-1')
        RETURNING match_source_id
        """,
        (fixture["match_id"], fixture["match_version_id"]),
    )
    match_version_source_id = _fetchval(
        db,
        """
        INSERT INTO match_version_sources (
            match_version_id,
            source_system,
            source_match_id,
            source_observed_at,
            as_of
        )
        VALUES (%s, 'SACKMANN', 'match-version-source-1', %s, %s)
        RETURNING match_version_source_id
        """,
        (fixture["match_version_id"], _utc(8, 0), _utc(8, 0)),
    )
    match_result_observation_id = _fetchval(
        db,
        """
        INSERT INTO match_result_observations (
            match_id,
            outcome_status,
            winner_side,
            sets_won_player_1,
            sets_won_player_2,
            games_won_player_1,
            games_won_player_2,
            source_observed_at,
            as_of
        )
        VALUES (%s, 'COMPLETED', 1, 2, 0, 12, 6, %s, %s)
        RETURNING match_result_observation_id
        """,
        (fixture["match_id"], _utc(9, 0), _utc(9, 0)),
    )
    match_stat_observation_id = _fetchval(
        db,
        """
        INSERT INTO match_stat_observations (
            match_id,
            player_id,
            source_system,
            source_record_id,
            observation_kind,
            first_serves_in,
            first_serve_points_won,
            second_serve_points_won,
            service_points_played,
            return_points_won,
            source_observed_at,
            as_of
        )
        VALUES (%s, %s, 'SACKMANN', 'stat-source-1', 'RAW_SOURCE', 20, 15, 10, 30, 8, %s, %s)
        RETURNING match_stat_observation_id
        """,
        (fixture["match_id"], fixture["player_1_id"], _utc(9, 0), _utc(9, 0)),
    )
    odds_snapshot_id = _fetchval(
        db,
        """
        INSERT INTO odds_snapshots (
            match_id,
            source_system,
            bookmaker_code,
            market_type,
            selection_type,
            decimal_price,
            source_snapshot_id,
            source_observed_at,
            as_of
        )
        VALUES (%s, 'ODDS_API', 'PINNACLE', 'MATCH_WINNER', 'PLAYER_1', 2.10, 'odds-source-1', %s, %s)
        RETURNING odds_snapshot_id
        """,
        (fixture["match_id"], _utc(9, 30), _utc(9, 30)),
    )
    rating_snapshot_id = _fetchval(
        db,
        """
        INSERT INTO rating_snapshots (
            player_id,
            rating_metric,
            surface,
            rating_value,
            sample_match_count,
            model_version,
            feature_version,
            as_of
        )
        VALUES (%s, 'ELO', 'HARD', 1875.25, 50, 'm1', 'f1', %s)
        RETURNING rating_snapshot_id
        """,
        (fixture["player_1_id"], _utc(9, 45)),
    )
    policy_id = _insert_decision_policy_version(db, policy_version="append-only-policy")
    config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="append-only-config",
    )
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
    )
    prediction_id = _insert_prediction(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        agent_config_version_id=config_id,
        agent_code="AGENT_1_SURFACE_ELO",
        prediction_status="PREDICTED",
        selection_side=1,
        win_probability=0.55,
        variant_id="append-only-prediction",
        as_of=_utc(10, 0),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 10),
    )
    decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="append-only-decision",
        as_of=_utc(10, 5),
        prediction_locked_at=_utc(11, 0),
        created_at=_utc(10, 15),
    )
    decision_agent_code = "AGENT_1_SURFACE_ELO"
    db.execute(
        """
        INSERT INTO decision_agents (
            decision_id,
            agent_code,
            participation_status,
            prediction_run_id,
            match_id,
            prediction_id,
            proposed_selection_side,
            proposed_probability,
            created_at
        )
        VALUES (%s, %s, 'ACTIVE', %s, %s, %s, 1, 0.55, %s)
        """,
        (decision_id, decision_agent_code, run_id, fixture["match_id"], prediction_id, _utc(10, 20)),
    )
    data_health_check_id = _fetchval(
        db,
        """
        INSERT INTO data_health_checks (
            source_system,
            dataset_name,
            checked_at,
            freshness_state,
            unmatched_count,
            error_count,
            status
        )
        VALUES ('SACKMANN', 'matches', %s, 'FRESH', 0, 0, 'HEALTHY')
        RETURNING data_health_check_id
        """,
        (_utc(12, 0),),
    )

    cases = [
        ("player_source_ids", "player_source_id", player_source_id),
        ("identity_resolution_events", "identity_resolution_event_id", event_id),
        ("identity_resolution_candidates", "identity_resolution_candidate_id", candidate_id_1),
        ("tournament_source_ids", "tournament_source_id", tournament_source_id),
        ("matches", "match_id", fixture["match_id"]),
        ("match_versions", "match_version_id", fixture["match_version_id"]),
        ("match_source_ids", "match_source_id", match_source_id),
        ("match_version_sources", "match_version_source_id", match_version_source_id),
        ("match_result_observations", "match_result_observation_id", match_result_observation_id),
        ("match_stat_observations", "match_stat_observation_id", match_stat_observation_id),
        ("odds_snapshots", "odds_snapshot_id", odds_snapshot_id),
        ("rating_snapshots", "rating_snapshot_id", rating_snapshot_id),
        ("predictions", "prediction_id", prediction_id),
        ("decision_policy_versions", "decision_policy_version_id", policy_id),
        ("agent_config_versions", "agent_config_version_id", config_id),
        ("data_health_checks", "data_health_check_id", data_health_check_id),
    ]

    for table_name, pk_name, pk_value in cases:
        with pytest.raises(psycopg.Error):
            db.execute(
                f"UPDATE {table_name} SET {pk_name} = {pk_name} WHERE {pk_name} = %s",
                (pk_value,),
            )
        with pytest.raises(psycopg.Error):
            db.execute(
                f"DELETE FROM {table_name} WHERE {pk_name} = %s",
                (pk_value,),
            )

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE decision_agents
            SET agent_code = agent_code
            WHERE decision_id = %s AND agent_code = %s
            """,
            (decision_id, decision_agent_code),
        )
    with pytest.raises(psycopg.Error):
        db.execute(
            """
            DELETE FROM decision_agents
            WHERE decision_id = %s AND agent_code = %s
            """,
            (decision_id, decision_agent_code),
        )

    assert candidate_id_2 > candidate_id_1


def test_0005_prediction_insert_protocol_and_lock_serialization(postgres_dsn: str):
    late_insert_result: dict[str, object] = {}
    competing_update_result: dict[str, object] = {}

    try:
        _reset_public_schema(postgres_dsn)
        _run_manifest_migrations(postgres_dsn, "up")
        with psycopg.connect(postgres_dsn, autocommit=True) as setup_conn:
            fixture = _insert_match_fixture(setup_conn)
            run_id = _insert_prediction_run(
                setup_conn,
                run_status="RUNNING",
                as_of=_utc(10, 0),
                started_at=_utc(10, 0),
            )
            config_1_id = _insert_agent_config_version(
                setup_conn,
                agent_code="AGENT_1_SURFACE_ELO",
                config_version="concurrency-agent-1",
            )
            config_2_id = _insert_agent_config_version(
                setup_conn,
                agent_code="AGENT_2_HIERARCHICAL_MARKOV",
                config_version="concurrency-agent-2",
            )

            with pytest.raises(psycopg.Error):
                setup_conn.execute(
                    """
                    INSERT INTO predictions (
                        match_id,
                        prediction_run_id,
                        agent_config_version_id,
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
                    VALUES (%s, %s, %s, 'AGENT_1_SURFACE_ELO', 'PREDICTED', 1, 0.55, 'm1', 'f1', 'standalone-insert', %s, %s, %s)
                    """,
                    (
                        fixture["match_id"],
                        run_id,
                        config_1_id,
                        _utc(10, 0),
                        _utc(11, 0),
                        _utc(10, 10),
                    ),
                )

        conn1 = psycopg.connect(postgres_dsn, autocommit=False)
        conn2 = psycopg.connect(postgres_dsn, autocommit=False)
        conn3 = psycopg.connect(postgres_dsn, autocommit=False)
        try:
            for conn in (conn1, conn2, conn3):
                conn.execute("SET statement_timeout = '5s'")
                conn.execute("SET lock_timeout = '5s'")

            conn1.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
            conn1.execute(
                """
                SELECT prediction_run_id
                FROM prediction_runs
                WHERE prediction_run_id = %s
                FOR UPDATE
                """,
                (run_id,),
            )
            conn1.execute(
                """
                INSERT INTO predictions (
                    match_id,
                    prediction_run_id,
                    agent_config_version_id,
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
                VALUES (%s, %s, %s, 'AGENT_1_SURFACE_ELO', 'PREDICTED', 1, 0.55, 'm1', 'f1', 'serialized-authorized', %s, %s, %s)
                """,
                (
                    fixture["match_id"],
                    run_id,
                    config_1_id,
                    _utc(10, 0),
                    _utc(11, 0),
                    _utc(10, 10),
                ),
            )
            conn1.execute(
                """
                UPDATE prediction_runs
                SET run_status = 'COMPLETED',
                    prediction_finalized_at = %s,
                    ended_at = %s
                WHERE prediction_run_id = %s
                """,
                (_utc(11, 0), _utc(11, 5), run_id),
            )

            def _late_insert_worker() -> None:
                try:
                    conn2.execute(
                        """
                        INSERT INTO predictions (
                            match_id,
                            prediction_run_id,
                            agent_config_version_id,
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
                        VALUES (%s, %s, %s, 'AGENT_2_HIERARCHICAL_MARKOV', 'PREDICTED', 2, 0.45, 'm1', 'f1', 'late-insert', %s, %s, %s)
                        """,
                        (
                            fixture["match_id"],
                            run_id,
                            config_2_id,
                            _utc(10, 0),
                            _utc(11, 0),
                            _utc(10, 11),
                        ),
                    )
                    conn2.commit()
                    late_insert_result["status"] = "committed"
                except psycopg.Error as exc:
                    conn2.rollback()
                    late_insert_result["status"] = "failed"
                    late_insert_result["message"] = str(exc)

            def _competing_update_worker() -> None:
                try:
                    conn3.execute(
                        """
                        UPDATE prediction_runs
                        SET prediction_finalized_at = %s
                        WHERE prediction_run_id = %s
                        """,
                        (_utc(11, 1), run_id),
                    )
                    conn3.commit()
                    competing_update_result["status"] = "committed"
                except psycopg.Error as exc:
                    conn3.rollback()
                    competing_update_result["status"] = "failed"
                    competing_update_result["message"] = str(exc)

            late_insert_thread = threading.Thread(target=_late_insert_worker)
            competing_update_thread = threading.Thread(target=_competing_update_worker)
            late_insert_thread.start()
            competing_update_thread.start()

            time.sleep(0.25)
            assert late_insert_thread.is_alive()
            assert competing_update_thread.is_alive()

            conn1.commit()

            late_insert_thread.join(timeout=5)
            competing_update_thread.join(timeout=5)
            assert late_insert_result["status"] == "failed"
            assert competing_update_result["status"] == "failed"

            with psycopg.connect(postgres_dsn, autocommit=True) as conn:
                assert conn.execute(
                    "SELECT count(*) FROM predictions WHERE prediction_run_id = %s",
                    (run_id,),
                ).fetchone()[0] == 1
                assert conn.execute(
                    """
                    SELECT run_status, prediction_finalized_at, ended_at
                    FROM prediction_runs
                    WHERE prediction_run_id = %s
                    """,
                    (run_id,),
                ).fetchone() == ("COMPLETED", _utc(11, 0), _utc(11, 5))

            with psycopg.connect(postgres_dsn, autocommit=True) as conn:
                failed_run_id = _insert_prediction_run(
                    conn,
                    run_status="FAILED",
                    as_of=_utc(12, 0),
                    started_at=_utc(12, 0),
                    prediction_finalized_at=_utc(12, 4),
                    ended_at=_utc(12, 5),
                )
                cancelled_run_id = _insert_prediction_run(
                    conn,
                    run_status="CANCELLED",
                    as_of=_utc(13, 0),
                    started_at=_utc(13, 0),
                    prediction_finalized_at=_utc(13, 4),
                    ended_at=_utc(13, 5),
                )
                for blocked_run_id, variant_id in (
                    (failed_run_id, "failed-run-block"),
                    (cancelled_run_id, "cancelled-run-block"),
                ):
                    with pytest.raises(psycopg.Error):
                        conn.execute(
                            """
                            INSERT INTO predictions (
                                match_id,
                                prediction_run_id,
                                agent_config_version_id,
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
                            VALUES (%s, %s, %s, 'AGENT_1_SURFACE_ELO', 'PREDICTED', 1, 0.55, 'm1', 'f1', %s, %s, %s, %s)
                            """,
                            (
                                fixture["match_id"],
                                blocked_run_id,
                                config_1_id,
                                variant_id,
                                _utc(12, 0),
                                _utc(12, 30),
                                _utc(12, 10),
                            ),
                        )
        finally:
            conn1.close()
            conn2.close()
            conn3.close()
    finally:
        _reset_public_schema(postgres_dsn)
        _run_manifest_migrations(postgres_dsn, "up")

def test_0006_prediction_run_contract_and_probability_precision(db: psycopg.Connection):
    fixture = _insert_match_fixture(db)
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
        prediction_contract_version="predictive_agent_result_v1",
    )
    _insert_prediction_run_target(db, prediction_run_id=run_id, match_id=fixture["match_id"])
    config_ids = {
        "AGENT_1_SURFACE_ELO": _insert_agent_config_version(
            db,
            agent_code="AGENT_1_SURFACE_ELO",
            config_version="agent-1-common-v1",
        ),
        "AGENT_2_HIERARCHICAL_MARKOV": _insert_agent_config_version(
            db,
            agent_code="AGENT_2_HIERARCHICAL_MARKOV",
            config_version="agent-2-common-v1",
        ),
        "AGENT_3_INTRANSITIVITY": _insert_agent_config_version(
            db,
            agent_code="AGENT_3_INTRANSITIVITY",
            config_version="agent-3-common-v1",
            calibration_version=None,
        ),
    }
    high_precision_probability = Decimal(
        "0.53000000000000000000000000000000000000000000000001"
    )

    prediction_ids = _insert_prediction_batch(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        rows=[
            {
                "agent_config_version_id": config_ids["AGENT_1_SURFACE_ELO"],
                "agent_code": "AGENT_1_SURFACE_ELO",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.550000"),
                "probability_representation": "agent1_raw_probability_v1",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
            },
            {
                "agent_config_version_id": config_ids["AGENT_2_HIERARCHICAL_MARKOV"],
                "agent_code": "AGENT_2_HIERARCHICAL_MARKOV",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.560000"),
                "probability_representation": "agent2_raw_probability_v1",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
            },
            {
                "agent_config_version_id": config_ids["AGENT_3_INTRANSITIVITY"],
                "agent_code": "AGENT_3_INTRANSITIVITY",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": high_precision_probability,
                "probability_representation": "agent3_raw_probability_v1",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
            },
        ],
    )

    stored_probability, representation = db.execute(
        """
        SELECT win_probability, probability_representation
        FROM predictions
        WHERE prediction_id = %s
        """,
        (prediction_ids[2],),
    ).fetchone()
    assert stored_probability == high_precision_probability
    assert representation == "agent3_raw_probability_v1"

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE prediction_runs
            SET prediction_contract_version = 'legacy_prediction_ledger_v1'
            WHERE prediction_run_id = %s
            """,
            (run_id,),
        )


def test_0006_new_contract_requires_target_membership_and_representation_mapping(
    db: psycopg.Connection,
):
    fixture = _insert_match_fixture(db)
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
        prediction_contract_version="predictive_agent_result_v1",
    )
    config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="target-membership-agent-1",
    )

    with pytest.raises(psycopg.Error):
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=run_id,
            agent_config_version_id=config_id,
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=Decimal("0.550000"),
            probability_representation="agent1_raw_probability_v1",
            as_of=_utc(10, 0),
            prediction_locked_at=_utc(11, 0),
            created_at=_utc(10, 10),
        )

    _insert_prediction_run_target(db, prediction_run_id=run_id, match_id=fixture["match_id"])

    with pytest.raises(psycopg.Error):
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=run_id,
            agent_config_version_id=config_id,
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=Decimal("0.550000"),
            probability_representation="agent2_raw_probability_v1",
            as_of=_utc(10, 0),
            prediction_locked_at=_utc(11, 0),
            created_at=_utc(10, 10),
        )


def test_0006_exact_three_agent_completion_gate_and_empty_slate(db: psycopg.Connection):
    empty_run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(8, 0),
        started_at=_utc(8, 0),
        prediction_contract_version="predictive_agent_result_v1",
    )
    db.execute(
        """
        UPDATE prediction_runs
        SET run_status = 'COMPLETED',
            prediction_finalized_at = %s,
            ended_at = %s
        WHERE prediction_run_id = %s
        """,
        (_utc(8, 30), _utc(8, 35), empty_run_id),
    )

    fixture = _insert_match_fixture(db)
    incomplete_run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
        prediction_contract_version="predictive_agent_result_v1",
    )
    _insert_prediction_run_target(
        db,
        prediction_run_id=incomplete_run_id,
        match_id=fixture["match_id"],
    )
    agent_1_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="completion-agent-1",
    )
    with pytest.raises(psycopg.Error):
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=incomplete_run_id,
            agent_config_version_id=agent_1_config_id,
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=Decimal("0.550000"),
            probability_representation="agent1_raw_probability_v1",
            as_of=_utc(10, 0),
            prediction_locked_at=_utc(11, 0),
            created_at=_utc(10, 10),
        )
    assert db.execute(
        "SELECT count(*) FROM predictions WHERE prediction_run_id = %s",
        (incomplete_run_id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT prediction_finalized_at FROM prediction_runs WHERE prediction_run_id = %s",
        (incomplete_run_id,),
    ).fetchone()[0] is None

    complete_run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(12, 0),
        started_at=_utc(12, 0),
        prediction_contract_version="predictive_agent_result_v1",
    )
    _insert_prediction_run_target(db, prediction_run_id=complete_run_id, match_id=fixture["match_id"])
    agent_2_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_2_HIERARCHICAL_MARKOV",
        config_version="completion-agent-2",
    )
    agent_3_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_3_INTRANSITIVITY",
        config_version="completion-agent-3",
    )
    _insert_prediction_batch(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=complete_run_id,
        rows=[
            {
                "agent_config_version_id": agent_1_config_id,
                "agent_code": "AGENT_1_SURFACE_ELO",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.550000"),
                "probability_representation": "agent1_raw_probability_v1",
                "as_of": _utc(12, 0),
                "prediction_locked_at": _utc(13, 0),
            },
            {
                "agent_config_version_id": agent_2_config_id,
                "agent_code": "AGENT_2_HIERARCHICAL_MARKOV",
                "prediction_status": "ABSTAINED",
                "probability_representation": None,
                "as_of": _utc(12, 0),
                "prediction_locked_at": _utc(13, 0),
            },
            {
                "agent_config_version_id": agent_3_config_id,
                "agent_code": "AGENT_3_INTRANSITIVITY",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.53000000000000000000000000000000000000000000000001"),
                "probability_representation": "agent3_raw_probability_v1",
                "as_of": _utc(12, 0),
                "prediction_locked_at": _utc(13, 0),
            },
        ],
    )


def test_0006_target_and_prediction_post_finalization_inserts_are_blocked(
    db: psycopg.Connection,
):
    fixture = _insert_match_fixture(db)
    config_ids = {
        "AGENT_1_SURFACE_ELO": _insert_agent_config_version(
            db,
            agent_code="AGENT_1_SURFACE_ELO",
            config_version="post-final-agent-1",
        ),
        "AGENT_2_HIERARCHICAL_MARKOV": _insert_agent_config_version(
            db,
            agent_code="AGENT_2_HIERARCHICAL_MARKOV",
            config_version="post-final-agent-2",
        ),
        "AGENT_3_INTRANSITIVITY": _insert_agent_config_version(
            db,
            agent_code="AGENT_3_INTRANSITIVITY",
            config_version="post-final-agent-3",
        ),
    }
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
        prediction_contract_version="predictive_agent_result_v1",
    )
    _insert_prediction_run_target(db, prediction_run_id=run_id, match_id=fixture["match_id"])
    _insert_prediction_batch(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        rows=[
            {
                "agent_config_version_id": config_ids["AGENT_1_SURFACE_ELO"],
                "agent_code": "AGENT_1_SURFACE_ELO",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.550000"),
                "probability_representation": "agent1_raw_probability_v1",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
            },
            {
                "agent_config_version_id": config_ids["AGENT_2_HIERARCHICAL_MARKOV"],
                "agent_code": "AGENT_2_HIERARCHICAL_MARKOV",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.560000"),
                "probability_representation": "agent2_raw_probability_v1",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
            },
            {
                "agent_config_version_id": config_ids["AGENT_3_INTRANSITIVITY"],
                "agent_code": "AGENT_3_INTRANSITIVITY",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.57000000000000000000000000000000000000000000000001"),
                "probability_representation": "agent3_raw_probability_v1",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
            },
        ],
    )
    second_fixture = _insert_match_fixture(db)

    with pytest.raises(psycopg.Error):
        _insert_prediction_run_target(db, prediction_run_id=run_id, match_id=second_fixture["match_id"])

    with pytest.raises(psycopg.Error):
        _insert_prediction(
            db,
            match_id=fixture["match_id"],
            prediction_run_id=run_id,
            agent_config_version_id=config_ids["AGENT_1_SURFACE_ELO"],
            agent_code="AGENT_1_SURFACE_ELO",
            prediction_status="PREDICTED",
            selection_side=1,
            win_probability=Decimal("0.580000"),
            probability_representation="agent1_raw_probability_v1",
            as_of=_utc(10, 0),
            prediction_locked_at=_utc(11, 0),
        )


def test_0006_decision_agent_lineage_requires_exact_probability_and_representation(
    db: psycopg.Connection,
):
    fixture = _insert_match_fixture(db)
    run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(10, 0),
        started_at=_utc(10, 0),
        prediction_contract_version="predictive_agent_result_v1",
    )
    _insert_prediction_run_target(db, prediction_run_id=run_id, match_id=fixture["match_id"])
    config_ids = {
        "AGENT_1_SURFACE_ELO": _insert_agent_config_version(
            db,
            agent_code="AGENT_1_SURFACE_ELO",
            config_version="decision-lineage-agent-1",
        ),
        "AGENT_2_HIERARCHICAL_MARKOV": _insert_agent_config_version(
            db,
            agent_code="AGENT_2_HIERARCHICAL_MARKOV",
            config_version="decision-lineage-agent-2",
        ),
        "AGENT_3_INTRANSITIVITY": _insert_agent_config_version(
            db,
            agent_code="AGENT_3_INTRANSITIVITY",
            config_version="decision-lineage-agent-3",
        ),
    }
    prediction_ids = _insert_prediction_batch(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        rows=[
            {
                "agent_config_version_id": config_ids["AGENT_1_SURFACE_ELO"],
                "agent_code": "AGENT_1_SURFACE_ELO",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.550000"),
                "probability_representation": "agent1_raw_probability_v1",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
            },
            {
                "agent_config_version_id": config_ids["AGENT_2_HIERARCHICAL_MARKOV"],
                "agent_code": "AGENT_2_HIERARCHICAL_MARKOV",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.560000"),
                "probability_representation": "agent2_raw_probability_v1",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
            },
            {
                "agent_config_version_id": config_ids["AGENT_3_INTRANSITIVITY"],
                "agent_code": "AGENT_3_INTRANSITIVITY",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.57000000000000000000000000000000000000000000000001"),
                "probability_representation": "agent3_raw_probability_v1",
                "as_of": _utc(10, 0),
                "prediction_locked_at": _utc(11, 0),
            },
        ],
    )
    policy_id = _insert_decision_policy_version(db, policy_version="decision-lineage-policy")
    decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="decision-lineage",
        as_of=_utc(10, 5),
        prediction_locked_at=_utc(11, 0),
    )

    db.execute(
        """
        INSERT INTO decision_agents (
            decision_id,
            agent_code,
            participation_status,
            prediction_run_id,
            match_id,
            prediction_id,
            proposed_selection_side,
            proposed_probability,
            probability_representation,
            created_at
        )
        VALUES (%s, 'AGENT_3_INTRANSITIVITY', 'ACTIVE', %s, %s, %s, 1, %s, 'agent3_raw_probability_v1', %s)
        """,
        (
            decision_id,
            run_id,
            fixture["match_id"],
            prediction_ids[2],
            Decimal("0.57000000000000000000000000000000000000000000000001"),
            _utc(10, 20),
        ),
    )

    wrong_decision_id = _insert_decision(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=run_id,
        decision_policy_version_id=policy_id,
        variant_id="decision-lineage-wrong",
        as_of=_utc(10, 6),
        prediction_locked_at=_utc(11, 0),
    )
    with pytest.raises(psycopg.Error):
        db.execute(
            """
            INSERT INTO decision_agents (
                decision_id,
                agent_code,
                participation_status,
                prediction_run_id,
                match_id,
                prediction_id,
                proposed_selection_side,
                proposed_probability,
                probability_representation,
                created_at
            )
            VALUES (%s, 'AGENT_3_INTRANSITIVITY', 'ACTIVE', %s, %s, %s, 1, %s, 'agent2_raw_probability_v1', %s)
            """,
            (
                wrong_decision_id,
                run_id,
                fixture["match_id"],
                prediction_ids[2],
                Decimal("0.57000000000000000000000000000000000000000000000001"),
                _utc(10, 21),
            ),
        )


def test_0006_loader_reads_only_finalized_new_contract_persisted_truth(
    db: psycopg.Connection,
):
    from sports_edge_tennis.data.prediction_persistence import (
        load_finalized_predictive_agent_predictions,
    )

    fixture = _insert_match_fixture(db)
    legacy_run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(9, 0),
        started_at=_utc(9, 0),
    )
    legacy_config_id = _insert_agent_config_version(
        db,
        agent_code="AGENT_1_SURFACE_ELO",
        config_version="loader-legacy-agent-1",
    )
    _insert_prediction(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=legacy_run_id,
        agent_config_version_id=legacy_config_id,
        agent_code="AGENT_1_SURFACE_ELO",
        prediction_status="PREDICTED",
        selection_side=1,
        win_probability=Decimal("0.550000"),
        as_of=_utc(9, 0),
        prediction_locked_at=_utc(10, 0),
    )

    failed_run_id = _insert_prediction_run(
        db,
        run_status="FAILED",
        as_of=_utc(12, 0),
        started_at=_utc(12, 0),
        ended_at=_utc(12, 30),
        prediction_contract_version="predictive_agent_result_v1",
    )

    complete_run_id = _insert_prediction_run(
        db,
        run_status="RUNNING",
        as_of=_utc(14, 0),
        started_at=_utc(14, 0),
        prediction_contract_version="predictive_agent_result_v1",
    )
    _insert_prediction_run_target(db, prediction_run_id=complete_run_id, match_id=fixture["match_id"])
    config_ids = {
        "AGENT_1_SURFACE_ELO": _insert_agent_config_version(
            db,
            agent_code="AGENT_1_SURFACE_ELO",
            config_version="loader-agent-1",
        ),
        "AGENT_2_HIERARCHICAL_MARKOV": _insert_agent_config_version(
            db,
            agent_code="AGENT_2_HIERARCHICAL_MARKOV",
            config_version="loader-agent-2",
        ),
        "AGENT_3_INTRANSITIVITY": _insert_agent_config_version(
            db,
            agent_code="AGENT_3_INTRANSITIVITY",
            config_version="loader-agent-3",
        ),
    }
    _insert_prediction_batch(
        db,
        match_id=fixture["match_id"],
        prediction_run_id=complete_run_id,
        rows=[
            {
                "agent_config_version_id": config_ids["AGENT_1_SURFACE_ELO"],
                "agent_code": "AGENT_1_SURFACE_ELO",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.550000"),
                "probability_representation": "agent1_raw_probability_v1",
                "as_of": _utc(14, 0),
                "prediction_locked_at": _utc(15, 0),
            },
            {
                "agent_config_version_id": config_ids["AGENT_2_HIERARCHICAL_MARKOV"],
                "agent_code": "AGENT_2_HIERARCHICAL_MARKOV",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.560000"),
                "probability_representation": "agent2_raw_probability_v1",
                "as_of": _utc(14, 0),
                "prediction_locked_at": _utc(15, 0),
            },
            {
                "agent_config_version_id": config_ids["AGENT_3_INTRANSITIVITY"],
                "agent_code": "AGENT_3_INTRANSITIVITY",
                "prediction_status": "PREDICTED",
                "selection_side": 1,
                "win_probability": Decimal("0.57000000000000000000000000000000000000000000000001"),
                "probability_representation": "agent3_raw_probability_v1",
                "as_of": _utc(14, 0),
                "prediction_locked_at": _utc(15, 0),
            },
        ],
    )

    assert load_finalized_predictive_agent_predictions(db, prediction_run_id=legacy_run_id) == []
    assert load_finalized_predictive_agent_predictions(db, prediction_run_id=failed_run_id) == []

    rows = load_finalized_predictive_agent_predictions(db, prediction_run_id=complete_run_id)
    assert [row.agent_code for row in rows] == [
        "AGENT_1_SURFACE_ELO",
        "AGENT_2_HIERARCHICAL_MARKOV",
        "AGENT_3_INTRANSITIVITY",
    ]
    assert rows[2].win_probability == Decimal(
        "0.57000000000000000000000000000000000000000000000001"
    )
