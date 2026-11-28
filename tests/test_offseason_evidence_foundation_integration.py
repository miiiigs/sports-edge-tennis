"""Joint integration gate for the two independently developed evidence migrations."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_evidence_migration_manifest_and_cumulative_schema():
    entries = json.loads((ROOT / "db/migrations/manifest.json").read_text())["migrations"]
    versions = [entry["version"] for entry in entries]
    assert len(set(versions)) == len(versions)
    assert versions == [f"{i:04d}" for i in range(1, len(entries) + 1)]
    assert entries[22]["name"] == "prediction_feature_snapshots"
    assert entries[23]["name"] == "oddspapi_identity_history"
    for entry in entries:
        for kind in ("up", "down", "schema_snapshot"):
            assert (ROOT / entry[kind]).is_file()
    first = (ROOT / entries[22]["schema_snapshot"]).read_text()
    combined = (ROOT / entries[23]["schema_snapshot"]).read_text()
    assert combined.startswith(first.rstrip())
    for table in ("prediction_feature_snapshots", "oddspapi_event_identity_history"):
        assert combined.count(f"CREATE TABLE {table} (") == 1


def test_both_evidence_tables_exist_after_manifest_upgrade(db):
    for table in ("prediction_feature_snapshots", "oddspapi_event_identity_history"):
        assert db.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()[0]
        triggers = db.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass(%s) "
            "AND NOT tgisinternal AND tgenabled <> 'D'",
            (f"public.{table}",),
        ).fetchall()
        assert triggers, f"Missing enabled evidence protection trigger on {table}"
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
