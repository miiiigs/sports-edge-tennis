from datetime import date

from sports_edge_tennis.dashboard.daily_bets import (
    load_daily_bet_summaries,
    load_daily_bets,
)


def test_daily_bet_history_queries_compile_against_migrated_postgres(db):
    assert load_daily_bet_summaries(db) == ()
    assert load_daily_bets(db, decision_date=date(2026, 9, 13)) == ()
