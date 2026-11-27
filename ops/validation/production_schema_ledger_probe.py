"""Read-only production migration-ledger probe.

This repository has no explicit migration ledger table: production migrations
are applied manually and the de-facto ledger is the set of schema objects each
migration creates. This probe reports object presence only.

Safety:
  * connects with ``default_transaction_read_only=on`` and an explicit
    ``REPEATABLE READ READ ONLY`` transaction;
  * never executes DDL or DML;
  * prints no DSNs, names, credentials or row values.

Run inside an environment that can reach the target database, for example:

    railway run python ops/validation/production_schema_ledger_probe.py

or with an explicit URL:

    TENNIS_DATABASE_URL=... python ops/validation/production_schema_ledger_probe.py

It is intentionally not wired into the runtime.
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

# (version, kind, argument) - one distinctive object per migration.
SIGNATURES = [
    ("0001_tennis_data_foundation", "table", "players"),
    ("0002_day5_historical_integrity", "enum_label", "competition_tier:ATP_TOUR"),
    ("0003_day5_temporal_reconciliation", "column", "match_versions.match_start_at"),
    ("0004_prediction_runs_and_policy_versions", "table", "prediction_runs"),
    ("0005_append_only_enforcement", "function", "raise_append_only_mutation"),
    ("0006_common_predictive_interface", "column",
     "prediction_runs.prediction_contract_version"),
    ("0007_manager_consensus_layer", "table", "manager_consensus_results"),
    ("0008_market_value_v1", "table", "market_policy_versions"),
    ("0009_live_odds_ingestion_v1", "table", "raw_odds_observations"),
    ("0010_quarter_kelly_stake_v1", "column", "decisions.suggested_stake_fraction"),
    ("0011_a2_history_data_regime", "table", "a2_history_data_regimes"),
    ("0012_publication_control_v1", "table", "publication_control_events"),
    ("0013_model_identity_bridge_v1", "table", "model_identity_bridge_events"),
    ("0014_strict_three_manager_policy_v2", "constraint",
     "chk_manager_policy_versions_allow_agent3_abstention"),
    ("0015_oddspapi_request_ledger_v1", "table", "oddspapi_request_ledger"),
    ("0016_cross_provider_match_reconciliations", "table",
     "cross_provider_match_reconciliations"),
    ("0017_prediction_abstention_reasons", "column",
     "predictions.abstain_reason_code"),
    ("0018_tournament_identity_corrections", "table",
     "tournament_identity_corrections"),
    ("0019_provider_fixture_metadata", "table", "provider_fixture_metadata"),
    ("0020_a3_support_diagnostics", "table", "a3_support_diagnostics"),
    ("0021_walkover_winner_optional", "constraint_def",
     "match_result_observations|(outcome_status = 'WALKOVER'::result_status) OR ("),
    ("0022_defaulted_outcome", "enum_label", "result_status:DEFAULTED"),
    ("0023_prediction_feature_snapshots", "table", "prediction_feature_snapshots"),
    ("0024_oddspapi_identity_history", "table", "oddspapi_event_identity_history"),
]


def _connect() -> tuple[str, psycopg.Connection]:
    errors: list[str] = []
    for name in DSN_CANDIDATES:
        value = os.environ.get(name)
        if not value:
            continue
        try:
            return name, psycopg.connect(value, connect_timeout=15, autocommit=False)
        except psycopg.OperationalError as exc:
            # Class only: never echo the DSN.
            errors.append(f"{name}:{type(exc).__name__}")
    raise SystemExit(f"no reachable database URL: {errors}")


def probe(conn: psycopg.Connection) -> dict[str, object]:
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET statement_timeout = '20s'")
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        raise SystemExit("read-only guard failed")

    out: dict[str, object] = {}

    def table(name: str) -> bool:
        return conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",)
        ).fetchone()[0]

    def column(table_name: str, column_name: str) -> bool:
        return conn.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s
                  AND column_name=%s)
            """,
            (table_name, column_name),
        ).fetchone()[0]

    def enum_label(spec: str) -> bool:
        type_name, label = spec.split(":", 1)
        return conn.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid
                JOIN pg_namespace n ON n.oid=t.typnamespace
                WHERE n.nspname='public' AND t.typname=%s AND e.enumlabel=%s)
            """,
            (type_name, label),
        ).fetchone()[0]

    def function(name: str) -> bool:
        return conn.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.proname=%s)
            """,
            (name,),
        ).fetchone()[0]

    def constraint(name: str) -> bool:
        return conn.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname=%s)",
            (name,),
        ).fetchone()[0]

    def constraint_def(spec: str) -> bool:
        table_name, needle = spec.split("|", 1)
        return conn.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid=c.conrelid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public' AND t.relname=%s
                  AND pg_get_constraintdef(c.oid) ILIKE %s)
            """,
            (table_name, f"%{needle}%"),
        ).fetchone()[0]

    resolvers = {
        "table": table,
        "enum_label": enum_label,
        "function": function,
        "constraint": constraint,
        "constraint_def": constraint_def,
    }

    def resolve(kind: str, argument: str) -> bool:
        if kind == "column":
            table_name, column_name = argument.split(".", 1)
            return column(table_name, column_name)
        return bool(resolvers[kind](argument))

    ledger = {version: resolve(kind, argument)
              for version, kind, argument in SIGNATURES}
    out["migration_signatures"] = ledger
    out["applied_version_count"] = sum(ledger.values())
    out["highest_contiguous_version"] = _highest_contiguous(ledger)
    out["ledger_tables"] = [
        r[0]
        for r in conn.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public'
              AND (table_name ILIKE '%migration%'
                   OR table_name ILIKE '%schema_version%')
            ORDER BY 1
            """
        ).fetchall()
    ]
    for name in ("prediction_feature_snapshots", "oddspapi_event_identity_history"):
        out[f"{name}_rows"] = (
            conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            if table(name) else None
        )
    out["append_only_triggers"] = [
        r[0]
        for r in conn.execute(
            """
            SELECT t.tgname FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgenabled <> 'D'
              AND c.relname IN ('prediction_feature_snapshots',
                                'oddspapi_event_identity_history')
            ORDER BY 1
            """
        ).fetchall()
    ]
    return out


def _highest_contiguous(ledger: dict[str, bool]) -> str | None:
    highest = None
    for version in sorted(ledger):
        if not ledger[version]:
            break
        highest = version
    return highest


def main() -> int:
    name, conn = _connect()
    with conn:
        result = probe(conn)
        conn.rollback()
    result["dsn_variable"] = name
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
