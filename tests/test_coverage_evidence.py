from sports_edge_tennis.performance.coverage_evidence import (
    _ratio,
    load_agent_coverage_slices,
)


def test_empty_database_has_no_coverage_slices(db):
    assert load_agent_coverage_slices(db) == ()


def test_coverage_ratio_guard():
    assert _ratio(0, 0) is None
    assert _ratio(3, 4) == 0.75
