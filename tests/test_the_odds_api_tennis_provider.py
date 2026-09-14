from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sports_edge_tennis.odds.providers.the_odds_api import (
    THE_ODDS_API_PROVIDER_CODE,
    build_the_odds_api_tennis_observation,
)


def _event():
    return {
        "id": "odds-event-1",
        "home_team": "Carlos Alcaraz",
        "away_team": "Jannik Sinner",
        "commence_time": "2026-09-14T12:00:00Z",
        "bookmakers": [
            {
                "key": "pinnacle",
                "last_update": "2026-09-14T10:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-09-14T10:01:00Z",
                        "outcomes": [
                            {"name": "Carlos Alcaraz", "price": 1.91},
                            {"name": "Jannik Sinner", "price": 1.95},
                        ],
                    }
                ],
            },
            {
                "key": "bet365",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Carlos Alcaraz", "price": "1.90"},
                            {"name": "Jannik Sinner", "price": "1.96"},
                        ],
                    }
                ],
            },
        ],
    }


def test_the_odds_api_tennis_h2h_normalizes_multi_bookmaker_observations() -> None:
    observation = build_the_odds_api_tennis_observation(
        event_payload=_event(),
        player_1_name="Carlos Alcaraz",
        player_2_name="Jannik Sinner",
        player_1_source_id="theodds:carlos",
        player_2_source_id="theodds:jannik",
        received_at=datetime(2026, 9, 14, 10, 2, tzinfo=UTC),
        response_id="response-1",
    )

    assert observation.provider_code == THE_ODDS_API_PROVIDER_CODE
    assert observation.provider_event_id == "odds-event-1"
    assert observation.provider_observed_at == datetime(2026, 9, 14, 10, 1, tzinfo=UTC)
    assert {market.provider_bookmaker_key for market in observation.markets} == {
        "pinnacle",
        "bet365",
    }
    pinnacle = next(
        market for market in observation.markets if market.provider_bookmaker_key == "pinnacle"
    )
    assert pinnacle.provider_market_key == "odds-event-1:pinnacle:h2h"
    assert [selection.decimal_price for selection in pinnacle.selections] == [
        Decimal("1.91"),
        Decimal("1.95"),
    ]


def test_the_odds_api_tennis_fails_closed_on_participant_mismatch() -> None:
    with pytest.raises(ValueError, match="PARTICIPANT_MATCH_UNRESOLVED"):
        build_the_odds_api_tennis_observation(
            event_payload=_event(),
            player_1_name="Carlos Alcaraz",
            player_2_name="Novak Djokovic",
            player_1_source_id="theodds:carlos",
            player_2_source_id="theodds:novak",
            received_at=datetime(2026, 9, 14, 10, 2, tzinfo=UTC),
            response_id="response-1",
        )


def test_the_odds_api_tennis_drops_incomplete_or_duplicate_h2h_market() -> None:
    event = _event()
    event["bookmakers"][0]["markets"][0]["outcomes"] = [
        {"name": "Carlos Alcaraz", "price": 1.91},
        {"name": "Carlos Alcaraz", "price": 1.92},
    ]

    observation = build_the_odds_api_tennis_observation(
        event_payload=event,
        player_1_name="Carlos Alcaraz",
        player_2_name="Jannik Sinner",
        player_1_source_id="theodds:carlos",
        player_2_source_id="theodds:jannik",
        received_at=datetime(2026, 9, 14, 10, 2, tzinfo=UTC),
        response_id="response-1",
    )

    assert {market.provider_bookmaker_key for market in observation.markets} == {"bet365"}
