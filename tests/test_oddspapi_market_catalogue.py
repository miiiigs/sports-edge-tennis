from __future__ import annotations

from copy import deepcopy

from sports_edge_tennis.odds.catalogue import (
    ODDSPAPI_TENNIS_MATCH_WINNER,
    diagnose_payload,
    orientation_evidence,
)


def _payload() -> dict:
    return {
        "fixtureId": "id1200670776914752",
        "sportId": 12,
        "participant1Id": 785974,
        "participant2Id": 169486,
        "participant1Name": "Player One",
        "participant2Name": "Player Two",
        "startTime": "2026-11-27T17:30:00.000Z",
        "updatedAt": "2026-11-27T01:23:59.084Z",
        "bookmakerOdds": {
            "pinnacle+30": {
                "markets": {
                    "121": {
                        "marketActive": True,
                        "bookmakerMarketId": "1",
                        "outcomes": {
                            "121": {"players": {"0": {
                                "price": 1.69, "active": True,
                                "changedAt": "2026-11-27T00:40:07.837Z",
                            }}},
                            "122": {"players": {"0": {
                                "price": 2.02, "active": True,
                                "changedAt": "2026-11-27T00:40:07.837Z",
                            }}},
                        },
                    },
                    "123": {"outcomes": {"1": {}, "2": {}, "3": {}}},
                },
            },
        },
    }


def test_catalogue_identifies_defensive_real_payload_without_alerts():
    payload = _payload()
    alerts = diagnose_payload(payload)
    assert not [a for a in alerts if a.severity == "CRITICAL"]
    evidence = orientation_evidence(
        payload,
        market_id=ODDSPAPI_TENNIS_MATCH_WINNER.market_id,
        outcome_ids=ODDSPAPI_TENNIS_MATCH_WINNER.outcome_ids,
    )
    assert evidence["outcome_ids"] == ["121", "122"]
    assert evidence["orientation_basis"] == "VALIDATED_OUTCOME_ID_ORDER"
    assert evidence["participant_1_provider_id"] == "785974"


def test_changed_outcome_ids_are_critical_not_silently_accepted():
    payload = _payload()
    market = payload["bookmakerOdds"]["pinnacle+30"]["markets"].pop("121")
    market["outcomes"]["999"] = market["outcomes"].pop("122")
    payload["bookmakerOdds"]["pinnacle+30"]["markets"]["121"] = market
    alerts = diagnose_payload(payload)
    codes = {alert.code for alert in alerts}
    assert "OUTCOME_IDS_CHANGED" in codes
    assert "OUTCOME_MISSING" in codes
    assert any(alert.severity == "CRITICAL" for alert in alerts)


def test_tutorial_market_171_is_not_tennis_match_winner():
    payload = _payload()
    markets = payload["bookmakerOdds"]["pinnacle+30"]["markets"]
    markets["171"] = markets.pop("121")
    alerts = diagnose_payload(payload)
    assert any(alert.code == "WINNER_MARKET_ABSENT" and alert.severity == "CRITICAL"
               for alert in alerts)
    assert any("171" in alert.detail for alert in alerts)


def test_unexpected_sport_id_and_missing_bookmaker_payload_fail_closed():
    payload = _payload()
    payload["sportId"] = 1
    alerts = diagnose_payload(payload)
    assert any(alert.code == "UNEXPECTED_SPORT_ID" for alert in alerts)

    empty = {"fixtureId": "x", "sportId": 12}
    alerts = diagnose_payload(empty)
    assert [alert.code for alert in alerts] == ["NO_BOOKMAKER_PAYLOAD"]


def test_historical_shape_is_diagnosed_without_being_live_validated():
    payload = _payload()
    historical = deepcopy(payload)
    historical["bookmakers"] = historical.pop("bookmakerOdds")
    historical["bookmakers"]["pinnacle+30"]["markets"]["121"]["outcomes"] = {
        "121": {"players": {"0": [{"price": 1.69, "active": True}]}},
        "122": {"players": {"0": [{"price": 2.02, "active": True}]}},
    }
    alerts = diagnose_payload(historical)
    # Historical list-valued players are a different schema; the diagnostic
    # reports the non-object state rather than treating the list as a price.
    assert any(alert.code == "NON_OBJECT_PLAYER_STATE" for alert in alerts)
