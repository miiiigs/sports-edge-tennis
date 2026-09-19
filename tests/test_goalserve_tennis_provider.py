from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sports_edge_tennis.odds.providers.goalserve_tennis import (
    GOALSERVE_TENNIS_PROVIDER_CODE,
    build_goalserve_tennis_observation,
)


def _match():
    return {
        "id": "567872",
        "player": [
            {"id": "1716", "name": "M. Copil"},
            {"id": "11483", "name": "D. Schwartzman"},
        ],
        "odds": {
            "ts": "1551201954671",
            "type": [
                {
                    "id": "1",
                    "value": "Home/Away",
                    "bookmaker": [
                        {
                            "id": "18",
                            "name": "Pinnacle",
                            "odd": [
                                {"name": "1", "value": "2.04"},
                                {"name": "2", "value": "1.85"},
                            ],
                        },
                        {
                            "id": "82",
                            "name": "Unibet",
                            "odd": [
                                {"name": "1", "value": "2.10"},
                                {"name": "2", "value": "1.74"},
                            ],
                        },
                    ],
                },
                {
                    "id": "3",
                    "value": "Over/Under",
                    "bookmaker": {
                        "id": "18",
                        "name": "Pinnacle",
                        "odd": [
                            {"name": "Under", "value": "1.91"},
                            {"name": "Over", "value": "1.89"},
                        ],
                    },
                },
            ],
        },
    }


def test_goalserve_home_away_normalizes_complete_books_only() -> None:
    observation = build_goalserve_tennis_observation(
        match_payload=_match(),
        player_1_name="M Copil",
        player_2_name="D Schwartzman",
        player_1_source_id="1716",
        player_2_source_id="11483",
        received_at=datetime(2019, 2, 26, 18, 0, tzinfo=UTC),
        response_id="goalserve-response-1",
    )

    assert observation.provider_code == GOALSERVE_TENNIS_PROVIDER_CODE
    assert observation.provider_event_id == "567872"
    assert {market.provider_bookmaker_key for market in observation.markets} == {
        "18:Pinnacle",
        "82:Unibet",
    }
    pinnacle = next(
        market for market in observation.markets
        if market.provider_bookmaker_key == "18:Pinnacle"
    )
    assert [selection.provider_player_id for selection in pinnacle.selections] == [
        "1716",
        "11483",
    ]
    assert [selection.decimal_price for selection in pinnacle.selections] == [
        Decimal("2.04"),
        Decimal("1.85"),
    ]


def test_goalserve_participant_order_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="PARTICIPANT_ORDER_UNRESOLVED"):
        build_goalserve_tennis_observation(
            match_payload=_match(),
            player_1_name="D Schwartzman",
            player_2_name="M Copil",
            player_1_source_id="11483",
            player_2_source_id="1716",
            received_at=datetime(2019, 2, 26, 18, 0, tzinfo=UTC),
            response_id="goalserve-response-1",
        )


def test_goalserve_incomplete_home_away_book_is_dropped() -> None:
    match = _match()
    match["odds"]["type"][0]["bookmaker"][0]["odd"] = [
        {"name": "1", "value": "2.04"}
    ]
    observation = build_goalserve_tennis_observation(
        match_payload=match,
        player_1_name="M Copil",
        player_2_name="D Schwartzman",
        player_1_source_id="1716",
        player_2_source_id="11483",
        received_at=datetime(2019, 2, 26, 18, 0, tzinfo=UTC),
        response_id="goalserve-response-1",
    )
    assert {market.provider_bookmaker_key for market in observation.markets} == {
        "82:Unibet"
    }


def test_goalserve_singleton_json_containers_are_supported() -> None:
    match = _match()
    match["odds"]["type"] = {
        "id": "1",
        "value": "Home/Away",
        "bookmaker": {
            "id": "18",
            "name": "Pinnacle",
            "odd": [
                {"name": "1", "value": "2.04"},
                {"name": "2", "value": "1.85"},
            ],
        },
    }
    observation = build_goalserve_tennis_observation(
        match_payload=match,
        player_1_name="M Copil",
        player_2_name="D Schwartzman",
        player_1_source_id="1716",
        player_2_source_id="11483",
        received_at=datetime(2019, 2, 26, 18, 0, tzinfo=UTC),
        response_id="goalserve-response-1",
    )
    assert len(observation.markets) == 1
    assert observation.markets[0].provider_bookmaker_key == "18:Pinnacle"
