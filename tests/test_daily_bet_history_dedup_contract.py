from datetime import date

from sports_edge_tennis.dashboard.daily_bets import (
    load_daily_bet_summaries,
    load_daily_bets,
)


class _Result:
    def fetchall(self):
        return []


class _Conn:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        return _Result()


def test_results_queries_use_first_type_a_bet_per_match():
    conn = _Conn()

    assert load_daily_bet_summaries(conn) == ()
    assert load_daily_bets(conn, decision_date=date(2026, 9, 15)) == ()

    assert len(conn.sql) == 2
    for sql, _params in conn.sql:
        assert "DISTINCT ON (d.match_id)" in sql
        assert "ORDER BY d.match_id, COALESCE(d.published_at, d.as_of), d.decision_id" in sql
        assert "d.published_at IS NOT NULL" in sql
        assert "d.signal_type = 'TYPE_A_MODEL_EDGE'" in sql
        assert "d.decision_status = 'BET'" in sql
        assert "d.market_type = 'MATCH_WINNER'" in sql
