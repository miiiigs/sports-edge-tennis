from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sports_edge_tennis.odds.contracts import (
    BookmakerMapping,
    LiveOddsError,
    MatchVersionContext,
    ProviderBookmakerMarket,
    ProviderObservation,
    ProviderSelection,
)
from sports_edge_tennis.odds.normalizer import (
    build_quote,
    raw_payload_and_hash,
    source_snapshot_id,
    source_system_for_mapping,
)


def _utc(day: int, hour: int = 12) -> datetime:
    return datetime(2025, 9, day, hour, tzinfo=timezone.utc)


def _selection(player_id: str, price: str = "2.10") -> ProviderSelection:
    return ProviderSelection(provider_player_id=player_id, decimal_price=Decimal(price))


def _market(book: str = "pinnacle", key: str = "match-winner-1") -> ProviderBookmakerMarket:
    return ProviderBookmakerMarket(
        provider_bookmaker_key=book,
        provider_market_key=key,
        market_type="MATCH_WINNER",
        market_status="OPEN",
        selections=(_selection("p1"), _selection("p2", "1.80")),
    )


def _observation(
    *,
    provider: str = "PROVIDER_A",
    observation_key: str = "response-1",
    provider_observed_at: datetime | None = None,
    received_at: datetime = _utc(5),
    payload: dict | None = None,
) -> ProviderObservation:
    return ProviderObservation(
        provider_code=provider,
        provider_event_id="event-1",
        provider_observation_key=observation_key,
        provider_observed_at=provider_observed_at,
        received_at=received_at,
        provider_request_id="request-1",
        provider_response_id=observation_key,
        payload=payload or {"prices": [2.1], "api_key": "secret-value"},
        adapter_version="adapter-test-v1",
        markets=(_market(),),
    )


def _mapping(class_name: str = "SHARP", book: str = "PINNACLE") -> BookmakerMapping:
    return BookmakerMapping(
        provider_bookmaker_mapping_id=10,
        provider_code="PROVIDER_A",
        provider_bookmaker_key="pinnacle",
        mapping_version=1,
        bookmaker_code=book,
        normalization_class=class_name,  # type: ignore[arg-type]
        effective_from=_utc(1),
    )


def _match() -> MatchVersionContext:
    return MatchVersionContext(
        match_id=1,
        match_version_id=2,
        player_1_id=101,
        player_2_id=202,
        match_start_at=_utc(7),
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        match_status="SCHEDULED",
    )


def test_secret_redaction_is_deterministic_and_hashed_after_redaction():
    payload = {
        "api_key": "secret-a",
        "X-API-Key": "x-secret-a",
        "Set-Cookie": "session=a",
        "Proxy-Authorization": "proxy-a",
        "nested": {
            "authorization": "bearer token",
            "Access-Token": "access-a",
            "Refresh-Token": "refresh-a",
            "Client-Secret": "client-a",
            "price": Decimal("2.10"),
        },
    }
    redacted_a, hash_a = raw_payload_and_hash(payload)
    redacted_b, hash_b = raw_payload_and_hash(
        {
            "nested": {
                "price": Decimal("2.10"),
                "authorization": "different",
                "Access-Token": "access-b",
                "Refresh-Token": "refresh-b",
                "Client-Secret": "client-b",
            },
            "api_key": "secret-b",
            "X-API-Key": "x-secret-b",
            "Set-Cookie": "session=b",
            "Proxy-Authorization": "proxy-b",
        }
    )

    assert redacted_a["api_key"] == "[REDACTED]"
    assert redacted_a["X-API-Key"] == "[REDACTED]"
    assert redacted_a["Set-Cookie"] == "[REDACTED]"
    assert redacted_a["Proxy-Authorization"] == "[REDACTED]"
    assert redacted_a["nested"]["authorization"] == "[REDACTED]"
    assert redacted_a["nested"]["Access-Token"] == "[REDACTED]"
    assert redacted_a["nested"]["Refresh-Token"] == "[REDACTED]"
    assert redacted_a["nested"]["Client-Secret"] == "[REDACTED]"
    assert "secret-a" not in str(redacted_a)
    assert "session=a" not in str(redacted_a)
    assert "proxy-a" not in str(redacted_a)
    assert hash_a == hash_b


def test_stable_observation_key_controls_raw_identity_not_payload_hash():
    same_retry = _observation(observation_key="response-1")
    distinct = _observation(observation_key="response-2")

    assert same_retry.provider_observation_key == "response-1"
    assert distinct.provider_observation_key != same_retry.provider_observation_key
    assert raw_payload_and_hash(same_retry.payload)[1] == raw_payload_and_hash(distinct.payload)[1]


def test_source_snapshot_identity_is_provider_and_observation_safe():
    provider_a = _observation(provider="PROVIDER_A", observation_key="response-1")
    provider_b = _observation(provider="PROVIDER_B", observation_key="response-1")
    later_a = _observation(provider="PROVIDER_A", observation_key="response-2")

    assert source_snapshot_id(observation=provider_a, market=provider_a.markets[0]) == source_snapshot_id(
        observation=provider_a, market=provider_a.markets[0]
    )
    assert source_snapshot_id(observation=provider_a, market=provider_a.markets[0]) != source_snapshot_id(
        observation=provider_b, market=provider_b.markets[0]
    )
    assert source_snapshot_id(observation=provider_a, market=provider_a.markets[0]) != source_snapshot_id(
        observation=later_a, market=later_a.markets[0]
    )


def test_two_legs_in_one_coherent_update_share_snapshot_id():
    observation = _observation()
    market = observation.markets[0]
    assert len(market.selections) == 2
    assert source_snapshot_id(observation=observation, market=market) == source_snapshot_id(
        observation=observation,
        market=market,
    )


def test_mapping_class_normalization_boundary():
    assert source_system_for_mapping(_mapping("SHARP")) == "SHARP"
    assert source_system_for_mapping(_mapping("OFFERED")) == "OFFERED"
    assert source_system_for_mapping(_mapping("RAW_ONLY")) is None


def test_unsupported_market_fails_normalization():
    observation = _observation()
    market = ProviderBookmakerMarket(
        provider_bookmaker_key="pinnacle",
        provider_market_key="sets",
        market_type="SET_BETTING",  # type: ignore[arg-type]
        market_status="OPEN",
        selections=(_selection("p1"),),
    )
    with pytest.raises(LiveOddsError):
        build_quote(
            observation=observation,
            raw_odds_observation_id=1,
            market=market,
            selection=market.selections[0],
            selection_player_id=101,
            match=_match(),
            mapping=_mapping(),
        )


def test_participant_side_is_identity_derived_not_list_order():
    observation = _observation()
    quote = build_quote(
        observation=observation,
        raw_odds_observation_id=1,
        market=observation.markets[0],
        selection=observation.markets[0].selections[0],
        selection_player_id=202,
        match=_match(),
        mapping=_mapping(),
    )
    assert quote.selection_type == "PLAYER_2"


def test_receive_time_fallback_semantics():
    observation = _observation(provider_observed_at=None, received_at=_utc(5, 9))
    assert observation.observation_time_basis == "RECEIVE_TIME"
    assert observation.observation_time == _utc(5, 9)

    provider_time = _observation(provider_observed_at=_utc(5, 8), received_at=_utc(5, 9))
    assert provider_time.observation_time_basis == "PROVIDER_TIME"
    assert provider_time.observation_time == _utc(5, 8)
