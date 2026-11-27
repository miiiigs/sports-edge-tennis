"""API-Tennis request-timezone, draw and first-issue cutoff verification."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from sports_edge_tennis.data.sources.api_tennis_live import (
    ApiTennisSourceAError,
    _match_start_at,
)
from sports_edge_tennis.live import history_refresh, transport
from sports_edge_tennis.live.api_tennis_draw import (
    BYE,
    LUCKY_LOSER,
    QUALIFIER,
    REPLACEMENT_DETECTION,
    SEEDED,
    TBD,
    WILDCARD,
    classify_seed_label,
    summarize_draw,
)
from sports_edge_tennis.live.transport import (
    fetch_api_tennis_draw,
    fetch_api_tennis_fixtures,
)
from sports_edge_tennis.validation.fixture_lifecycle import select_prematch_targets


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_fixture_request_explicitly_asks_for_utc(monkeypatch):
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append(params)
        return _Response({"success": 1, "result": []})

    monkeypatch.setattr(transport.requests, "get", fake_get)
    fetch_api_tennis_fixtures(
        "secret", date(2027, 1, 19), clock=lambda: datetime(2026, 11, 26, tzinfo=UTC)
    )
    assert calls[0]["timezone"] == "UTC"
    assert calls[0]["method"] == "get_fixtures"


def test_draw_request_explicitly_asks_for_utc_and_qualification(monkeypatch):
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append(params)
        return _Response({"success": 1, "result": {"brackets": []}})

    monkeypatch.setattr(transport.requests, "get", fake_get)
    fetch_api_tennis_draw(
        "secret", "1236", 2027, clock=lambda: datetime(2026, 11, 26, tzinfo=UTC)
    )
    assert calls[0]["timezone"] == "UTC"
    assert calls[0]["include_qualification"] == "1"
    assert calls[0]["method"] == "get_draw"


def test_non_utc_fixture_request_is_rejected_before_the_provider_call(monkeypatch):
    def exploding_get(*args, **kwargs):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(transport.requests, "get", exploding_get)
    with pytest.raises(ValueError, match="must use UTC"):
        fetch_api_tennis_fixtures("secret", date(2027, 1, 19), source_timezone="Europe/Berlin")
    with pytest.raises(ValueError, match="must use UTC"):
        history_refresh.fetch_api_tennis_fixture_window(
            "secret",
            date(2027, 1, 19),
            date(2027, 1, 19),
            source_timezone="Europe/Berlin",
        )


def test_history_window_request_still_uses_explicit_utc(monkeypatch):
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append(params)
        return _Response({"success": 1, "result": []})

    monkeypatch.setattr(history_refresh.requests, "get", fake_get)
    history_refresh.fetch_api_tennis_fixture_window(
        "secret", date(2027, 1, 19), date(2027, 1, 19)
    )
    assert calls[0]["timezone"] == "UTC"


def test_parsed_match_start_is_timezone_aware_utc():
    start = _match_start_at(event_date="2027-01-19", event_time="02:00")
    assert start.tzinfo is not None
    assert start.utcoffset() == timedelta(0)
    assert start == datetime(2027, 1, 19, 2, 0, tzinfo=UTC)


def test_parser_rejects_an_offset_instead_of_silently_reinterpreting_it():
    with pytest.raises(
        ApiTennisSourceAError, match="event_time_must_be_utc_without_offset"
    ):
        _match_start_at(event_date="2027-01-19", event_time="02:00+01:00")


def test_european_dst_transitions_would_corrupt_an_assumed_utc_parser():
    berlin = ZoneInfo("Europe/Berlin")
    # Spring forward 2026-03-29: 01:30 CET = 00:30 UTC; 03:30 CEST = 01:30 UTC.
    assert datetime(2026, 3, 29, 1, 30, tzinfo=berlin).astimezone(UTC) == datetime(
        2026, 3, 29, 0, 30, tzinfo=UTC
    )
    assert datetime(2026, 3, 29, 3, 30, tzinfo=berlin).astimezone(UTC) == datetime(
        2026, 3, 29, 1, 30, tzinfo=UTC
    )
    # Fall back 2026-10-25: 02:30 occurs twice, one hour apart in UTC.
    first = datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=0).astimezone(UTC)
    second = datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=1).astimezone(UTC)
    assert first == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert second == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert first != second


def test_australian_open_local_final_time_converts_to_utc():
    melbourne = ZoneInfo("Australia/Melbourne")
    local = datetime(2027, 1, 31, 19, 30, tzinfo=melbourne)
    assert local.utcoffset() == timedelta(hours=11)  # AEDT
    assert local.astimezone(UTC) == datetime(2027, 1, 31, 8, 30, tzinfo=UTC)


def test_first_issue_cutoff_is_exactly_thirty_minutes_before_start():
    as_of = datetime(2027, 1, 19, 0, 0, tzinfo=UTC)
    exactly = SimpleNamespace(
        match_id=1,
        match_status="SCHEDULED",
        match_start_at=as_of + timedelta(minutes=30),
    )
    too_late = SimpleNamespace(
        match_id=2,
        match_status="SCHEDULED",
        match_start_at=as_of + timedelta(minutes=29, seconds=59),
    )
    started = SimpleNamespace(
        match_id=3,
        match_status="SCHEDULED",
        match_start_at=as_of - timedelta(seconds=1),
    )
    eligible, excluded = select_prematch_targets(
        [exactly, too_late, started], as_of=as_of
    )
    assert eligible == [1]
    assert {row["match_id"] for row in excluded} == {2, 3}
    assert all(
        row["reason"] == "START_INSIDE_OR_AFTER_DEADLINE" for row in excluded
    )


def test_draw_seed_labels_distinguish_qualifier_wildcard_lucky_loser_and_seed():
    assert classify_seed_label("Q") == QUALIFIER
    assert classify_seed_label("WC") == WILDCARD
    assert classify_seed_label("LL") == LUCKY_LOSER
    assert classify_seed_label("12") == SEEDED
    assert classify_seed_label(None) == "UNCLASSIFIED"


def test_draw_summary_reconciles_single_snapshot_fields_without_inference():
    payload = {
        "success": 1,
        "result": {
            "tournament": {
                "tournament_key": "1236",
                "tournament_name": "Australian Open",
                "tournament_surface": "Hard",
                "tournament_season": "2027",
            },
            "source": "draw_feed",
            "brackets": [
                {
                    "stage": "Singles - Main",
                    "qualification": False,
                    "draw_size": 128,
                    "rounds": [
                        {
                            "round_name": "1/64-finals",
                            "matches": [
                                {
                                    "draw_key": 1,
                                    "match_number": 1,
                                    "status": "BYE",
                                    "first_player": {
                                        "player_key": 10,
                                        "name": "Seed One",
                                        "seed": "1",
                                    },
                                    "second_player": None,
                                    "winner_player_key": None,
                                    "next_slot_key": 9,
                                },
                                {
                                    "draw_key": 2,
                                    "match_number": 2,
                                    "status": "Finished",
                                    "first_player": {
                                        "player_key": 11,
                                        "name": "Qualifier",
                                        "seed": "Q",
                                    },
                                    "second_player": {
                                        "player_key": 12,
                                        "name": "Wild Card",
                                        "seed": "WC",
                                    },
                                    "winner_player_key": 11,
                                    "next_slot_key": 9,
                                },
                                {
                                    "draw_key": 3,
                                    "match_number": 3,
                                    "status": "TBA",
                                    "first_player": None,
                                    "second_player": None,
                                    "winner_player_key": None,
                                    "next_slot_key": None,
                                },
                            ],
                        }
                    ],
                },
                {
                    "stage": "Singles - Qualification",
                    "qualification": True,
                    "draw_size": 128,
                    "rounds": [
                        {
                            "round_name": "1/64-finals",
                            "matches": [
                                {
                                    "match_number": 1,
                                    "status": "Scheduled",
                                    "first_player": {
                                        "player_key": 21,
                                        "name": "Lucky Loser",
                                        "seed": "LL",
                                    },
                                    "second_player": {
                                        "player_key": 22,
                                        "name": "Unseeded",
                                        "seed": "",
                                    },
                                }
                            ],
                        }
                    ],
                },
            ],
        },
    }
    summary = summarize_draw(payload)
    assert summary.tournament_key == "1236"
    assert summary.tournament_name == "Australian Open"
    assert summary.tournament_surface == "Hard"
    assert summary.source == "draw_feed"
    assert summary.bracket_count == 2
    assert summary.counts[BYE] == 1
    assert summary.counts[QUALIFIER] == 1
    assert summary.counts[WILDCARD] == 1
    assert summary.counts[LUCKY_LOSER] == 1
    assert summary.counts[SEEDED] == 1
    assert summary.counts[TBD] == 1
    # A single snapshot cannot prove a replacement transition.
    assert REPLACEMENT_DETECTION == "REQUIRES_DRAW_DIFF"


def test_draw_summary_rejects_a_non_draw_payload():
    with pytest.raises(TypeError, match="brackets"):
        summarize_draw({"success": 1, "result": {"tournament": {}}})
