"""Safety and classification tests for bounded one-off seasonal provider audit."""
from collections import Counter, defaultdict
from datetime import UTC, datetime
from sports_edge_tennis.validation import seasonal_provider_audit as audit


def _fixture(event_key, event_type_type, when, *, qualification="false", name="Test Open"):
    return {
        "event_key": event_key, "tournament_key": "4901",
        "first_player_key": "P1", "second_player_key": "P2",
        "event_first_player": "Test A", "event_second_player": "Test B",
        "event_type_type": event_type_type, "tournament_name": name,
        "event_date": when, "event_time": "12:00",
        "event_qualification": qualification,
        "event_status": "Not Started",
    }


def test_exact_two_windows_and_seven_attempt_cap():
    assert (audit.END - audit.START).days == 49
    assert audit.MAX_REQUESTS == 7
    assert len(audit.HISTORICAL_DATES) == 4


def test_weekly_provider_dedup_and_itf_exclusion():
    received = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
    rows = [
        _fixture("1", "Challenger Men Singles", "2026-11-18"),
        _fixture("1", "Challenger Men Singles", "2026-11-18"),
        _fixture("2", "Atp Singles", "2026-11-19", qualification="true"),
        _fixture("3", "Itf Men Singles", "2026-11-19"),
        _fixture("4", "Atp Singles", "2026-11-20", name="ATP Finals"),
    ]
    stats = defaultdict(Counter)
    audit._scan_window(rows, received, set(), stats)
    ch = stats[("2026-11-16", "CHALLENGER")]
    atp = stats[("2026-11-16", "ATP_TOUR")]
    assert ch["provider_unique"] == 1
    assert ch["scheduled_forward_candidates"] == 1
    assert atp["provider_unique"] == 2
    assert atp["qualifying"] == 1
    assert atp["special_scope_review"] == 1
    assert atp["scheduled_forward_candidates"] == 1
    assert all(k[1] in ("ATP_TOUR", "CHALLENGER") for k in stats)


def test_unresolved_draw_and_special_events_are_not_accepted_automatically():
    assert audit._draw_kind({}) == "UNKNOWN"
    assert audit._draw_kind({"event_qualification": "True"}) == "QUALIFYING"
    assert audit._special({"tournament_name": "United Cup Brisbane"})
    assert not audit._special({"tournament_name": "ATP Challenger Helsinki"})


def test_no_predictions_db_mutations_or_extra_odds_clients():
    import inspect
    src = inspect.getsource(audit)
    assert "requests.post(" not in src
    assert ".execute(\"INSERT" not in src
    assert "ODDSPAPI" not in src
    assert "MAX_REQUESTS = 7" in src
