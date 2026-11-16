"""Matched Manager-vs-Original audit prevents misleading unmatched rate comparison."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ops.brian_manager_matched import compare, matched_sql


def _row(mid, a_side, m_side, winner=1, terminal="COMPLETED"):
    when=datetime(2026,11,6,8,tzinfo=UTC)
    return (
        mid, date(2026,11,6), when+timedelta(hours=2),
        when if a_side else None, a_side,
        Decimal(".78") if a_side else None,
        "MANAGER_CONSENSUS" if a_side else None,
        when if m_side else None, m_side,
        Decimal(".74") if m_side else None,
        terminal, winner, when+timedelta(hours=5),
    )


def test_matched_manager_separates_kept_rejected_and_changed_sides():
    report=compare([
        _row(1,1,1),  # agreed both won
        _row(2,1,2),  # opposite sides, Manager lost
        _row(3,1,None,winner=2),  # original rejected loss
        _row(4,None,1,winner=1),  # Manager-only win
        _row(5,1,1,terminal="WALKOVER"),  # void, not loss
    ])
    buckets=report["matched_buckets"]
    assert buckets["MATCHED_SAME_SIDE"]["picks"]==2
    assert buckets["MATCHED_SAME_SIDE"]["manager_won"]==1
    assert buckets["MATCHED_DIFFERENT_SIDE"]["original_won"]==1
    assert buckets["MATCHED_DIFFERENT_SIDE"]["manager_lost"]==1
    assert buckets["ORIGINAL_ONLY_NOT_MANAGER"]["original_lost"]==1
    assert buckets["MANAGER_ONLY_NOT_ORIGINAL"]["manager_won"]==1
    assert report["total_distinct_matches"]==5
    with pytest.raises(ValueError, match="duplicate"):
        compare([_row(1,1,1),_row(1,1,1)])


def test_matched_query_includes_issue_time_and_as_of_source_checks():
    sql=matched_sql()
    assert "FULL OUTER JOIN first_model_picks" in sql
    assert "mro" not in sql or "x.ingested_at <= %(as_of)s" in sql
    assert "x.source_observed_at <= %(as_of)s" in sql
    assert "aligned_picked_at" in sql
    assert "manager_picked_at" in sql
    assert "INSERT INTO" not in sql
