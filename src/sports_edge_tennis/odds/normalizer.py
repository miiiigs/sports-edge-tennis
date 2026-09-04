from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sports_edge_tennis.data.normalize import normalize_hash_text

from .contracts import (
    BookmakerMapping,
    LiveOddsError,
    LiveOddsIntegrityError,
    MatchVersionContext,
    NormalizedOddsQuote,
    ProviderBookmakerMarket,
    ProviderObservation,
    ProviderSelection,
)


RAW_HASH_VERSION = "SE_LIVE_ODDS_RAW_V1"
SNAPSHOT_ID_VERSION = "SE_LIVE_ODDS_SNAPSHOT_V1"
RECORD_HASH_VERSION = "SE_LIVE_ODDS_RECORD_V1"
_REDACTED = "[REDACTED]"
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "authorization",
    "bearer",
    "client_secret",
    "password",
    "secret",
    "token",
    "cookie",
    "set-cookie",
}


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _canonical_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        normalized = value.normalize()
        text = format(normalized, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return _canonical_value(Decimal(str(value)))
    if isinstance(value, str):
        return normalize_hash_text(value)
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {normalize_hash_text(str(key)): _canonical_value(value[key]) for key in sorted(value, key=str)}
    raise TypeError(f"unsupported canonical value type: {type(value)!r}")


def stable_json(payload: Any) -> str:
    return json.dumps(_canonical_value(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _redact_value(payload)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            normalized_key = str(key).strip().casefold().replace("-", "_")
            redacted[str(key)] = _REDACTED if normalized_key in _SECRET_KEYS else _redact_value(child)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    return value


def payload_hash(redacted_payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(stable_json({"payload": redacted_payload}).encode("utf-8")).hexdigest()
    return f"{RAW_HASH_VERSION}:{digest}"


def raw_payload_and_hash(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    redacted = redact_payload(payload)
    return redacted, payload_hash(redacted)


def source_snapshot_id(
    *,
    observation: ProviderObservation,
    market: ProviderBookmakerMarket,
) -> str:
    payload = {
        "provider_code": observation.provider_code,
        "provider_event_id": observation.provider_event_id,
        "provider_bookmaker_key": market.provider_bookmaker_key,
        "provider_market_key": market.provider_market_key,
        "provider_observation_key": observation.provider_observation_key,
        "market_type": market.market_type,
        "observation_time": observation.observation_time,
    }
    digest = hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()
    return f"{SNAPSHOT_ID_VERSION}:{digest}"


def source_record_hash(
    *,
    observation: ProviderObservation,
    market: ProviderBookmakerMarket,
    selection_type: str,
    mapping: BookmakerMapping,
    price: Decimal,
) -> str:
    payload = {
        "source_snapshot_id": source_snapshot_id(observation=observation, market=market),
        "source_system": mapping.normalization_class,
        "bookmaker_code": mapping.bookmaker_code,
        "selection_type": selection_type,
        "decimal_price": price,
    }
    digest = hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()
    return f"{RECORD_HASH_VERSION}:{digest}"


def source_system_for_mapping(mapping: BookmakerMapping) -> str | None:
    if mapping.normalization_class in ("SHARP", "OFFERED"):
        return mapping.normalization_class
    if mapping.normalization_class == "RAW_ONLY":
        return None
    raise LiveOddsIntegrityError(f"unsupported normalization_class {mapping.normalization_class!r}")


def selection_type_for_player(selection_player_id: int, match: MatchVersionContext) -> str:
    if selection_player_id == match.player_1_id:
        return "PLAYER_1"
    if selection_player_id == match.player_2_id:
        return "PLAYER_2"
    raise LiveOddsError("provider participant is not a canonical match participant")


def validate_supported_market(match: MatchVersionContext, market: ProviderBookmakerMarket) -> None:
    if market.market_type != "MATCH_WINNER":
        raise LiveOddsError("unsupported_market")
    if match.tour_scope not in ("ATP_MAIN", "CHALLENGER"):
        raise LiveOddsError("unsupported_tour_scope")
    if match.competition_tier not in ("GRAND_SLAM", "MASTERS_1000", "ATP_500", "ATP_250", "ATP_TOUR", "CHALLENGER"):
        raise LiveOddsError("unsupported_competition_tier")
    if match.match_status != "SCHEDULED":
        raise LiveOddsError("unsupported_match_status")
    if match.match_start_at is not None and match.match_start_at <= market_time_placeholder():
        raise LiveOddsError("match_not_prematch")


def market_time_placeholder() -> datetime:
    return datetime.min.replace(tzinfo=timezone.utc)


def build_quote(
    *,
    observation: ProviderObservation,
    raw_odds_observation_id: int,
    market: ProviderBookmakerMarket,
    selection: ProviderSelection,
    selection_player_id: int,
    match: MatchVersionContext,
    mapping: BookmakerMapping,
) -> NormalizedOddsQuote:
    source_system = source_system_for_mapping(mapping)
    if source_system is None:
        raise LiveOddsIntegrityError("RAW_ONLY mappings must not create normalized odds")
    if mapping.provider_code != observation.provider_code:
        raise LiveOddsIntegrityError("raw provider and bookmaker mapping provider differ")

    selection_type = selection_type_for_player(selection_player_id, match)
    snapshot_id = source_snapshot_id(observation=observation, market=market)
    observed_at = observation.observation_time
    if match.match_start_at is not None and match.match_start_at <= observed_at:
        raise LiveOddsError("match_not_prematch")
    if market.market_type != "MATCH_WINNER":
        raise LiveOddsError("unsupported_market")
    if market.market_status not in ("OPEN", "SUSPENDED", "CLOSED", "UNKNOWN"):
        raise LiveOddsError("unsupported_status")
    if match.tour_scope not in ("ATP_MAIN", "CHALLENGER"):
        raise LiveOddsError("unsupported_tour_scope")
    if match.competition_tier not in ("GRAND_SLAM", "MASTERS_1000", "ATP_500", "ATP_250", "ATP_TOUR", "CHALLENGER"):
        raise LiveOddsError("unsupported_competition_tier")
    if match.match_status != "SCHEDULED":
        raise LiveOddsError("unsupported_match_status")

    return NormalizedOddsQuote(
        source_system=source_system,  # type: ignore[arg-type]
        bookmaker_code=mapping.bookmaker_code,
        selection_type=selection_type,  # type: ignore[arg-type]
        decimal_price=selection.decimal_price,
        market_status=market.market_status,
        source_snapshot_id=snapshot_id,
        source_record_hash=source_record_hash(
            observation=observation,
            market=market,
            selection_type=selection_type,
            mapping=mapping,
            price=selection.decimal_price,
        ),
        source_observed_at=observed_at,
        as_of=observed_at,
        raw_odds_observation_id=raw_odds_observation_id,
        provider_bookmaker_mapping_id=mapping.provider_bookmaker_mapping_id,
    )

