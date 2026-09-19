from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

import psycopg

from sports_edge_tennis.live.goalserve import (
    GoalserveTennisMatchEnvelope,
    iter_goalserve_tennis_envelopes,
)
from sports_edge_tennis.live.goalserve_reconciliation import (
    GoalserveReconciliationError,
    apply_goalserve_identity_bridge,
    reconcile_goalserve_event,
)
from sports_edge_tennis.odds.providers.goalserve_tennis import (
    build_goalserve_tennis_observation,
    new_goalserve_tennis_response_id,
)
from sports_edge_tennis.odds.service import ingest_provider_observation


@dataclass(frozen=True)
class GoalserveOddsIngestOutcome:
    candidate_matches: int
    provider_events_seen: int
    exact_events: int
    existing_events: int
    unmatched_events: int
    ambiguous_events: int
    raw_observations: int
    normalized_quotes: int
    unknown_bookmaker_groups: int
    unknown_participant_groups: int


def ingest_goalserve_tennis_odds(
    conn: psycopg.Connection,
    *,
    payload: Mapping[str, Any],
    received_at: datetime,
    candidate_match_ids: tuple[int, ...],
    response_id: str | None = None,
) -> GoalserveOddsIngestOutcome:
    """Reconcile Goalserve events to the canonical slate, then reuse live odds normalization.

    No Goalserve event or player identity is persisted unless the event resolves exactly
    to one candidate canonical match. Unknown bookmaker mappings remain raw-only.
    """
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")

    response_id = response_id or new_goalserve_tennis_response_id(
        received_at=received_at
    )
    envelopes = iter_goalserve_tennis_envelopes(payload)
    exact_events = 0
    existing_events = 0
    unmatched_events = 0
    ambiguous_events = 0
    raw_observations = 0
    normalized_quotes = 0
    unknown_bookmaker_groups = 0
    unknown_participant_groups = 0

    for envelope in envelopes:
        resolution = reconcile_goalserve_event(
            conn,
            envelope=envelope,
            candidate_match_ids=candidate_match_ids,
            as_of=received_at,
        )
        if resolution.status == "UNMATCHED":
            unmatched_events += 1
            continue
        if resolution.status == "AMBIGUOUS":
            ambiguous_events += 1
            continue
        if resolution.status == "EXACT":
            exact_events += 1
        elif resolution.status == "EXISTING":
            existing_events += 1
        else:  # defensive against future status expansion
            raise GoalserveReconciliationError(
                f"unsupported Goalserve reconciliation status {resolution.status!r}"
            )

        apply_goalserve_identity_bridge(
            conn,
            envelope=envelope,
            resolution=resolution,
        )
        player_1_name, player_2_name, player_1_id, player_2_id = _provider_orientation(
            conn,
            envelope=envelope,
            match_id=int(resolution.match_id),
            as_of=received_at,
        )
        observation = build_goalserve_tennis_observation(
            match_payload=envelope.match_payload,
            player_1_name=player_1_name,
            player_2_name=player_2_name,
            player_1_source_id=player_1_id,
            player_2_source_id=player_2_id,
            received_at=received_at,
            response_id=response_id,
        )
        raw_observations += 1
        reconciled = ingest_provider_observation(conn, observation)
        normalized_quotes += (
            reconciled.normalized_inserted + reconciled.normalized_existing
        )
        unknown_bookmaker_groups += reconciled.unknown_bookmaker
        unknown_participant_groups += reconciled.unknown_participant

    return GoalserveOddsIngestOutcome(
        candidate_matches=len(candidate_match_ids),
        provider_events_seen=len(envelopes),
        exact_events=exact_events,
        existing_events=existing_events,
        unmatched_events=unmatched_events,
        ambiguous_events=ambiguous_events,
        raw_observations=raw_observations,
        normalized_quotes=normalized_quotes,
        unknown_bookmaker_groups=unknown_bookmaker_groups,
        unknown_participant_groups=unknown_participant_groups,
    )


def _provider_orientation(
    conn: psycopg.Connection,
    *,
    envelope: GoalserveTennisMatchEnvelope,
    match_id: int,
    as_of: datetime,
) -> tuple[str, str, str, str]:
    """Return provider-order names/ids after verifying both Goalserve player links.

    The adapter consumes provider order, while the odds normalizer resolves those
    stable provider player IDs back to canonical player IDs.
    """
    row = conn.execute(
        """
        SELECT
            mv.player_1_id,
            mv.player_2_id,
            p1.source_player_id,
            p2.source_player_id
        FROM LATERAL (
            SELECT player_1_id, player_2_id
            FROM match_versions
            WHERE match_id = %s
              AND source_observed_at <= %s
              AND as_of <= %s
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv
        JOIN player_source_ids p1
          ON p1.player_id = mv.player_1_id
         AND p1.source_system = 'GOALSERVE_TENNIS'
        JOIN player_source_ids p2
          ON p2.player_id = mv.player_2_id
         AND p2.source_system = 'GOALSERVE_TENNIS'
        """,
        (match_id, as_of, as_of),
    ).fetchone()
    if row is None:
        raise GoalserveReconciliationError(
            "Goalserve player bridge is incomplete for reconciled match"
        )

    canonical_p1, canonical_p2, canonical_p1_goalserve, canonical_p2_goalserve = row
    provider_pair = (envelope.player_1_id, envelope.player_2_id)
    canonical_pair = (str(canonical_p1_goalserve), str(canonical_p2_goalserve))
    if provider_pair == canonical_pair:
        return (
            envelope.player_1_name,
            envelope.player_2_name,
            envelope.player_1_id,
            envelope.player_2_id,
        )
    if provider_pair == canonical_pair[::-1]:
        # Keep provider order for Home/Away selections. The stable source IDs
        # still resolve to the correct canonical players downstream.
        return (
            envelope.player_1_name,
            envelope.player_2_name,
            envelope.player_1_id,
            envelope.player_2_id,
        )
    raise GoalserveReconciliationError(
        "Goalserve provider participants differ from persisted player bridge"
    )


__all__ = [
    "GoalserveOddsIngestOutcome",
    "ingest_goalserve_tennis_odds",
]
