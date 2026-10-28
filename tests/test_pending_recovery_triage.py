"""Read-only pending action routing: separate match, provider and DB blocks."""
from types import SimpleNamespace as NS
import pytest

from sports_edge_tennis.live.pending_recovery_triage import (
    action_for, render_pending_recovery_triage,
)


def item(mid, reason, status="SCHEDULED", flags=()):
    return NS(match_id=mid, reason=reason, flags=flags,
              evidence=NS(latest_match_status=status))


def test_all_actionable_cases_get_exact_guarded_owner_path():
    cases=(
        item(132211,"TERMINAL_FIXTURE_WITHOUT_RESULT","WALKOVER"),
        item(131307,"TERMINAL_FIXTURE_WITHOUT_RESULT","COMPLETED"),
        item(130903,"NO_API_TENNIS_LINK"),
        item(130717,"PROVIDER_FIXTURE_NOT_REOBSERVED",flags=("OUTSIDE_RECOVERY_WINDOW",)),
        item(132800,"PROVIDER_FIXTURE_NOT_REOBSERVED"),
    )
    lines=render_pending_recovery_triage(cases)
    assert "actionable=5" in lines[0]
    assert "BACKUP_GATED_WINNERLESS_WALKOVER" in lines[1]
    assert "VERIFY_TERMINAL_WINNER_EVIDENCE" in lines[2]
    assert "REVIEW_CANONICAL_API_TENNIS_ID" in lines[3]
    assert "VERIFY_HISTORICAL_PROVIDER_EVENT" in lines[4]
    assert "VERIFY_RECENT_PROVIDER_EVENT" in lines[5]
    assert all("no_forced_winner=TRUE" in line for line in lines[1:])


def test_future_and_awaiting_are_never_actionable_or_voided():
    samples=(
        item(1,"NOT_STARTED"),
        item(2,"FIXTURE_POSTPONED","POSTPONED"),
        item(3,"AWAITING_RESULT"),
        item(4,"RESULT_DELAYED"),
    )
    lines=render_pending_recovery_triage(samples)
    assert len(lines)==1
    assert "actionable=0" in lines[0]
    assert "WAIT_NOT_STARTED_OR_POSTPONED:2" in lines[0]
    assert "WAIT_FOR_AUTHORITATIVE_TERMINAL:2" in lines[0]


def test_unknown_reasons_fail_closed_not_grading():
    with pytest.raises(ValueError,match="unknown"):
        action_for(item(4,"UNKNOWN"))
