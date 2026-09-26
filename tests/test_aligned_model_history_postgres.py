from sports_edge_tennis.dashboard.model_pick_history import (
    load_aligned_daily_model_pick_summaries,
)


def test_aligned_model_pick_history_query_compiles_against_migrated_postgres(db):
    assert load_aligned_daily_model_pick_summaries(db) == ()
