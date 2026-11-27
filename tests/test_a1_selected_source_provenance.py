from datetime import datetime, timedelta, timezone

import pytest

from sports_edge_tennis.validation.a1_selected_source_provenance import (
    SOURCE_CENSUS_SQL,
    audit_selected_history_sources,
)

AT = datetime(2026, 11, 25, 7, 20, tzinfo=timezone.utc)
HISTORY = AT - timedelta(days=1)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows, *, read_only="on", isolation="repeatable read"):
        self.rows = rows
        self.read_only = read_only
        self.isolation = isolation
        self.params = None

    def execute(self, sql, params=None):
        if sql == "SHOW transaction_read_only":
            return Cursor([(self.read_only,)])
        if sql == "SHOW transaction_isolation":
            return Cursor([(self.isolation,)])
        assert sql == SOURCE_CENSUS_SQL
        self.params = params
        return Cursor(self.rows)


def row(mid, *, selected=HISTORY, result_source="tennis_data", version_source="API_TENNIS"):
    return (mid, HISTORY, 8, selected, result_source, "results", "v1", "hash-r",
            9, version_source, "fixtures", "v2", "hash-v",
            [{"source_system": version_source}])


def ledger(*ids):
    return [{"match_id": mid, "history_as_of": HISTORY.isoformat()} for mid in ids]


def test_source_census_preserves_distinct_ingest_sources_and_unknown_identity():
    conn = Connection([row(1), row(2, result_source=None)])
    result = audit_selected_history_sources(conn, ledger_rows=ledger(2, 1), cutoff=AT)
    assert conn.params["ids"] == [1, 2]
    assert result["ledger_matches"] == 2
    assert result["status_counts"] == {
        "SELECTED_ROWS_SOURCE_ATTRIBUTED": 1, "SOURCE_UNKNOWN": 1}
    assert result["attributed_groups"] == [
        {"result_source": "tennis_data", "version_source": "API_TENNIS", "matches": 1}]
    assert result["records"][0]["identity_source"] == "UNKNOWN_NOT_TRACED"
    assert result["can_promote"] is False


def test_cutoff_mismatch_does_not_enter_source_attributed_groups():
    report = audit_selected_history_sources(
        Connection([row(1, selected=AT)]), ledger_rows=ledger(1), cutoff=AT)
    assert report["status_counts"] == {"EXPECTED_HISTORY_AS_OF_MISMATCH": 1}
    assert report["attributed_groups"] == []


def test_rejects_missing_denominator_and_duplicates():
    with pytest.raises(ValueError, match="denominator"):
        audit_selected_history_sources(Connection([]), ledger_rows=ledger(1), cutoff=AT)
    with pytest.raises(ValueError, match="duplicate"):
        audit_selected_history_sources(Connection([]), ledger_rows=ledger(1, 1), cutoff=AT)


@pytest.mark.parametrize("read_only,isolation", [
    ("off", "repeatable read"), ("on", "read committed")])
def test_requires_readonly_repeatable_snapshot(read_only, isolation):
    with pytest.raises(ValueError):
        audit_selected_history_sources(
            Connection([], read_only=read_only, isolation=isolation),
            ledger_rows=ledger(1), cutoff=AT)
