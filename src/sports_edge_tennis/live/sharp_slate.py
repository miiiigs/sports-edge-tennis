from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Callable, Mapping

import psycopg

from sports_edge_tennis.live.orchestrator import _parse_board, _process_event
from sports_edge_tennis.odds.providers.pinnapi import to_pinnapi_event_evidence
from sports_edge_tennis.odds.reconciliation import (
    MAX_VARIANT_FIRST_MAPPING_START_DELTA,
    _load_candidates,
    _orient_candidate,
    _participant_pair_uses_deterministic_variant,
    _start_within_first_mapping_tolerance,
    _tour_scope_compatible,
    _tournament_compatible,
    normalize_pinnapi_participant_name,
)


_EXTENDED_VARIANT_DIAGNOSTIC_DELTA = timedelta(minutes=120)


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
    log: Callable[[str], None] | None = None,
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
    reason_counts: Counter[str] = Counter()
    candidates = (
        _load_candidates(conn, candidate_match_ids=candidate_match_ids, as_of=as_of)
        if log is not None
        else {}
    )
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
        if log is not None:
            bucket = _diagnostic_bucket(result, event, candidates)
            reason_counts[bucket] += 1
            if result["match_id"] is None:
                log(_reconcile_diagnostic_line(result, event, candidates, bucket))
                extended = _extended_variant_time_candidate(event, candidates)
                if extended is not None:
                    candidate, delta_minutes = extended
                    reason_counts["variant_time_60_120"] += 1
                    log(
                        _extended_variant_time_line(
                            event,
                            candidate,
                            delta_minutes=delta_minutes,
                        )
                    )
    if log is not None:
        for rejected in parse_result.rejected_events:
            reason_counts["other"] += 1
        log(
            "PINNACLE_RECONCILE_SUMMARY "
            f"events={len(parse_result.events)} "
            f"resolved={resolved} "
            f"no_player_pair={reason_counts['no_player_pair']} "
            f"ambiguous_player_pair={reason_counts['ambiguous_player_pair']} "
            f"time_mismatch={reason_counts['time_mismatch']} "
            f"variant_time_60_120={reason_counts['variant_time_60_120']} "
            f"tour_mismatch={reason_counts['tour_mismatch']} "
            f"tournament_mismatch={reason_counts['tournament_mismatch']} "
            f"other={reason_counts['other']}"
        )
    return SharpSlateOutcome(
        candidate_matches=len(candidate_match_ids),
        board_events=len(parse_result.events),
        resolved_events=resolved,
        normalized_events=normalized,
        rejected_events=len(parse_result.rejected_events),
    )


def _diagnostic_bucket(
    result: dict[str, Any],
    event: Any,
    candidates: Mapping[int, Any],
) -> str:
    if result["match_id"] is not None:
        return "resolved"
    if result.get("reconciliation_status") == "IDENTITY_CONFLICT":
        return "ambiguous_player_pair"

    reason = result.get("reconciliation_reason")
    if reason == "participant_mismatch":
        return "no_player_pair"
    if reason == "tour_scope_conflict":
        return "tour_mismatch"
    if reason in {"child_or_special_event", "existing_mapping_outside_candidate_scope", "no_pit_match_version"}:
        return "other"

    evidence = to_pinnapi_event_evidence(event)
    participant_matches = [
        candidate
        for candidate in candidates.values()
        if _orient_candidate(candidate, evidence) is not None
    ]
    if not participant_matches:
        return "no_player_pair"
    tour_matches = [
        candidate
        for candidate in participant_matches
        if _tour_scope_compatible(candidate, evidence)
    ]
    if not tour_matches:
        return "tour_mismatch"
    tournament_matches = [
        candidate
        for candidate in tour_matches
        if _tournament_compatible(candidate, evidence)
    ]
    if tournament_matches and all(
        not _start_within_first_mapping_tolerance(candidate.match.match_start_at, evidence)
        for candidate in tournament_matches
    ):
        return "time_mismatch"
    return "tournament_mismatch"


def _extended_variant_time_candidate(
    event: Any,
    candidates: Mapping[int, Any],
) -> tuple[Any, int] | None:
    """Identify one safe observational candidate between 60 and 120 minutes.

    This deliberately does not persist a mapping. It asks whether the exact existing
    deterministic-variant path would have been unique if its 60-minute guard were
    extended to 120 minutes, while retaining participant, tournament, and tour checks.
    """
    evidence = to_pinnapi_event_evidence(event)
    matches: list[tuple[Any, int]] = []
    for candidate in candidates.values():
        orientation = _orient_candidate(candidate, evidence)
        if orientation is None:
            continue
        if not _tour_scope_compatible(candidate, evidence):
            continue
        if not _tournament_compatible(candidate, evidence):
            continue
        if not _participant_pair_uses_deterministic_variant(candidate, evidence, orientation):
            continue
        start_at = candidate.match.match_start_at
        if start_at is None:
            continue
        delta = abs(start_at.astimezone(evidence.scheduled_start_utc.tzinfo) - evidence.scheduled_start_utc)
        if MAX_VARIANT_FIRST_MAPPING_START_DELTA < delta <= _EXTENDED_VARIANT_DIAGNOSTIC_DELTA:
            matches.append((candidate, int(delta.total_seconds() // 60)))
    if len(matches) != 1:
        return None
    return matches[0]


def _extended_variant_time_line(
    event: Any,
    candidate: Any,
    *,
    delta_minutes: int,
) -> str:
    evidence = to_pinnapi_event_evidence(event)
    canonical_start = candidate.match.match_start_at
    return (
        "PINNACLE_TIME_SHIFT_CANDIDATE "
        f"provider_event_id={_safe_text(evidence.provider_event_id_text)} "
        f"match_id={candidate.match.match_id} "
        f"player1={_quoted(candidate.player_1_name)} "
        f"player2={_quoted(candidate.player_2_name)} "
        f"tournament={_quoted(candidate.tournament_name)} "
        f"canonical_start={canonical_start.isoformat() if canonical_start is not None else '-'} "
        f"provider_start={evidence.scheduled_start_utc.isoformat()} "
        f"delta_minutes={delta_minutes} mapping_changed=0"
    )


def _reconcile_diagnostic_line(
    result: dict[str, Any],
    event: Any,
    candidates: Mapping[int, Any],
    bucket: str,
) -> str:
    evidence = to_pinnapi_event_evidence(event)
    best_candidate, best_score = _best_name_candidate(evidence, candidates)
    best_match_id = "-"
    best_player1 = "-"
    best_player2 = "-"
    time_delta_minutes = "-"
    tournament_match = "-"
    tour_class_match = "-"
    if best_candidate is not None:
        best_match_id = str(best_candidate.match.match_id)
        best_player1 = _safe_text(best_candidate.player_1_name)
        best_player2 = _safe_text(best_candidate.player_2_name)
        if best_candidate.match.match_start_at is not None:
            time_delta_minutes = str(
                int(
                    abs(
                        best_candidate.match.match_start_at.astimezone(evidence.scheduled_start_utc.tzinfo)
                        - evidence.scheduled_start_utc
                    ).total_seconds()
                    // 60
                )
            )
        tournament_match = str(_tournament_compatible(best_candidate, evidence)).lower()
        tour_class_match = str(_tour_scope_compatible(best_candidate, evidence)).lower()

    candidate_ids = result.get("candidate_match_ids") or ()
    candidate_count = len(candidate_ids) if candidate_ids else len(candidates)
    reason = result.get("reconciliation_reason") or bucket
    return (
        "PINNACLE_RECONCILE "
        f"provider_event_id={_safe_text(evidence.provider_event_id_text)} "
        f"status={result.get('reconciliation_status', 'UNMATCHED')} "
        f"reason={reason} "
        f"candidate_count={candidate_count} "
        f"home={_quoted(evidence.home_name)} "
        f"away={_quoted(evidence.away_name)} "
        f"best_match_id={best_match_id} "
        f"best_player1={_quoted(best_player1)} "
        f"best_player2={_quoted(best_player2)} "
        f"time_delta_minutes={time_delta_minutes} "
        f"tournament_match={tournament_match} "
        f"tour_class_match={tour_class_match} "
        f"participant_similarity={best_score:.3f}"
    )


def _best_name_candidate(evidence: Any, candidates: Mapping[int, Any]) -> tuple[Any | None, float]:
    best_candidate = None
    best_score = -1.0
    home = normalize_pinnapi_participant_name(evidence.home_name)
    away = normalize_pinnapi_participant_name(evidence.away_name)
    for candidate in candidates.values():
        p1 = normalize_pinnapi_participant_name(candidate.player_1_name)
        p2 = normalize_pinnapi_participant_name(candidate.player_2_name)
        direct = (_similarity(p1, home) + _similarity(p2, away)) / 2.0
        reverse = (_similarity(p1, away) + _similarity(p2, home)) / 2.0
        score = max(direct, reverse)
        if score > best_score:
            best_score = score
            best_candidate = candidate
    return best_candidate, max(best_score, 0.0)


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _safe_text(value: Any) -> str:
    if value is None:
        return "-"
    return " ".join(str(value).replace("\n", " ").replace("\r", " ").split())[:120] or "-"


def _quoted(value: Any) -> str:
    return '"' + _safe_text(value).replace('"', "'") + '"'
