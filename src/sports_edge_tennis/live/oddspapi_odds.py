from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

import psycopg

from sports_edge_tennis.live.oddspapi import parse_oddspapi_tennis_fixture
from sports_edge_tennis.live.oddspapi_reconciliation import (
    OddspapiReconciliationError,
    reconcile_oddspapi_event,
)
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    build_oddspapi_tennis_observation,
    new_oddspapi_response_id,
)
from sports_edge_tennis.odds.service import ingest_provider_observation


@dataclass(frozen=True)
class OddspapiOddsIngestOutcome:
    candidate_matches: int
    provider_events_seen: int
    exact_events: int
    existing_events: int
    unmatched_events: int
    ambiguous_events: int
    conflict_events: int
    normalized_quotes: int
    unknown_bookmaker_groups: int
    unknown_participant_groups: int


def ingest_oddspapi_tennis_odds(
    conn: psycopg.Connection,
    *,
    payload_rows: Sequence[Mapping[str, Any]],
    received_at: datetime,
    candidate_match_ids: tuple[int, ...],
    response_id: str | None = None,
) -> OddspapiOddsIngestOutcome:
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    response_id = response_id or new_oddspapi_response_id(received_at=received_at)

    exact = existing = unmatched = ambiguous = conflicts = 0
    normalized_quotes = unknown_bookmaker = unknown_participant = 0
    seen = 0
    seen_fixture_ids: set[str] = set()

    for payload in payload_rows:
        envelope = parse_oddspapi_tennis_fixture(payload)
        if envelope.fixture_id in seen_fixture_ids:
            continue
        seen_fixture_ids.add(envelope.fixture_id)
        seen += 1
        resolution = reconcile_oddspapi_event(
            conn,
            envelope=envelope,
            candidate_match_ids=candidate_match_ids,
            as_of=received_at,
            persist=True,
        )
        if resolution.status == "UNMATCHED":
            unmatched += 1
            continue
        if resolution.status == "AMBIGUOUS":
            ambiguous += 1
            continue
        if resolution.status == "CONFLICT":
            conflicts += 1
            continue
        if resolution.status == "EXACT":
            exact += 1
        elif resolution.status == "EXISTING":
            existing += 1
        else:
            raise OddspapiReconciliationError(
                f"unsupported OddsPapi reconciliation status {resolution.status!r}"
            )

        observation = build_oddspapi_tennis_observation(
            event_payload=payload,
            received_at=received_at,
            response_id=response_id,
        )
        result = ingest_provider_observation(conn, observation)
        normalized_quotes += result.normalized_inserted + result.normalized_existing
        unknown_bookmaker += result.unknown_bookmaker
        unknown_participant += result.unknown_participant

    return OddspapiOddsIngestOutcome(
        candidate_matches=len(candidate_match_ids),
        provider_events_seen=seen,
        exact_events=exact,
        existing_events=existing,
        unmatched_events=unmatched,
        ambiguous_events=ambiguous,
        conflict_events=conflicts,
        normalized_quotes=normalized_quotes,
        unknown_bookmaker_groups=unknown_bookmaker,
        unknown_participant_groups=unknown_participant,
    )


__all__ = ["OddspapiOddsIngestOutcome", "ingest_oddspapi_tennis_odds"]
