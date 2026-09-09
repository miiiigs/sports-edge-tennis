from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

import psycopg

from sports_edge_tennis.live.orchestrator import _parse_board, _process_event


@dataclass(frozen=True)
class SharpSlateOutcome:
    candidate_matches: int
    board_events: int
    resolved_events: int
    normalized_events: int
    rejected_events: int


def normalize_pinnapi_board_for_matches(
    conn: psycopg.Connection,
    *,
    board_payload: Mapping[str, Any],
    received_at: datetime,
    board_observation_id: str,
    candidate_match_ids: tuple[int, ...],
    as_of: datetime,
) -> SharpSlateOutcome:
    """Normalize one already-fetched Pinnacle board against the full model slate.

    The caller reuses the exact board observation id used by the live orchestrator,
    so reprocessing consensus events is idempotent while previously unreconciled
    events can be matched to non-consensus targets for Type B scanning.
    """
    parse_result = _parse_board(
        dict(board_payload),
        received_at,
        board_observation_id,
    )
    resolved = 0
    normalized = 0
    for event in parse_result.events:
        result = _process_event(
            conn,
            event,
            parse_result.board_identity,
            received_at,
            candidate_match_ids,
            as_of,
        )
        if result["match_id"] is not None:
            resolved += 1
        if result["normalized"]:
            normalized += 1
    return SharpSlateOutcome(
        candidate_matches=len(candidate_match_ids),
        board_events=len(parse_result.events),
        resolved_events=resolved,
        normalized_events=normalized,
        rejected_events=len(parse_result.rejected_events),
    )
