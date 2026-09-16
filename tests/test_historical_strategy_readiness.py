from sports_edge_tennis.validation.historical_strategy_readiness import (
    load_historical_strategy_readiness,
)


def test_empty_database_blocks_full_historical_type_a_backtest(db):
    result = load_historical_strategy_readiness(
        db,
        year_start=2024,
        year_end=2025,
    )

    assert result.ready_for_full_type_a_backtest is False
    assert [row.season_year for row in result.years] == [2024, 2025]
    assert all(row.completed_matches == 0 for row in result.years)
    assert "insufficient years: 2024,2025" in result.reason


class _Rows:
    def fetchall(self):
        return []


class _CaptureConn:
    def __init__(self):
        self.sql = ""

    def execute(self, sql, params):
        self.sql = sql
        return _Rows()


def test_readiness_query_requires_true_prestart_same_snapshot_pinnacle_pair():
    conn = _CaptureConn()
    load_historical_strategy_readiness(conn, year_start=2024, year_end=2024)

    sql = conn.sql
    assert "os.source_observed_at < s.match_start_at" in sql
    assert "os.as_of < s.match_start_at" in sql
    assert "os.source_system = 'SHARP'" in sql
    assert "os.bookmaker_code = 'PINNACLE'" in sql
    assert "COALESCE(os.source_snapshot_id, os.source_record_hash)" in sql
    assert "HAVING count(DISTINCT os.selection_type) = 2" in sql
