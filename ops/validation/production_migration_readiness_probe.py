"""Read-only production migration-readiness probe for migrations 0023/0024.

Complements ``production_schema_ledger_probe.py`` (object presence only).
This probe answers the operational questions a release review must answer
*before* an approved migration window:

  * which server/role/session settings apply to DDL;
  * how large the tables the migrations touch actually are;
  * whether existing data can satisfy the new constraints;
  * which objects depend on ``match_versions`` (lock surface);
  * whether the current role may create the new objects at all;
  * whether another session holds locks that would block the migration.

Safety:
  * ``default_transaction_read_only=on`` and ``REPEATABLE READ READ ONLY``;
  * no DDL, no DML, no temp tables;
  * prints aggregate counts, sizes and booleans only: no DSNs, names,
    credentials or row values.

Usage (inside a network that can reach the database):

    TENNIS_DATABASE_URL=... python ops/validation/production_migration_readiness_probe.py

Not wired into the runtime.
"""
from __future__ import annotations

import json
import os
import sys

import psycopg

DSN_CANDIDATES = (
    "TENNIS_DATABASE_URL",
    "DATABASE_PUBLIC_URL",
    "DATABASE_URL",
    "DATABASE_PRIVATE_URL",
    "PGURL",
)

# Exact counts are bounded by statement_timeout; these are the tables the
# release either touches (lock surface, constraint validation) or needs for
# rollback planning.
COUNTED_TABLES = (
    "matches",
    "match_versions",
    "match_source_ids",
    "match_version_sources",
    "match_result_observations",
    "players",
    "agent_config_versions",
    "prediction_runs",
    "prediction_run_targets",
    "predictions",
    "manager_consensus_results",
    "rating_snapshots",
    "tournament_editions",
    "tournament_source_ids",
    "oddspapi_event_identity_cache",
    "oddspapi_request_ledger",
)

SIZED_TABLES = (
    "match_versions",
    "predictions",
    "prediction_runs",
    "manager_consensus_results",
    "match_result_observations",
    "rating_snapshots",
    "raw_odds_observations",
)


def _connect() -> tuple[str, psycopg.Connection]:
    errors: list[str] = []
    for name in DSN_CANDIDATES:
        value = os.environ.get(name)
        if not value:
            continue
        try:
            return name, psycopg.connect(value, connect_timeout=15, autocommit=False)
        except psycopg.OperationalError as exc:
            errors.append(f"{name}:{type(exc).__name__}")
    raise SystemExit(f"no reachable database URL: {errors}")


def _setting(conn: psycopg.Connection, name: str) -> str | None:
    row = conn.execute("SELECT current_setting(%s, true)", (name,)).fetchone()
    return None if row is None else row[0]


def _relation(conn: psycopg.Connection, table: str) -> str | None:
    row = conn.execute("SELECT to_regclass(%s)::text", (f"public.{table}",)).fetchone()
    return row[0]


def _count(conn: psycopg.Connection, table: str) -> int | None:
    if _relation(conn, table) is None:
        return None
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def probe(conn: psycopg.Connection) -> dict[str, object]:
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET statement_timeout = '60s'")
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        raise SystemExit("read-only guard failed")

    out: dict[str, object] = {}

    out["server"] = {
        "version_num": conn.execute("SHOW server_version_num").fetchone()[0],
        "version": conn.execute("SHOW server_version").fetchone()[0],
        "current_user": conn.execute("SELECT current_user").fetchone()[0],
        "session_user_is_superuser": conn.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = session_user"
        ).fetchone()[0],
        "current_user_can_create_in_public": conn.execute(
            "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
        ).fetchone()[0],
        "database_size_bytes": conn.execute(
            "SELECT pg_database_size(current_database())"
        ).fetchone()[0],
    }
    out["session_settings"] = {
        name: _setting(conn, name)
        for name in (
            "lock_timeout",
            "statement_timeout",
            "idle_in_transaction_session_timeout",
            "maintenance_work_mem",
            "work_mem",
            "max_locks_per_transaction",
            "max_parallel_maintenance_workers",
        )
    }
    out["application_settings"] = {
        "server_version_num": _setting(conn, "server_version_num"),
        "default_transaction_read_only": _setting(conn, "default_transaction_read_only"),
    }

    out["row_counts"] = {t: _count(conn, t) for t in COUNTED_TABLES}

    sizes: dict[str, object] = {}
    for table in SIZED_TABLES:
        if _relation(conn, table) is None:
            sizes[table] = None
            continue
        row = conn.execute(
            """
            SELECT pg_total_relation_size(c.oid),
                   pg_relation_size(c.oid),
                   COALESCE((SELECT sum(pg_total_relation_size(i.indexrelid))
                             FROM pg_index i WHERE i.indrelid = c.oid), 0),
                   c.reltuples::bigint,
                   pg_size_pretty(pg_total_relation_size(c.oid))
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public' AND c.relname=%s
            """,
            (table,),
        ).fetchone()
        sizes[table] = {
            "total_bytes": row[0],
            "heap_bytes": row[1],
            "index_bytes": row[2],
            "planner_estimate_rows": row[3],
            "total_pretty": row[4],
        }
    out["relation_sizes"] = sizes

    out["tables_present"] = {
        t: _relation(conn, t) is not None
        for t in (
            "prediction_feature_snapshots",
            "oddspapi_event_identity_history",
            "oddspapi_event_identity_cache",
            "agent_config_versions",
            "match_version_sources",
        )
    }

    # Objects that would be touched or newly depended on by 0023/0024.
    out["match_versions_locks"] = {
        "row_count": out["row_counts"]["match_versions"],
        "existing_indexes": [
            r[0]
            for r in conn.execute(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname='public' AND tablename='match_versions'
                ORDER BY 1
                """
            ).fetchall()
        ],
        "foreign_keys_referencing": conn.execute(
            """
            SELECT count(*) FROM pg_constraint
            WHERE contype='f' AND confrelid='public.match_versions'::regclass
            """
        ).fetchone()[0],
        "dependent_views": conn.execute(
            """
            SELECT count(*) FROM pg_depend d
            JOIN pg_rewrite r ON r.oid = d.objid
            JOIN pg_class v ON v.oid = r.ev_class
            WHERE d.refobjid = 'public.match_versions'::regclass AND v.relkind = 'v'
            """
        ).fetchone()[0],
        "dependent_triggers": conn.execute(
            """
            SELECT count(*) FROM pg_trigger
            WHERE tgrelid='public.match_versions'::regclass AND NOT tgisinternal
            """
        ).fetchone()[0],
    }

    out["constraint_prerequisites"] = {
        "uq_predictions_lineage_identity_present": conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_predictions_lineage_identity')"
        ).fetchone()[0],
        "uq_predictions_lineage_identity_columns": [
            r[0]
            for r in conn.execute(
                """
                SELECT a.attname
                FROM pg_constraint c
                JOIN unnest(c.conkey) WITH ORDINALITY k(attnum, ord) ON TRUE
                JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum
                WHERE c.conname='uq_predictions_lineage_identity'
                ORDER BY k.ord
                """
            ).fetchall()
        ],
        "agent_code_labels": [
            r[0]
            for r in conn.execute(
                """
                SELECT e.enumlabel FROM pg_enum e
                JOIN pg_type t ON t.oid=e.enumtypid
                WHERE t.typname='agent_code' ORDER BY e.enumsortorder
                """
            ).fetchall()
        ],
        "raise_append_only_mutation_present": conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname='raise_append_only_mutation')"
        ).fetchone()[0],
        "match_versions_orientation_duplicates": conn.execute(
            """
            SELECT count(*) FROM (
                SELECT match_version_id, player_1_id, player_2_id
                FROM match_versions
                GROUP BY 1,2,3 HAVING count(*) > 1
            ) d
            """
        ).fetchone()[0],
        "predictions_lineage_duplicates": conn.execute(
            """
            SELECT count(*) FROM (
                SELECT prediction_id, prediction_run_id, match_id, agent_code
                FROM predictions
                GROUP BY 1,2,3,4 HAVING count(*) > 1
            ) d
            """
        ).fetchone()[0],
        "predictions_without_run": conn.execute(
            "SELECT count(*) FROM predictions WHERE prediction_run_id IS NULL"
        ).fetchone()[0],
    }

    out["migration_lock_state"] = {
        "other_sessions": conn.execute(
            """
            SELECT count(*) FROM pg_stat_activity
            WHERE datname=current_database() AND pid <> pg_backend_pid()
            """
        ).fetchone()[0],
        "sessions_in_transaction": conn.execute(
            """
            SELECT count(*) FROM pg_stat_activity
            WHERE datname=current_database() AND pid <> pg_backend_pid()
              AND state <> 'idle'
            """
        ).fetchone()[0],
        "blocking_locks": conn.execute(
            """
            SELECT count(*) FROM pg_locks l
            JOIN pg_stat_activity a ON a.pid=l.pid
            WHERE NOT l.granted
            """
        ).fetchone()[0],
        "longest_active_transaction_seconds": conn.execute(
            """
            SELECT COALESCE(max(EXTRACT(EPOCH FROM (now() - xact_start))), 0)::int
            FROM pg_stat_activity
            WHERE datname=current_database() AND pid <> pg_backend_pid()
              AND xact_start IS NOT NULL
            """
        ).fetchone()[0],
        "replication_slots": conn.execute(
            "SELECT count(*) FROM pg_replication_slots"
        ).fetchone()[0],
    }

    out["predictions_by_agent"] = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT agent_code::text, count(*) FROM predictions GROUP BY 1 ORDER BY 1"
        ).fetchall()
    }
    out["tournament_source_ids_by_provider"] = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT source_system, count(*) FROM tournament_source_ids GROUP BY 1 ORDER BY 1"
        ).fetchall()
    }
    out["tournament_editions_missing_end_date"] = conn.execute(
        "SELECT count(*) FROM tournament_editions WHERE end_date IS NULL"
    ).fetchone()[0]

    return out


def main() -> int:
    name, conn = _connect()
    try:
        with conn:
            result = probe(conn)
            conn.rollback()
    finally:
        conn.close()
    result["dsn_variable"] = name
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
