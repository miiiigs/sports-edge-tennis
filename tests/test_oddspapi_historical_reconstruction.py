"""Negative regression tests for OddsPapi live/historical normalization."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    build_oddspapi_tennis_observation,
    reconstruct_historical_winner,
    resolve_winner_market,
    select_historical_state,
)


def _t(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 26, hour, minute, tzinfo=UTC)


def _entry(price, created, *, active=True, changed=None, provider_changed=None):
    return {"createdAt": created, "price": price, "active": active,
            "limit": 1000, "changedAt": provider_changed,
            "bookmakerChangedAt": changed}


def _history(*, market_id="121", outcomes=("121", "122"), p1=None, p2=None):
    p1 = [_entry(1.80, "2026-11-26T10:00:00Z")] if p1 is None else p1
    p2 = [_entry(2.10, "2026-11-26T10:00:00Z")] if p2 is None else p2
    return {
        "fixtureId": "id1200000000000001",
        "participant1Id": 101,
        "participant2Id": 202,
        "startTime": "2026-11-26T18:00:00Z",
        "bookmakers": {
            "pinnacle+30": {
                "markets": {
                    market_id: {
                        "marketActive": True,
                        "outcomes": {
                            outcomes[0]: {"players": {"0": p1}},
                            outcomes[1]: {"players": {"0": p2}},
                        },
                    }
                }
            }
        },
    }


def test_latest_state_at_cutoff_is_selected_not_first_entry():
    payload = _history(
        p1=[_entry(1.80, "2026-11-26T10:00:00Z"),
            _entry(2.00, "2026-11-26T12:00:00Z")])
    early = reconstruct_historical_winner(payload, cutoff=_t(11))
    late = reconstruct_historical_winner(payload, cutoff=_t(13))
    assert float(early[0]["prices"][0]) == pytest.approx(1.80)
    assert float(late[0]["prices"][0]) == pytest.approx(2.00)
    assert late[0]["reconstructed_at"] == _t(12)


def test_suspended_latest_state_is_never_replaced_by_an_earlier_active_price():
    payload = _history(
        p1=[_entry(1.80, "2026-11-26T10:00:00Z"),
            _entry(1.80, "2026-11-26T12:00:00Z", active=False)])
    state = reconstruct_historical_winner(payload, cutoff=_t(13))
    assert state[0]["market_status"] == "SUSPENDED"
    assert state[0]["states"] == ["SUSPENDED", "ACTIVE"]
    assert float(state[0]["prices"][0]) == pytest.approx(1.80)


def test_reversed_participant_order_keeps_prices_attached_to_participants():
    payload = _history(
        p1=[_entry(1.80, "2026-11-26T10:00:00Z")],
        p2=[_entry(2.10, "2026-11-26T10:00:00Z")])
    normal = reconstruct_historical_winner(payload, cutoff=_t(13))[0]
    swapped = _history(
        p1=[_entry(1.80, "2026-11-26T10:00:00Z")],
        p2=[_entry(2.10, "2026-11-26T10:00:00Z")])
    swapped["participant1Id"], swapped["participant2Id"] = 202, 101
    reversed_state = reconstruct_historical_winner(swapped, cutoff=_t(13))[0]
    assert normal["participant_ids"] == ["101", "202"]
    assert reversed_state["participant_ids"] == ["202", "101"]
    assert normal["prices"] == reversed_state["prices"]


def test_missing_second_price_is_not_a_two_sided_market():
    payload = _history(p2=[])
    assert reconstruct_historical_winner(payload, cutoff=_t(13)) == []


def test_duplicate_observations_use_timestamp_then_source_position():
    payload = _history(
        p1=[_entry(1.70, "2026-11-26T12:00:00Z"),
            _entry(1.90, "2026-11-26T12:00:00Z")])
    state = reconstruct_historical_winner(payload, cutoff=_t(13))[0]
    assert float(state["prices"][0]) == pytest.approx(1.90)


def test_invalid_decimal_odds_fail_closed():
    for bad in (1.0, 0, -2, "abc", None):
        payload = _history(
            p1=[_entry(bad, "2026-11-26T10:00:00Z")])
        assert reconstruct_historical_winner(payload, cutoff=_t(13)) == []


def test_post_cutoff_observations_are_not_used():
    payload = _history(
        p1=[_entry(1.80, "2026-11-26T19:00:00Z")],
        p2=[_entry(2.10, "2026-11-26T19:00:00Z")])
    assert reconstruct_historical_winner(payload, cutoff=_t(13)) == []


def test_missing_bookmaker_timestamp_is_rejected_not_assumed_fresh():
    payload = _history(
        p1=[{"price": 1.80, "active": True}],
        p2=[_entry(2.10, "2026-11-26T10:00:00Z")])
    assert reconstruct_historical_winner(payload, cutoff=_t(13)) == []


def test_incorrect_market_type_is_not_silently_accepted_as_tennis():
    payload = _history(market_id="171", outcomes=("171", "172"))
    assert reconstruct_historical_winner(payload, cutoff=_t(13)) == []
    assert resolve_winner_market(
        payload["bookmakers"]["pinnacle+30"]["markets"]) is None


def test_catalogue_override_is_explicit_and_validated():
    payload = _history(market_id="171", outcomes=("171", "172"))
    state = reconstruct_historical_winner(
        payload, cutoff=_t(13),
        catalogue_market_id="171", catalogue_outcome_ids=("171", "172"))
    assert state[0]["market_id"] == "171"
    assert state[0]["market_status"] == "OPEN"


def test_incoherent_two_sided_state_is_suspended_not_fabricated_open():
    payload = _history(
        p1=[_entry(1.80, "2026-11-26T10:00:00Z", active=True)],
        p2=[_entry(2.10, "2026-11-26T10:00:00Z", active=False)])
    state = reconstruct_historical_winner(payload, cutoff=_t(13))[0]
    assert state["market_status"] == "SUSPENDED"
    assert state["states"] == ["ACTIVE", "SUSPENDED"]


def test_historical_list_shape_is_not_read_as_a_live_price():
    live = {
        "fixtureId": "id1200000000000001",
        "participant1Id": 101,
        "participant2Id": 202,
        "bookmakerOdds": {
            "pinnacle+30": {
                "suspended": False,
                "markets": {
                    "121": {
                        "marketActive": True,
                        "outcomes": {
                            "121": {"players": {"0": [_entry(1.8, "2026-11-26T10:00:00Z")]}},
                            "122": {"players": {"0": [_entry(2.1, "2026-11-26T10:00:00Z")]}},
                        },
                    }
                },
            }
        },
    }
    observation = build_oddspapi_tennis_observation(
        event_payload=live, received_at=_t(11), response_id="r")
    assert observation.markets == ()


def test_numeric_json_keys_are_normalized():
    live = {
        "fixtureId": "id1200000000000001",
        "participant1Id": 101,
        "participant2Id": 202,
        "bookmakerOdds": {
            "pinnacle+30": {
                "suspended": False,
                "markets": {
                    "121": {
                        "marketActive": True,
                        "outcomes": {
                            "121": {"players": {"0": {"active": True, "price": 1.8}}},
                            "122": {"players": {"0": {"active": True, "price": 2.1}}},
                        },
                    }
                },
            }
        },
    }
    observation = build_oddspapi_tennis_observation(
        event_payload=live, received_at=_t(11), response_id="r")
    assert len(observation.markets) == 1
    assert [float(s.decimal_price) for s in observation.markets[0].selections] == [
        pytest.approx(1.8), pytest.approx(2.1)]


def test_select_historical_state_exposes_time_bases_separately():
    state = select_historical_state(
        [_entry(1.8, "2026-11-26T10:00:00Z",
                changed="2026-11-26T09:58:00Z",
                provider_changed="2026-11-26T09:59:00Z")], _t(11))
    assert state["created_at"] == _t(10)
    assert state["provider_changed_at"] == _t(9, 59)
    assert state["bookmaker_changed_at"] == _t(9, 58)
