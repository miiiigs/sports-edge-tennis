import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DB_ROOT = REPO_ROOT / "db"
MANIFEST_PATH = DB_ROOT / "migrations" / "manifest.json"
UP_MIGRATION_PATH = DB_ROOT / "migrations" / "0001_tennis_data_foundation.up.sql"
DOWN_MIGRATION_PATH = DB_ROOT / "migrations" / "0001_tennis_data_foundation.down.sql"
UP_MIGRATION_PATH_0002 = DB_ROOT / "migrations" / "0002_day5_historical_integrity.up.sql"
DOWN_MIGRATION_PATH_0002 = DB_ROOT / "migrations" / "0002_day5_historical_integrity.down.sql"
UP_MIGRATION_PATH_0003 = DB_ROOT / "migrations" / "0003_day5_temporal_reconciliation.up.sql"
DOWN_MIGRATION_PATH_0003 = DB_ROOT / "migrations" / "0003_day5_temporal_reconciliation.down.sql"
UP_MIGRATION_PATH_0004 = DB_ROOT / "migrations" / "0004_prediction_runs_and_policy_versions.up.sql"
DOWN_MIGRATION_PATH_0004 = DB_ROOT / "migrations" / "0004_prediction_runs_and_policy_versions.down.sql"
UP_MIGRATION_PATH_0005 = DB_ROOT / "migrations" / "0005_append_only_enforcement_and_lock_guards.up.sql"
DOWN_MIGRATION_PATH_0005 = DB_ROOT / "migrations" / "0005_append_only_enforcement_and_lock_guards.down.sql"
UP_MIGRATION_PATH_0006 = DB_ROOT / "migrations" / "0006_common_predictive_interface_persistence.up.sql"
DOWN_MIGRATION_PATH_0006 = DB_ROOT / "migrations" / "0006_common_predictive_interface_persistence.down.sql"
UP_MIGRATION_PATH_0009 = DB_ROOT / "migrations" / "0009_live_odds_ingestion_v1.up.sql"
DOWN_MIGRATION_PATH_0009 = DB_ROOT / "migrations" / "0009_live_odds_ingestion_v1.down.sql"
UP_MIGRATION_PATH_0010 = DB_ROOT / "migrations" / "0010_quarter_kelly_stake_v1.up.sql"
DOWN_MIGRATION_PATH_0010 = DB_ROOT / "migrations" / "0010_quarter_kelly_stake_v1.down.sql"
UP_MIGRATION_PATH_0011 = DB_ROOT / "migrations" / "0011_a2_history_data_regime_v1.up.sql"
DOWN_MIGRATION_PATH_0011 = DB_ROOT / "migrations" / "0011_a2_history_data_regime_v1.down.sql"
UP_MIGRATION_PATH_0012 = DB_ROOT / "migrations" / "0012_publication_control_v1.up.sql"
DOWN_MIGRATION_PATH_0012 = DB_ROOT / "migrations" / "0012_publication_control_v1.down.sql"
SCHEMA_SNAPSHOT_PATH = DB_ROOT / "schema" / "0001_tennis_data_foundation.sql"
SCHEMA_SNAPSHOT_PATH_0002 = DB_ROOT / "schema" / "0002_day5_historical_integrity.sql"
SCHEMA_SNAPSHOT_PATH_0003 = DB_ROOT / "schema" / "0003_day5_temporal_reconciliation.sql"
SCHEMA_SNAPSHOT_PATH_0004 = DB_ROOT / "schema" / "0004_prediction_runs_and_policy_versions.sql"
SCHEMA_SNAPSHOT_PATH_0005 = DB_ROOT / "schema" / "0005_append_only_enforcement_and_lock_guards.sql"
SCHEMA_SNAPSHOT_PATH_0006 = DB_ROOT / "schema" / "0006_common_predictive_interface_persistence.sql"
SCHEMA_SNAPSHOT_PATH_0009 = DB_ROOT / "schema" / "0009_live_odds_ingestion_v1.sql"
SCHEMA_SNAPSHOT_PATH_0010 = DB_ROOT / "schema" / "0010_quarter_kelly_stake_v1.sql"
SCHEMA_SNAPSHOT_PATH_0011 = DB_ROOT / "schema" / "0011_a2_history_data_regime_v1.sql"
SCHEMA_SNAPSHOT_PATH_0012 = DB_ROOT / "schema" / "0012_publication_control_v1.sql"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized_sql(text: str) -> str:
    text = re.sub(r"--.*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _table_block(sql: str, table_name: str) -> str:
    pattern = rf"create table {table_name} \((.*?)\);"
    match = re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
    assert match, f"missing table {table_name}"
    return match.group(1).lower()


def _type_block(sql: str, type_name: str) -> str:
    pattern = rf"create type {type_name} as enum \((.*?)\);"
    match = re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
    assert match, f"missing enum {type_name}"
    return match.group(1).lower()


def test_migration_manifest_is_ordered_and_files_exist():
    manifest = json.loads(_read_text(MANIFEST_PATH))
    migrations = manifest["migrations"]
    versions = [entry["version"] for entry in migrations]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert migrations[0]["up"] == "db/migrations/0001_tennis_data_foundation.up.sql"
    assert migrations[0]["down"] == "db/migrations/0001_tennis_data_foundation.down.sql"
    assert migrations[0]["schema_snapshot"] == "db/schema/0001_tennis_data_foundation.sql"
    assert migrations[1]["up"] == "db/migrations/0002_day5_historical_integrity.up.sql"
    assert migrations[1]["down"] == "db/migrations/0002_day5_historical_integrity.down.sql"
    assert migrations[1]["schema_snapshot"] == "db/schema/0002_day5_historical_integrity.sql"
    assert migrations[2]["up"] == "db/migrations/0003_day5_temporal_reconciliation.up.sql"
    assert migrations[2]["down"] == "db/migrations/0003_day5_temporal_reconciliation.down.sql"
    assert migrations[2]["schema_snapshot"] == "db/schema/0003_day5_temporal_reconciliation.sql"
    assert migrations[3]["up"] == "db/migrations/0004_prediction_runs_and_policy_versions.up.sql"
    assert migrations[3]["down"] == "db/migrations/0004_prediction_runs_and_policy_versions.down.sql"
    assert migrations[3]["schema_snapshot"] == "db/schema/0004_prediction_runs_and_policy_versions.sql"
    assert migrations[4]["up"] == "db/migrations/0005_append_only_enforcement_and_lock_guards.up.sql"
    assert migrations[4]["down"] == "db/migrations/0005_append_only_enforcement_and_lock_guards.down.sql"
    assert migrations[4]["schema_snapshot"] == "db/schema/0005_append_only_enforcement_and_lock_guards.sql"
    assert migrations[5]["up"] == "db/migrations/0006_common_predictive_interface_persistence.up.sql"
    assert migrations[5]["down"] == "db/migrations/0006_common_predictive_interface_persistence.down.sql"
    assert migrations[5]["schema_snapshot"] == "db/schema/0006_common_predictive_interface_persistence.sql"
    assert UP_MIGRATION_PATH.exists()
    assert DOWN_MIGRATION_PATH.exists()
    assert UP_MIGRATION_PATH_0002.exists()
    assert DOWN_MIGRATION_PATH_0002.exists()
    assert UP_MIGRATION_PATH_0003.exists()
    assert DOWN_MIGRATION_PATH_0003.exists()
    assert UP_MIGRATION_PATH_0004.exists()
    assert DOWN_MIGRATION_PATH_0004.exists()
    assert UP_MIGRATION_PATH_0005.exists()
    assert DOWN_MIGRATION_PATH_0005.exists()
    assert UP_MIGRATION_PATH_0006.exists()
    assert DOWN_MIGRATION_PATH_0006.exists()
    assert UP_MIGRATION_PATH_0009.exists()
    assert DOWN_MIGRATION_PATH_0009.exists()
    assert UP_MIGRATION_PATH_0010.exists()
    assert DOWN_MIGRATION_PATH_0010.exists()
    assert UP_MIGRATION_PATH_0011.exists()
    assert DOWN_MIGRATION_PATH_0011.exists()
    assert SCHEMA_SNAPSHOT_PATH.exists()
    assert SCHEMA_SNAPSHOT_PATH_0002.exists()
    assert SCHEMA_SNAPSHOT_PATH_0003.exists()
    assert SCHEMA_SNAPSHOT_PATH_0004.exists()
    assert SCHEMA_SNAPSHOT_PATH_0005.exists()
    assert SCHEMA_SNAPSHOT_PATH_0006.exists()
    assert SCHEMA_SNAPSHOT_PATH_0009.exists()
    assert SCHEMA_SNAPSHOT_PATH_0010.exists()
    assert SCHEMA_SNAPSHOT_PATH_0011.exists()


def test_schema_snapshot_matches_bootstrap_migration():
    assert _normalized_sql(_read_text(UP_MIGRATION_PATH)) == _normalized_sql(
        _read_text(SCHEMA_SNAPSHOT_PATH)
    )
    expected_full_schema = _normalized_sql(_read_text(UP_MIGRATION_PATH) + "\n" + _read_text(UP_MIGRATION_PATH_0002))
    assert expected_full_schema == _normalized_sql(_read_text(SCHEMA_SNAPSHOT_PATH_0002))
    expected_full_schema_0003 = _normalized_sql(
        _read_text(UP_MIGRATION_PATH)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0002)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0003)
    )
    assert expected_full_schema_0003 == _normalized_sql(_read_text(SCHEMA_SNAPSHOT_PATH_0003))
    expected_full_schema_0004 = _normalized_sql(
        _read_text(UP_MIGRATION_PATH)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0002)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0003)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0004)
    )
    assert expected_full_schema_0004 == _normalized_sql(_read_text(SCHEMA_SNAPSHOT_PATH_0004))
    expected_full_schema_0005 = _normalized_sql(
        _read_text(UP_MIGRATION_PATH)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0002)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0003)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0004)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0005)
    )
    assert expected_full_schema_0005 == _normalized_sql(_read_text(SCHEMA_SNAPSHOT_PATH_0005))
    expected_full_schema_0006 = _normalized_sql(
        _read_text(UP_MIGRATION_PATH)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0002)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0003)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0004)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0005)
        + "\n"
        + _read_text(UP_MIGRATION_PATH_0006)
    )
    assert expected_full_schema_0006 == _normalized_sql(_read_text(SCHEMA_SNAPSHOT_PATH_0006))


def test_live_odds_v1_migration_manifest_and_snapshot():
    manifest = json.loads(_read_text(MANIFEST_PATH))
    assert next(entry for entry in manifest["migrations"] if entry["version"] == "0009") == {
        "version": "0009",
        "name": "live_odds_ingestion_v1",
        "up": "db/migrations/0009_live_odds_ingestion_v1.up.sql",
        "down": "db/migrations/0009_live_odds_ingestion_v1.down.sql",
        "schema_snapshot": "db/schema/0009_live_odds_ingestion_v1.sql",
    }
    expected_full_schema_0009 = _normalized_sql(
        "\n".join(
            _read_text(REPO_ROOT / Path(entry["up"]))
            for entry in manifest["migrations"]
            if entry["version"] <= "0009"
        )
    )
    assert expected_full_schema_0009 == _normalized_sql(_read_text(SCHEMA_SNAPSHOT_PATH_0009))

    sql = _read_text(UP_MIGRATION_PATH_0009).lower()
    assert "create table raw_odds_observations" in sql
    assert "create table provider_bookmaker_mappings" in sql
    assert "effective_to" not in sql
    assert "add column odds_origin odds_origin" in sql
    assert "chk_odds_snapshots_live_provider_lineage" in sql


def test_quarter_kelly_stake_v1_migration_manifest_and_snapshot():
    manifest = json.loads(_read_text(MANIFEST_PATH))
    assert next(entry for entry in manifest["migrations"] if entry["version"] == "0010") == {
        "version": "0010",
        "name": "quarter_kelly_stake_v1",
        "up": "db/migrations/0010_quarter_kelly_stake_v1.up.sql",
        "down": "db/migrations/0010_quarter_kelly_stake_v1.down.sql",
        "schema_snapshot": "db/schema/0010_quarter_kelly_stake_v1.sql",
    }
    expected_full_schema_0010 = _normalized_sql(
        "\n".join(
            _read_text(REPO_ROOT / Path(entry["up"]))
            for entry in manifest["migrations"]
            if entry["version"] <= "0010"
        )
    )
    assert expected_full_schema_0010 == _normalized_sql(_read_text(SCHEMA_SNAPSHOT_PATH_0010))

    sql = _read_text(UP_MIGRATION_PATH_0010).lower()
    assert "staking_rule_version text" in sql
    assert "suggested_stake_fraction numeric(18, 12)" in sql
    assert "quarter_kelly_cap_2_v1" in sql
    assert "chk_decisions_no_positive_stake_without_bet" in sql


def test_a2_history_data_regime_v1_migration_manifest_and_snapshot():
    manifest = json.loads(_read_text(MANIFEST_PATH))
    entry_0011 = next(
        entry for entry in manifest["migrations"] if entry["version"] == "0011"
    )
    assert entry_0011 == {
        "version": "0011",
        "name": "a2_history_data_regime_v1",
        "up": "db/migrations/0011_a2_history_data_regime_v1.up.sql",
        "down": "db/migrations/0011_a2_history_data_regime_v1.down.sql",
        "schema_snapshot": "db/schema/0011_a2_history_data_regime_v1.sql",
    }
    expected_full_schema_0011 = _normalized_sql(
        "\n".join(
            _read_text(REPO_ROOT / Path(entry["up"]))
            for entry in manifest["migrations"]
            if entry["version"] <= "0011"
        )
    )
    assert expected_full_schema_0011 == _normalized_sql(
        _read_text(SCHEMA_SNAPSHOT_PATH_0011)
    )

    sql = _read_text(UP_MIGRATION_PATH_0011).lower()
    assert "create table a2_history_data_regimes" in sql
    assert "source_system text not null check (source_system = 'api_tennis')" in sql
    assert "activated_at timestamptz not null" in sql
    assert "window_start date not null" in sql
    assert "window_end date not null" in sql
    assert "chunk_days smallint not null" in sql
    assert "trg_a2_history_data_regimes_append_only_mutation" in sql
    assert "execute function raise_append_only_mutation()" in sql


def test_publication_control_v1_migration_manifest_and_snapshot():
    manifest = json.loads(_read_text(MANIFEST_PATH))
    assert manifest["migrations"][-1] == {
        "version": "0012",
        "name": "publication_control_v1",
        "up": "db/migrations/0012_publication_control_v1.up.sql",
        "down": "db/migrations/0012_publication_control_v1.down.sql",
        "schema_snapshot": "db/schema/0012_publication_control_v1.sql",
    }
    expected_full_schema_0012 = _normalized_sql(
        "\n".join(
            _read_text(REPO_ROOT / Path(entry["up"]))
            for entry in manifest["migrations"]
        )
    )
    assert expected_full_schema_0012 == _normalized_sql(
        _read_text(SCHEMA_SNAPSHOT_PATH_0012)
    )

    sql = _read_text(UP_MIGRATION_PATH_0012).lower()
    assert "create table publication_control_events" in sql
    assert "create type publication_control_action" in sql
    assert "create type publication_control_source" in sql
    assert "trg_publication_control_events_append_only_mutation" in sql
    assert "execute function raise_append_only_mutation()" in sql


def test_day5_forward_migration_is_separate_from_bootstrap():
    bootstrap_sql = _read_text(UP_MIGRATION_PATH).lower()
    assert "'atp_tour'" not in _type_block(bootstrap_sql, "competition_tier")

    day5_sql = _read_text(UP_MIGRATION_PATH_0002).lower()
    day5_down_sql = _read_text(DOWN_MIGRATION_PATH_0002).lower()
    day5_temporal_sql = _read_text(UP_MIGRATION_PATH_0003).lower()
    day5_temporal_down_sql = _read_text(DOWN_MIGRATION_PATH_0003).lower()
    assert "add value if not exists 'atp_tour'" in day5_sql
    assert "rename to competition_tier_day5_historical_integrity" in day5_down_sql
    assert "requires atp_tour rows to be removed before rollback" in day5_down_sql
    assert "alter column match_start_at drop not null" in day5_temporal_sql
    assert "requires rows with null match_start_at to be removed before rollback" in day5_temporal_down_sql


def test_schema_contains_required_tables_and_temporal_columns():
    sql = _read_text(UP_MIGRATION_PATH)
    required_tables = [
        "ingest_runs",
        "players",
        "player_source_ids",
        "player_aliases",
        "identity_resolution_events",
        "identity_resolution_candidates",
        "tournaments",
        "tournament_editions",
        "matches",
        "match_versions",
        "match_version_sources",
        "match_result_observations",
        "match_stat_observations",
        "odds_snapshots",
        "rating_snapshots",
        "predictions",
        "decisions",
        "decision_clv",
        "data_health_checks",
    ]
    for table_name in required_tables:
        assert f"create table {table_name} (" in sql.lower()

    match_versions = _table_block(sql, "match_versions")
    for column in ("match_start_at", "source_observed_at", "as_of", "ingested_at"):
        assert column in match_versions

    odds_snapshots = _table_block(sql, "odds_snapshots")
    for column in ("source_observed_at", "as_of", "ingested_at"):
        assert column in odds_snapshots

    predictions = _table_block(sql, "predictions")
    assert "prediction_locked_at" in predictions


def test_player_identity_constraints_are_fail_closed():
    sql = _read_text(UP_MIGRATION_PATH).lower()
    players = _table_block(sql, "players")
    assert "canonical_name text not null" in players
    assert "primary key" in players
    assert "canonical_name primary key" not in players

    player_source_ids = _table_block(sql, "player_source_ids")
    assert "unique (source_system, source_player_id)" in player_source_ids

    identity_resolution = _table_block(sql, "identity_resolution_events")
    assert "resolved_player_id bigint references players(player_id)" in identity_resolution

    identity_resolution_candidates = _table_block(sql, "identity_resolution_candidates")
    assert (
        "identity_resolution_event_id bigint not null references "
        "identity_resolution_events(identity_resolution_event_id)" in identity_resolution_candidates
    )
    assert "player_id bigint not null references players(player_id)" in identity_resolution_candidates
    assert "unique (identity_resolution_event_id, player_id)" in identity_resolution_candidates
    assert "create function validate_identity_resolution_candidate_state" in sql
    assert "create constraint trigger trg_identity_resolution_candidates_candidate_state" in sql

    resolution_status = _type_block(sql, "identity_resolution_status")
    for value in ("'resolved'", "'unmatched'", "'ambiguous'"):
        assert value in resolution_status


def test_match_probability_and_odds_constraints_are_encoded():
    sql = _read_text(UP_MIGRATION_PATH).lower()

    match_versions = _table_block(sql, "match_versions")
    assert "check (player_1_id <> player_2_id)" in match_versions

    predictions = _table_block(sql, "predictions")
    assert "win_probability numeric(8, 6)" in predictions
    assert "win_probability >= 0 and win_probability <= 1" in predictions
    assert "prediction_status = 'abstained'" in predictions

    odds_snapshots = _table_block(sql, "odds_snapshots")
    assert "decimal_price numeric(12, 6) not null check (decimal_price > 1)" in odds_snapshots
    assert "market_type <> 'match_winner'" in odds_snapshots
    assert "nulls not distinct" in sql
    assert "create unique index uq_match_stat_source_record" in sql
    assert "source_record_id,\n        match_id,\n        player_id,\n        observation_kind,\n        source_observed_at" in _read_text(UP_MIGRATION_PATH)
    assert "create unique index uq_odds_snapshots_source_hash" in sql


def test_predictions_remain_blind_and_decisions_separate_type_a_type_b():
    sql = _read_text(UP_MIGRATION_PATH).lower()
    predictions = _table_block(sql, "predictions")
    forbidden_prediction_fields = [
        "market_decimal_price",
        "sharp_reference_price",
        "price_bookmaker_code",
        "devig_method",
    ]
    for field in forbidden_prediction_fields:
        assert field not in predictions

    signal_type = _type_block(sql, "signal_type")
    assert "'type_a_model_edge'" in signal_type
    assert "'type_b_price_dispersion'" in signal_type

    decisions = _table_block(sql, "decisions")
    assert "decision_status decision_status not null" in decisions
    assert "reason_code decision_reason_code not null default 'none'" in decisions
    assert "signal_type signal_type not null" in decisions
    assert "created_at >= prediction_locked_at" not in decisions
    assert "created_at >= as_of" in decisions
    assert "reason_code not in ('none', 'veto_triggered')" in decisions
    assert "when selection_type in ('player_1', 'side_1') then selection_side is not distinct from 1" in decisions
    assert "when selection_type in ('player_2', 'side_2') then selection_side is not distinct from 2" in decisions

    decision_settlements = _table_block(sql, "decision_settlements")
    assert "settled_at timestamptz" in decision_settlements
    assert "settled_at is null or settled_at >= created_at" not in decision_settlements


def test_readme_and_docs_reflect_postgres_foundation():
    readme = _read_text(REPO_ROOT / "README.md")
    data_model = _read_text(REPO_ROOT / "docs" / "DATA_MODEL.md")
    point_in_time = _read_text(REPO_ROOT / "docs" / "POINT_IN_TIME.md")
    decision_log = _read_text(REPO_ROOT / "docs" / "DECISION_LOG.md")
    db_readme = _read_text(DB_ROOT / "README.md")

    assert "db/" in readme
    assert "append-only" in data_model.lower()
    assert "sha-256" in data_model.lower()
    assert "match_version_sources" in data_model
    assert "as_of <= T" in point_in_time
    assert "created_at may fall before or after prediction_locked_at" in point_in_time
    assert "github actions" in db_readme.lower()
    assert "match_version_sources" in decision_log
    assert "PostgreSQL" in db_readme
