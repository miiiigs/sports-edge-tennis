from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sports_edge_tennis.odds.providers.api_tennis import (
    build_api_tennis_offered_observation,
)


def test_api_tennis_home_away_maps_first_and_second_player():
    received_at = datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc)
    observation = build_api_tennis_offered_observation(
        provider_event_id="159923",
        event_payload={
            "Home/Away": {
                "Home": {"bet365": "2.50", "bwin": "2.40"},
                "Away": {"bet365": "1.50", "bwin": "1.48"},
            }
        },
        first_player_source_id="1905",
        second_player_source_id="2076",
        received_at=received_at,
        response_id="API_TENNIS_ODDS_V1:test",
    )

    assert observation.provider_code == "API_TENNIS"
    assert observation.provider_event_id == "159923"
    assert observation.observation_time == received_at
    assert {market.provider_bookmaker_key for market in observation.markets} == {"bet365", "bwin"}

    bet365 = next(m for m in observation.markets if m.provider_bookmaker_key == "bet365")
    assert bet365.market_type == "MATCH_WINNER"
    assert bet365.market_status == "OPEN"
    assert bet365.selections[0].provider_player_id == "1905"
    assert bet365.selections[0].provider_selection_key == "Home"
    assert bet365.selections[0].decimal_price == Decimal("2.50")
    assert bet365.selections[1].provider_player_id == "2076"
    assert bet365.selections[1].provider_selection_key == "Away"
    assert bet365.selections[1].decimal_price == Decimal("1.50")


def test_api_tennis_offered_adapter_requires_complete_valid_pair():
    observation = build_api_tennis_offered_observation(
        provider_event_id="159923",
        event_payload={
            "Home/Away": {
                "Home": {"complete": "1.90", "missing-away": "2.10", "bad": "1.00"},
                "Away": {"complete": "1.95", "bad": "not-a-price"},
            }
        },
        first_player_source_id="1",
        second_player_source_id="2",
        received_at=datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc),
        response_id="API_TENNIS_ODDS_V1:test",
    )

    assert len(observation.markets) == 1
    assert observation.markets[0].provider_bookmaker_key == "complete"
