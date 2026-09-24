from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sports_edge_tennis.market.policy import is_fresh
from sports_edge_tennis.odds.normalizer import source_snapshot_id
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    ODDSPAPI_PROVIDER_CODE,
    build_oddspapi_tennis_observation,
    extract_oddspapi_match_winner_prices,
)


def _price(price, changed_at="2026-09-21T12:00:00Z", active=True):
    return {
        "active": active,
        "price": price,
        "changedAt": changed_at,
        "bookmakerChangedAt": changed_at,
    }


def _event():
    return {
        "fixtureId": "id1200000000000001",
        "participant1Id": 101,
        "participant2Id": 202,
        "sportId": 12,
        "tournamentId": 303,
        "startTime": "2026-09-21T15:00:00Z",
        "updatedAt": "2026-09-21T12:00:00Z",
        "bookmakerOdds": {
            "pinnacle+30": {
                "suspended": False,
                "markets": {
                    "121": {
                        "marketActive": True,
                        "outcomes": {
                            "121": {"players": {"0": _price(1.80)}},
                            "122": {"players": {"0": _price(2.10)}},
                        },
                    }
                },
            },
            "bet365": {
                "suspended": False,
                "markets": {
                    "121": {
                        "marketActive": True,
                        "outcomes": {
                            "121": {
                                "players": {
                                    "0": _price(
                                        "1.83",
                                        "2026-09-21T11:59:00Z",
                                    )
                                }
                            },
                            "122": {
                                "players": {
                                    "0": _price(
                                        "2.06",
                                        "2026-09-21T11:59:00Z",
                                    )
                                }
                            },
                        },
                    }
                },
            },
        },
    }


def test_builds_match_winner_markets_for_plan_books() -> None:
    observation = build_oddspapi_tennis_observation(
        event_payload=_event(),
        received_at=datetime(2026, 9, 21, 12, 1, tzinfo=UTC),
        response_id="response-1",
    )
    assert observation.provider_code == ODDSPAPI_PROVIDER_CODE
    assert observation.provider_event_id == "id1200000000000001"
    assert observation.provider_observed_at is None
    assert observation.observation_time_basis == "RECEIVE_TIME"
    assert observation.observation_time == datetime(2026, 9, 21, 12, 1, tzinfo=UTC)
    assert {
        market.provider_bookmaker_key for market in observation.markets
    } == {"pinnacle+30", "bet365"}
    pinnacle = next(
        market
        for market in observation.markets
        if market.provider_bookmaker_key == "pinnacle+30"
    )
    assert [selection.provider_player_id for selection in pinnacle.selections] == [
        "101",
        "202",
    ]
    assert [selection.decimal_price for selection in pinnacle.selections] == [
        Decimal("1.8"),
        Decimal("2.1"),
    ]


def test_single_book_filter_keeps_observation_at_receive_time() -> None:
    observation = build_oddspapi_tennis_observation(
        event_payload=_event(),
        received_at=datetime(2026, 9, 21, 12, 1, tzinfo=UTC),
        response_id="response-1:BOOK:bet365",
        allowed_bookmakers=("bet365",),
    )
    assert len(observation.markets) == 1
    assert observation.markets[0].provider_bookmaker_key == "bet365"
    assert observation.provider_observed_at is None
    assert observation.observation_time == datetime(2026, 9, 21, 12, 1, tzinfo=UTC)
    assert set(observation.payload["bookmakerOdds"]) == {"bet365"}


def test_stable_price_changed_at_is_not_used_as_observation_time() -> None:
    """Regression for match 130870: a pinnacle price quote last changed ~66 min
    before it was fetched must be observed at receive time, not at changedAt.
    """
    received_at = datetime(2026, 9, 24, 8, 7, 40, 587438, tzinfo=UTC)
    event = _event()
    event["bookmakerOdds"]["pinnacle+30"]["markets"]["121"]["outcomes"]["121"]["players"]["0"][
        "changedAt"
    ] = "2026-09-24T07:01:46.922Z"
    event["bookmakerOdds"]["pinnacle+30"]["markets"]["121"]["outcomes"]["121"]["players"]["0"][
        "bookmakerChangedAt"
    ] = "2026-09-24T07:01:16.457Z"
    event["bookmakerOdds"]["pinnacle+30"]["markets"]["121"]["outcomes"]["122"]["players"]["0"][
        "changedAt"
    ] = "2026-09-24T07:01:46.922Z"
    event["bookmakerOdds"]["pinnacle+30"]["markets"]["121"]["outcomes"]["122"]["players"]["0"][
        "bookmakerChangedAt"
    ] = "2026-09-24T07:01:16.457Z"

    observation = build_oddspapi_tennis_observation(
        event_payload=event,
        received_at=received_at,
        response_id="response-130870:BOOK:pinnacle+30",
        allowed_bookmakers=("pinnacle+30",),
    )
    assert observation.provider_observed_at is None
    assert observation.observation_time == received_at
    age_seconds = (received_at - observation.observation_time).total_seconds()
    assert age_seconds == 0


def test_stable_price_is_fresh_at_receive_time_not_changed_at() -> None:
    """The market observation (and therefore the snapshot identity) must advance
    with each poll even when the price itself has not changed. A price whose
    ``changedAt`` is 66 minutes old is still fresh when observed just now.
    """
    received_at = datetime(2026, 9, 24, 8, 7, 40, 587438, tzinfo=UTC)
    event = _event()
    for outcome_id in ("121", "122"):
        event["bookmakerOdds"]["pinnacle+30"]["markets"]["121"]["outcomes"][outcome_id]["players"]["0"][
            "changedAt"
        ] = "2026-09-24T07:01:46.922Z"

    later = received_at + timedelta(minutes=20)
    first = build_oddspapi_tennis_observation(
        event_payload=event,
        received_at=received_at,
        response_id="poll-1:BOOK:pinnacle+30",
        allowed_bookmakers=("pinnacle+30",),
    )
    second = build_oddspapi_tennis_observation(
        event_payload=event,
        received_at=later,
        response_id="poll-1:BOOK:pinnacle+30",
        allowed_bookmakers=("pinnacle+30",),
    )
    assert is_fresh(first.observation_time, received_at, 15) is True
    assert is_fresh(first.observation_time, received_at + timedelta(minutes=16), 15) is False
    assert source_snapshot_id(
        observation=first, market=first.markets[0]
    ) != source_snapshot_id(observation=second, market=second.markets[0])


def test_incomplete_or_inactive_winner_market_is_not_emitted() -> None:
    event = _event()
    event["bookmakerOdds"]["pinnacle+30"]["markets"]["121"]["outcomes"][
        "122"
    ]["players"]["0"]["active"] = False
    observation = build_oddspapi_tennis_observation(
        event_payload=event,
        received_at=datetime(2026, 9, 21, 12, 1, tzinfo=UTC),
        response_id="response-1",
        allowed_bookmakers=("pinnacle+30",),
    )
    assert observation.markets == ()


def test_extract_match_winner_prices_requires_complete_open_market() -> None:
    event = _event()
    assert extract_oddspapi_match_winner_prices(
        event,
        bookmaker="pinnacle+30",
    ) == (Decimal("1.8"), Decimal("2.1"))
    event["bookmakerOdds"]["pinnacle+30"]["suspended"] = True
    assert (
        extract_oddspapi_match_winner_prices(
            event,
            bookmaker="pinnacle+30",
        )
        is None
    )
