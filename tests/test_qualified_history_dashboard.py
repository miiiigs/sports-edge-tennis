"""Read-only historical qualified vs original scorecard contract tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from jinja2 import Environment
from pathlib import Path

from sports_edge_tennis.dashboard.qualified_history import (
    START_DAY, daily_history_payload, qualified_history_sql, summarize_rows,
)


def test_from_oct27_preserves_all_issued_originals_and_shadow_outcomes():
    rows = [
        (date(2026, 10, 27), 101, Decimal("0.80"), "WON"),
        (date(2026, 10, 27), 102, Decimal("0.65"), "LOST"),
        (date(2026, 10, 27), 103, Decimal("0.70"), "PENDING"),
        (date(2026, 10, 28), 104, Decimal("0.74"), "LOST"),
        (date(2026, 10, 28), 105, Decimal("0.79"), "WON"),
    ]
    days = summarize_rows(rows, as_of_date=date(2026, 10, 29))
    assert START_DAY == date(2026, 10, 27)
    assert [d.day for d in days] == [
        date(2026, 10, 29), date(2026, 10, 28), date(2026, 10, 27)
    ]
    old = days[2]
    assert (old.original, old.original_won, old.original_lost, old.original_pending) == (3, 1, 1, 1)
    assert (old.qualified, old.qualified_won, old.qualified_lost, old.qualified_pending) == (2, 1, 0, 1)
    assert (old.avoided_losses, old.missed_winners) == (1, 0)
    assert old.original_accuracy == 0.5
    assert old.qualified_accuracy == 1.0
    assert not old.qualified_passes_daily_goal
    assert days[0].complete is False
    assert days[0].original_accuracy is None
    assert days[1].qualified_accuracy == 0.5


def test_no_rows_keeps_entire_calendar_range_without_fake_matches():
    days = summarize_rows([], as_of_date=date(2026, 10, 28))
    assert len(days) == 2
    assert all(d.original == d.qualified == d.original_won == d.qualified_won == 0 for d in days)
    assert all(d.original_accuracy is None and d.qualified_accuracy is None for d in days)
    payload = daily_history_payload(days)
    assert payload["research_only"] is True
    assert payload["live_selection_changed"] is False
    assert payload["start_date"] == "2026-10-27"


@pytest.mark.parametrize("rows", [
    [(date(2026, 10, 27), 1, "NaN", "WON")],
    [(date(2026, 10, 27), 1, "1.3", "WON")],
    [(date(2026, 10, 27), 1, "0.7", "INFERRED")],
    [(date(2026, 10, 27), 1, "0.7", "WON"),
     (date(2026, 10, 27), 1, "0.7", "LOST")],
    [(date(2026, 10, 26), 1, "0.7", "WON")],
])
def test_bad_or_duplicate_records_fail_closed(rows):
    with pytest.raises(ValueError):
        summarize_rows(rows, as_of_date=date(2026, 10, 28))


def test_qualified_daily_sql_is_read_only_and_uses_original_pre_match_confidence():
    sql = qualified_history_sql()
    assert "WITH" in sql and "aligned_model_picks" in sql
    assert "p.aligned_probability" in sql
    assert "source_observed_at <= p.picked_at" in sql
    assert "match_result_observations" in sql
    assert "INSERT" not in sql and "UPDATE" not in sql and "DELETE" not in sql
    assert "BET" not in sql


def test_template_exposes_daily_comparison_without_replacing_original_scorecard():
    html = (Path(__file__).parents[1] /
        "src/sports_edge_tennis/dashboard/templates/model_pick_results.html").read_text()
    Environment().parse(html)
    assert "Original vs. Qualified ≥70%" in html
    assert "since 27 Oct 2026" in html
    assert "All aligned five-day acceptance" in html
    assert "qualified_parallel_history_days" in html
