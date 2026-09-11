from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

import psycopg

from sports_edge_tennis.odds.providers.api_tennis import (
    API_TENNIS_PROVIDER_CODE,
    build_api_tennis_offered_observation,
    new_api_tennis_odds_response_id,
)
from sports_edge_tennis.odds.service import ingest_provider_observation


class ApiTennisOfferedOddsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApiTennisOfferedOddsOutcome:
    candidate_matches: int
    events_with_odds: int
    raw_observations: int
    normalized_quotes: int
    missing_events: int
    bookmaker_count: int


def ingest_api_tennis_offered_odds(
    conn: psycopg.Connection,
    *,
    odds_result: Mapping[str, Mapping[str, Any]],
    received_at: datetime,
    candidate_match_ids: tuple[int, ...],
    response_id: str | None = None,
) -> ApiTennisOfferedOddsOutcome:
    """Persist API-Tennis pre-match Home/Away prices as OFFERED truth.

    Only candidate Manager-consensus matches are considered. API-Tennis
    ``Home`` is oriented to Source A first player and ``Away`` to Source A
    second player, using the already-persisted API_TENNIS match/player source
    identities. Missing or incomplete price pairs fail closed.
    """
    if received_at.tzinfo is None:
        raise ApiTennisOfferedOddsError("received_at must be timezone-aware")
    response_id = response_id or new_api_tennis_odds_response_id(received_at=received_at)

    events_with_odds = 0
    raw_observations = 0
    normalized_quotes = 0
    missing_events = 0
    bookmakers: set[str] = set()

    for match_id in candidate_match_ids:
        identity = _load_api_tennis_identity(conn, match_id=match_id, as_of=received_at)
        if identity is None:
            missing_events += 1
            continue
        event_id, first_player_source_id, second_player_source_id = identity
        event_payload = odds_result.get(event_id)
        if not isinstance(event_payload, Mapping):
            missing_events += 1
            continue

        observation = build_api_tennis_offered_observation(
            provider_event_id=event_id,
            event_payload=event_payload,
            first_player_source_id=first_player_source_id,
            second_player_source_id=second_player_source_id,
            received_at=received_at,
            response_id=response_id,
        )
        events_with_odds += 1
        raw_observations += 1

        for market in observation.markets:
            ensure_api_tennis_offered_mapping(
                conn,
                provider_bookmaker_key=market.provider_bookmaker_key,
                effective_from=received_at,
            )
            bookmakers.add(market.provider_bookmaker_key)

        reconciliation = ingest_provider_observation(conn, observation)
        normalized_quotes += reconciliation.normalized_inserted + reconciliation.normalized_existing

    return ApiTennisOfferedOddsOutcome(
        candidate_matches=len(candidate_match_ids),
        events_with_odds=events_with_odds,
        raw_observations=raw_observations,
        normalized_quotes=normalized_quotes,
        missing_events=missing_events,
        bookmaker_count=len(bookmakers),
    )


def ensure_api_tennis_offered_mapping(
    conn: psycopg.Connection,
    *,
    provider_bookmaker_key: str,
    effective_from: datetime,
) -> int:
    key = provider_bookmaker_key.strip()
    if not key:
        raise ApiTennisOfferedOddsError("provider_bookmaker_key must be non-empty")

    existing = conn.execute(
        """
        SELECT provider_bookmaker_mapping_id, bookmaker_code, normalization_class::text
        FROM provider_bookmaker_mappings
        WHERE provider_code = %s AND provider_bookmaker_key = %s
        ORDER BY effective_from DESC, provider_bookmaker_mapping_id DESC
        LIMIT 1
        """,
        (API_TENNIS_PROVIDER_CODE, key),
    ).fetchone()
    if existing is not None:
        mapping_id, existing_bookmaker_code, normalization_class = existing
        if normalization_class != "OFFERED":
            raise ApiTennisOfferedOddsError(
                f"existing API_TENNIS/{key} mapping is not OFFERED"
            )
        if not str(existing_bookmaker_code).strip():
            raise ApiTennisOfferedOddsError(
                f"existing API_TENNIS/{key} mapping has an empty bookmaker code"
            )
        return mapping_id

    bookmaker_code = _bookmaker_code(key)
    return conn.execute(
        """
        INSERT INTO provider_bookmaker_mappings (
            provider_code, provider_bookmaker_key, mapping_version,
            bookmaker_code, normalization_class, effective_from
        )
        VALUES (%s, %s, 1, %s, 'OFFERED', %s)
        RETURNING provider_bookmaker_mapping_id
        """,
        (API_TENNIS_PROVIDER_CODE, key, bookmaker_code, effective_from),
    ).fetchone()[0]


def _bookmaker_code(provider_bookmaker_key: str) -> str:
    code = re.sub(r"[^A-Za-z0-9]+", "_", provider_bookmaker_key).strip("_").upper()
    if not code:
        raise ApiTennisOfferedOddsError("bookmaker key cannot be normalized")
    return code


def _load_api_tennis_identity(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of: datetime,
) -> tuple[str, str, str] | None:
    row = conn.execute(
        """
        SELECT
            msi.source_match_id,
            p1.source_player_id,
            p2.source_player_id
        FROM match_source_ids msi
        JOIN LATERAL (
            SELECT player_1_id, player_2_id
            FROM match_versions
            WHERE match_id = msi.match_id
              AND as_of <= %s
              AND source_observed_at <= %s
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        JOIN player_source_ids p1
          ON p1.player_id = mv.player_1_id
         AND p1.source_system = %s
        JOIN player_source_ids p2
          ON p2.player_id = mv.player_2_id
         AND p2.source_system = %s
        WHERE msi.match_id = %s
          AND msi.source_system = %s
        ORDER BY msi.match_source_id DESC
        LIMIT 1
        """,
        (
            as_of,
            as_of,
            API_TENNIS_PROVIDER_CODE,
            API_TENNIS_PROVIDER_CODE,
            match_id,
            API_TENNIS_PROVIDER_CODE,
        ),
    ).fetchone()
    if row is None:
        return None
    return str(row[0]), str(row[1]), str(row[2])
