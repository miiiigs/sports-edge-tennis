"""Production three-day dashboard scorecard must preserve void/pending denominators."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from sports_edge_tennis.validation.live_three_day_scorecard import (
    _overview_rows, pending_reason,
)

NOW=datetime(2026,11,23,15,0,tzinfo=UTC)


def pick(status, *, fixture="NOT_STARTED", start=None):
    return SimpleNamespace(result_status=status,latest_fixture_status=fixture,
                           latest_match_start_at=start,match_start_at=start)


def test_four_outcome_states_are_mutually_exclusive_and_accuracy_uses_scored_only():
    observed=[pick(s) for s in ("WON","WON","LOST","VOID","PENDING")]
    x=_overview_rows(observed)
    assert x=={"issued":5,"wins":2,"losses":1,"pending":1,
               "void":1,"settled":3,"settled_accuracy":2/3}
    assert _overview_rows([pick("PENDING")])["settled_accuracy"] is None


def test_reconciliation_distinguishes_future_delayed_and_terminal_without_result():
    assert pending_reason(pick("PENDING",start=NOW+timedelta(hours=3)),NOW)=="FUTURE_SCHEDULED"
    assert pending_reason(pick("PENDING",start=NOW-timedelta(hours=3)),NOW)=="PAST_SCHEDULED_AWAITING_TERMINAL_RESULT"
    assert pending_reason(pick("PENDING",fixture="POSTPONED",start=NOW-timedelta(hours=3)),NOW)=="POSTPONED_PROVIDER_STATUS"
    assert pending_reason(pick("PENDING",fixture="COMPLETED",start=NOW-timedelta(hours=3)),NOW)=="TERMINAL_FIXTURE_NO_RESULT_OBSERVATION"
    assert pending_reason(pick("PENDING"),NOW)=="MISSING_SCHEDULED_START"
    assert pending_reason(pick("VOID"),NOW)=="NOT_PENDING"


def test_unrecognized_results_fail_closed():
    with pytest.raises(ValueError,match="unknown"):
        _overview_rows([pick("RETIRED")])
