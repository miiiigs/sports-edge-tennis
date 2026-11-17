"""Exact provider ID and conservative terminal-reconciliation safety."""
from datetime import date
from sports_edge_tennis.validation import aged_pending_provider_audit as a


def test_bounded_historical_windows_are_disjoint_and_max_31_days():
    spans = a._windows({date(2026,9,26),date(2026,10,5),
                        date(2026,11,8),date(2026,11,13)})
    assert len(spans) == 2
    assert spans[0][0] == date(2026,9,26)
    assert spans[-1][0] == date(2026,11,8)
    assert all((end-start).days < 31 for start,end in spans)


def test_no_match_disappearance_can_be_turned_into_void():
    assert a._assessment(missing_source=False,provider_rows=[]) == "EVENT_NOT_RETURNED_DO_NOT_AUTO_VOID"
    assert a._assessment(missing_source=True,provider_rows=None) == "NO_CANONICAL_API_TENNIS_LINK"


def test_completely_verified_completed_fixture_is_only_a_review_candidate():
    row={"event_status":"Finished","event_winner":"First Player"}
    assert a._assessment(missing_source=False,provider_rows=[row]) == "TERMINAL_WITH_WINNER_CANONICAL_REVIEW"
    assert a._assessment(missing_source=False,provider_rows=[row,row]) == "MULTIPLE_PROVIDER_ROWS_MANUAL_IDENTITY_REVIEW"


def test_winnerless_walkover_requires_source_based_policy_review():
    assert a._assessment(missing_source=False,provider_rows=[{
        "event_status":"Walkover","event_winner":None
    }]) == "TERMINAL_WALKOVER_CONFIRM_VOID_POLICY"


def test_no_model_changing_or_provider_odds_requests():
    import inspect
    source=inspect.getsource(a)
    assert "UPDATE " not in source and "INSERT " not in source
    assert "ODDSPAPI" not in source
    assert a.MAX_CALLS == 4
