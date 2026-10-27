"""Prevent favorable historical cherry-picking in live Manager day logs."""

from datetime import UTC, date, datetime
from sports_edge_tennis.performance.manager_daily_evidence import (
    ManagerDailyEvidence, load_manager_daily_evidence,
    manager_daily_query, render_manager_daily_evidence,
)


def test_daily_score_requires_complete_results_and_valid_regime():
    assert ManagerDailyEvidence(date(2026, 10, 27), 4, 3, 1, 0, 0, 1).status == "MET_73"
    assert ManagerDailyEvidence(date(2026, 10, 27), 11, 8, 3, 0, 0, 1).status == "BELOW_73"
    assert ManagerDailyEvidence(date(2026, 10, 27), 5, 4, 0, 0, 1, 1).status == "UNSETTLED"
    assert ManagerDailyEvidence(date(2026, 10, 27), 0, 0, 0, 0, 0, 0).status == "NO_PICKS"
    assert ManagerDailyEvidence(date(2026, 10, 27), 1, 0, 0, 1, 0, 1).status == "NO_GRADED"
    assert ManagerDailyEvidence(date(2026, 10, 27), 4, 3, 1, 0, 0, 2).status == "MIXED_OR_UNKNOWN_MODEL_REGIME"
    assert ManagerDailyEvidence(date(2026, 10, 27), 3, 3, 1, 0, 0, 1).status == "INCONSISTENT"


def test_no_round_up_from_72_point_7_percent():
    row = ManagerDailyEvidence(date(2026, 10, 27), 11, 8, 3, 0, 0, 1)
    assert row.status == "BELOW_73"
    line = render_manager_daily_evidence(row)
    assert "win_rate_pct=72.7" in line
    assert "target=BELOW_73" in line


def test_readonly_query_reuses_original_manager_identity_and_pit_date(db):
    query = manager_daily_query()
    assert "first_model_picks AS" in query
    assert "mv0.as_of <= p.picked_at" in query
    assert "mv0.source_observed_at <= p.picked_at" in query
    assert "mro.as_of DESC" in query
    assert "model_regime_key" in query
    assert "p.aligned_probability" not in query
    assert db.execute(query, (date(2026, 10, 21), date(2026, 10, 28))).fetchall() == []


def test_completed_days_emitted_even_without_any_picks(db):
    rows = load_manager_daily_evidence(db, as_of=datetime(2026, 10, 27, 20, tzinfo=UTC))
    assert len(rows) == 7
    assert rows[0].day == date(2026, 10, 21)
    assert rows[-1].day == date(2026, 10, 27)
    assert all(x.status == "NO_PICKS" for x in rows)
    assert "scope=FIRST_PRODUCTION_MANAGER_ONLY" in render_manager_daily_evidence(rows[-1])
