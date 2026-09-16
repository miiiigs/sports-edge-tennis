from decimal import Decimal

import pytest

from sports_edge_tennis.performance.opportunity_evidence import (
    _safe_decimal_ratio,
    load_entry_timing_buckets,
    load_lifecycle_summary,
    load_match_lifecycles,
)


def test_empty_database_has_empty_opportunity_evidence(db):
    summary = load_lifecycle_summary(db)
    assert summary.completed_runs == 0
    assert summary.unique_targets == 0
    assert summary.ever_consensus == 0
    assert summary.ever_bet == 0
    assert summary.latest_consensus == 0
    assert summary.latest_no_bet == 0
    assert summary.latest_unresolved == 0

    assert load_match_lifecycles(db) == ()
    assert load_entry_timing_buckets(db) == ()


def test_lifecycle_limit_is_guarded(db):
    with pytest.raises(ValueError, match="limit must be between"):
        load_match_lifecycles(db, limit=0)
    with pytest.raises(ValueError, match="limit must be between"):
        load_match_lifecycles(db, limit=1001)


def test_safe_decimal_ratio():
    assert _safe_decimal_ratio(Decimal("2.5"), 0) is None
    assert _safe_decimal_ratio(Decimal("1.5"), 3) == Decimal("0.5")
