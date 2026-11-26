"""Synthetic regression fixtures only; no supplier capability evidence."""
import pytest
from sports_edge_tennis.validation.commercial_procurement_audit import audit, budget


def fixture():
    return {"fixture_id": "a", "tour": "ATP_MAIN", "checkpoints": {
        "1h": {"cutoff": "2026-01-01T12:00:00Z", "scheduled_start": "2026-01-01T13:00:00Z",
               "schedule_known_at": "2026-01-01T10:00:00Z"}}}


def quote(**changes):
    return dict({"fixture_id": "a", "received_at": "2026-01-01T11:50:00Z",
                 "quote_at": "2026-01-01T11:49:00Z", "archive_only": False,
                 "identity_status": "VERIFIED", "identity_verified_at": "2026-01-01T11:00:00Z",
                 "bookmaker_verified": True, "bookmaker": "test", "market": "MATCH_WINNER",
                 "active": True, "p1": 1.5, "p2": 2.6}, **changes)


def cell(rows, roster=None):
    return audit([fixture()] if roster is None else roster, rows)["by_tour"]["ATP_MAIN"]["1h"]


def test_full_denominator_and_no_archive_promotion():
    assert cell([quote()])["valid_fresh_pair_rate"] == 1
    assert cell([quote(archive_only=True)])["valid_fresh_pair_rate"] == 0
    second = dict(fixture(), fixture_id="b")
    assert cell([quote()], [fixture(), second])["valid_fresh_pair_rate"] == .5


@pytest.mark.parametrize("change", [
    {"received_at": "2026-01-01T12:01:00Z"},
    {"quote_at": None}, {"quote_at": "2026-01-01T12:01:00Z"},
    {"quote_at": "2026-01-01T10:00:00Z"}, {"identity_status": "INCORRECT"},
    {"identity_verified_at": "2026-01-01T12:01:00Z"}, {"bookmaker_verified": False},
    {"p2": 1}, {"p1": float("nan")}, {"active": False}, {"market": "SET_WINNER"}])
def test_rejects_invalid_observations(change):
    assert cell([quote(**change)])["valid_fresh_pair_rate"] == 0


def test_latest_suspended_and_ambiguous_do_not_resurrect():
    assert cell([quote(), quote(received_at="2026-01-01T11:55:00Z", active=False)])["valid_fresh_pair_rate"] == 0
    assert cell([quote(), quote()])["valid_fresh_pair_rate"] == 0


def test_bad_roster_and_missing_schedule():
    with pytest.raises(ValueError):
        audit([fixture(), fixture()], [])
    with pytest.raises(ValueError):
        audit([dict(fixture(), tour="ITF")], [])
    assert cell([], [dict(fixture(), checkpoints={})])["missing_checkpoint"] == 1


def test_request_planning():
    assert budget()["fixture_checkpoint_calls"] == 1120
    assert budget(160, 4, 20)["historical_calls"] == 320
    assert budget(160, 4, 20)["tournament_calls"] == 80
    assert budget()["historical_min_spacing_seconds"] == 795


def test_later_mapping_or_bookmaker_failure_does_not_resurrect():
    for change in ({"identity_status": "INCORRECT"}, {"bookmaker_verified": False}):
        assert cell([quote(), quote(received_at="2026-01-01T11:55:00Z", **change)])["valid_fresh_pair_rate"] == 0
