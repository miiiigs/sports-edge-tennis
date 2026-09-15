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
