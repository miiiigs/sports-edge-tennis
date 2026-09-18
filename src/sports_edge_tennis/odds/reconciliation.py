from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Sequence

import psycopg

from .contracts import LiveOddsError, MatchVersionContext
from .persistence import load_pit_match_version


PINNAPI_SOURCE_SYSTEM = "PINNAPI"
MAX_FIRST_MAPPING_START_DELTA = timedelta(minutes=30)
MAX_VARIANT_FIRST_MAPPING_START_DELTA = timedelta(minutes=120)
MAX_UNIQUE_PARTICIPANT_START_DELTA = timedelta(hours=12)
PINNAPI_TOKEN_ALIASES = {
    "dar": "darwin",
}

ReconciliationStatus = Literal["RESOLVED", "UNMATCHED", "IDENTITY_CONFLICT"]
CanonicalSide = Literal["PLAYER_1", "PLAYER_2"]


@dataclass(frozen=True)
class PinnapiEventEvidence:
    provider_event_id: str
    home_name: str
    away_name: str
    scheduled_start: datetime
    league_id: str | None = None
    league_name: str | None = None
    parent_id: str | None = None
    event_type: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("provider_event_id", "home_name", "away_name"):
            if not getattr(self, field_name).strip():
                raise LiveOddsError(f"{field_name} must be non-empty")
        if self.scheduled_start.tzinfo is None:
            raise LiveOddsError("scheduled_start must be timezone-aware")

    @property
    def provider_event_id_text(self) -> str:
        return str(self.provider_event_id).strip()

    @property
    def scheduled_start_utc(self) -> datetime:
        return self.scheduled_start.astimezone(timezone.utc)


@dataclass(frozen=True)
class OrientedPinnapiMatch:
    match_id: int
    match_version_id: int
    provider_event_id: str
    home_canonical_player_id: int
    away_canonical_player_id: int
    home_selection_side: CanonicalSide
    away_selection_side: CanonicalSide


@dataclass(frozen=True)
class PinnapiReconciliationResult:
    status: ReconciliationStatus
    match: OrientedPinnapiMatch | None = None
    reason: str | None = None
    candidate_match_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class _Candidate:
    match: MatchVersionContext
    player_1_name: str
    player_2_name: str
    tournament_name: str
    tournament_source_id: str | None


_CHILD_SUFFIX_RE = re.compile(r"\((Games|Sets)\)\s*$", flags=re.IGNORECASE)


def _has_child_suffix(value: str) -> bool:
    return bool(_CHILD_SUFFIX_RE.search(value.strip()))


def _is_child_or_special_event(evidence: PinnapiEventEvidence) -> bool:
    if evidence.parent_id is not None and evidence.parent_id.strip():
        return True
    if (evidence.event_type or "").strip().casefold() != "prematch":
        return True
    if _has_child_suffix(evidence.home_name) or _has_child_suffix(evidence.away_name):
        return True
    return False


def reconcile_pinnapi_event(
    conn: psycopg.Connection,
    *,
    evidence: PinnapiEventEvidence,
    candidate_match_ids: Sequence[int],
    as_of: datetime,
) -> PinnapiReconciliationResult:
    if as_of.tzinfo is None:
        raise LiveOddsError("as_of must be timezone-aware")
    as_of_utc = as_of.astimezone(timezone.utc)
    if _is_child_or_special_event(evidence):
        return PinnapiReconciliationResult(status="UNMATCHED", reason="child_or_special_event")
    unique_candidate_ids = tuple(dict.fromkeys(int(match_id) for match_id in candidate_match_ids))
    existing_match_id = _existing_pinnapi_mapping(conn, evidence.provider_event_id_text)
    candidates = _load_candidates(conn, candidate_match_ids=unique_candidate_ids, as_of=as_of_utc)

    if existing_match_id is not None:
        if existing_match_id not in unique_candidate_ids:
            return PinnapiReconciliationResult(
                status="IDENTITY_CONFLICT",
                reason="existing_mapping_outside_candidate_scope",
            )
        candidate = candidates.get(existing_match_id)
        if candidate is None:
            return PinnapiReconciliationResult(status="UNMATCHED", reason="no_pit_match_version")
        orientation = _orient_candidate(candidate, evidence)
        # Once a provider event id has been persisted, participant identity is the
        # stable revalidation key. Pinnacle league/tournament labels can change as
        # round or competition wording is refreshed, so do not invalidate a known
        # event solely because mutable tournament text no longer matches. Explicit
        # tour-scope contradictions still fail closed.
        if orientation is None or not _tour_scope_compatible(candidate, evidence):
            return PinnapiReconciliationResult(
                status="IDENTITY_CONFLICT",
                reason="existing_mapping_evidence_conflict",
                candidate_match_ids=(existing_match_id,),
            )
        return PinnapiReconciliationResult(status="RESOLVED", match=orientation)

    strict_matches: list[tuple[_Candidate, OrientedPinnapiMatch]] = []
    variant_time_matches: list[tuple[_Candidate, OrientedPinnapiMatch]] = []
    fallback_matches: list[tuple[_Candidate, OrientedPinnapiMatch]] = []
    participant_match_count = 0
    tour_compatible_count = 0

    for candidate in candidates.values():
        orientation = _orient_candidate(candidate, evidence)
        if orientation is None:
            continue
        participant_match_count += 1

        if not _tour_scope_compatible(candidate, evidence):
            continue
        tour_compatible_count += 1

        tournament_compatible = _tournament_compatible(candidate, evidence)
        if (
            tournament_compatible
            and _start_within_first_mapping_tolerance(candidate.match.match_start_at, evidence)
        ):
            strict_matches.append((candidate, orientation))
            continue

        # Production evidence repeatedly shows a 100-minute Pinnacle start-time shift
        # for a Challenger match while the participant pair still uses only deterministic
        # initial/name variants and both tournament and tour identity agree. Extend only
        # this already-constrained path to 120 minutes; exact-name mappings remain on the
        # original 30-minute first-mapping guard and ambiguity still fails closed.
        if (
            tournament_compatible
            and _participant_pair_uses_deterministic_variant(candidate, evidence, orientation)
            and _start_within_variant_first_mapping_tolerance(
                candidate.match.match_start_at,
                evidence,
            )
        ):
            variant_time_matches.append((candidate, orientation))
            continue

        if (
            not tournament_compatible
            and _start_within_unique_participant_tolerance(
                candidate.match.match_start_at,
                evidence,
            )
        ):
            fallback_matches.append((candidate, orientation))

    first_mapping_matches = strict_matches + variant_time_matches
    if len(first_mapping_matches) > 1:
        return PinnapiReconciliationResult(
            status="IDENTITY_CONFLICT",
            reason="multiple_candidate_matches",
            candidate_match_ids=tuple(
                candidate.match.match_id for candidate, _orientation in first_mapping_matches
            ),
        )
    if len(strict_matches) == 1:
        candidate, orientation = strict_matches[0]
        _persist_pinnapi_mapping(
            conn,
            provider_event_id=evidence.provider_event_id_text,
            candidate=candidate,
        )
        return PinnapiReconciliationResult(status="RESOLVED", match=orientation)
    if len(variant_time_matches) == 1:
        candidate, orientation = variant_time_matches[0]
        _persist_pinnapi_mapping(
            conn,
            provider_event_id=evidence.provider_event_id_text,
            candidate=candidate,
        )
        return PinnapiReconciliationResult(
            status="RESOLVED",
            match=orientation,
            reason="deterministic_participant_variant_time_fallback",
        )

    if len(fallback_matches) > 1:
        return PinnapiReconciliationResult(
            status="IDENTITY_CONFLICT",
            reason="multiple_unique_participant_fallback_candidates",
            candidate_match_ids=tuple(
                candidate.match.match_id for candidate, _orientation in fallback_matches
            ),
        )
    if len(fallback_matches) == 1:
        candidate, orientation = fallback_matches[0]
        _persist_pinnapi_mapping(
            conn,
            provider_event_id=evidence.provider_event_id_text,
            candidate=candidate,
        )
        return PinnapiReconciliationResult(
            status="RESOLVED",
            match=orientation,
            reason="unique_participant_tournament_fallback",
        )

    if participant_match_count == 0:
        reason = "participant_mismatch"
    elif tour_compatible_count == 0:
        reason = "tour_scope_conflict"
    else:
        reason = "start_or_tournament_mismatch"
    return PinnapiReconciliationResult(status="UNMATCHED", reason=reason)


def normalize_pinnapi_participant_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip())
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    punctuation_as_space = re.sub(r"[\W_]+", " ", ascii_text, flags=re.UNICODE)
    return " ".join(punctuation_as_space.casefold().split())


def _participant_tokens(value: str) -> tuple[str, ...]:
    return tuple(normalize_pinnapi_participant_name(value).split())


def _token_compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    if PINNAPI_TOKEN_ALIASES.get(left) == right:
        return True
    if PINNAPI_TOKEN_ALIASES.get(right) == left:
        return True
    if len(left) == 1 and len(right) > 1:
        return right.startswith(left)
    if len(right) == 1 and len(left) > 1:
        return left.startswith(right)
    return False


def _participant_name_compatible(left: str, right: str) -> bool:
    """Match deterministic provider aliases without edit-distance fuzziness.

    Supports punctuation/diacritics, token-order differences, one-character
    initials, and a small explicit source alias table. One extra middle initial
    is accepted only when another token also requires initial expansion and a
    multi-character token matches exactly, keeping surname identity anchored.
    """
    left_tokens = _participant_tokens(left)
    right_tokens = _participant_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True

    if len(left_tokens) == len(right_tokens):
        unmatched = list(right_tokens)
        multiset_resolved = True
        for left_token in left_tokens:
            exact_index = next(
                (index for index, right_token in enumerate(unmatched) if left_token == right_token),
                None,
            )
            if exact_index is not None:
                unmatched.pop(exact_index)
                continue
            compatible_indexes = [
                index
                for index, right_token in enumerate(unmatched)
                if _token_compatible(left_token, right_token)
            ]
            if len(compatible_indexes) != 1:
                multiset_resolved = False
                break
            unmatched.pop(compatible_indexes[0])
        if multiset_resolved and not unmatched:
            return True

        # Repeated initials can make the order-insensitive multiset matcher look
        # ambiguous even when the provider and canonical names are deterministically
        # compatible in their observed token order. Example:
        #   "Jeffrey John Wolf" <-> "J.J. Wolf"
        # Keep this path narrow: same token count, every aligned token compatible,
        # at least one real initial expansion, and at least one exact multi-character
        # anchor (normally the surname). No edit distance or missing-token guess.
        aligned_pairs = tuple(zip(left_tokens, right_tokens))
        if all(_token_compatible(left_token, right_token) for left_token, right_token in aligned_pairs):
            exact_multi_character_anchor = any(
                left_token == right_token and len(left_token) > 1
                for left_token, right_token in aligned_pairs
            )
            used_initial_expansion = any(
                left_token != right_token
                and (
                    (len(left_token) == 1 and len(right_token) > 1)
                    or (len(right_token) == 1 and len(left_token) > 1)
                )
                for left_token, right_token in aligned_pairs
            )
            if exact_multi_character_anchor and used_initial_expansion:
                return True
        return False

    if abs(len(left_tokens) - len(right_tokens)) != 1:
        return False

    shorter, longer = (
        (left_tokens, right_tokens)
        if len(left_tokens) < len(right_tokens)
        else (right_tokens, left_tokens)
    )
    unmatched = list(longer)
    exact_multi_character_match = False
    used_initial_expansion = False
    for short_token in shorter:
        exact_index = next(
            (index for index, long_token in enumerate(unmatched) if short_token == long_token),
            None,
        )
        if exact_index is not None:
            if len(short_token) > 1:
                exact_multi_character_match = True
            unmatched.pop(exact_index)
            continue
        compatible_indexes = [
            index
            for index, long_token in enumerate(unmatched)
            if _token_compatible(short_token, long_token)
        ]
        if len(compatible_indexes) != 1:
            return False
        matched_long_token = unmatched[compatible_indexes[0]]
        if (
            (len(short_token) == 1 and len(matched_long_token) > 1)
            or (len(matched_long_token) == 1 and len(short_token) > 1)
        ):
            used_initial_expansion = True
        unmatched.pop(compatible_indexes[0])

    return (
        exact_multi_character_match
        and used_initial_expansion
        and len(unmatched) == 1
        and len(unmatched[0]) == 1
    )


def _participant_pair_uses_deterministic_variant(
    candidate: _Candidate,
    evidence: PinnapiEventEvidence,
    orientation: OrientedPinnapiMatch,
) -> bool:
    if orientation.home_selection_side == "PLAYER_1":
        pairs = (
            (evidence.home_name, candidate.player_1_name),
            (evidence.away_name, candidate.player_2_name),
        )
    else:
        pairs = (
            (evidence.home_name, candidate.player_2_name),
            (evidence.away_name, candidate.player_1_name),
        )
    return any(
        normalize_pinnapi_participant_name(provider_name)
        != normalize_pinnapi_participant_name(canonical_name)
        for provider_name, canonical_name in pairs
    )


def _load_candidates(
    conn: psycopg.Connection,
    *,
    candidate_match_ids: tuple[int, ...],
    as_of: datetime,
) -> dict[int, _Candidate]:
    loaded: dict[int, _Candidate] = {}
    for match_id in candidate_match_ids:
        match = load_pit_match_version(conn, match_id=match_id, observation_time=as_of)
        if match is None:
            continue
        row = conn.execute(
            """
            SELECT
                p1.canonical_name,
                p2.canonical_name,
                t.canonical_name,
                tsi.source_tournament_id
            FROM matches m
            JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
            JOIN tournaments t ON t.tournament_id = te.tournament_id
            JOIN players p1 ON p1.player_id = %s
            JOIN players p2 ON p2.player_id = %s
            LEFT JOIN tournament_source_ids tsi
              ON tsi.tournament_id = t.tournament_id
             AND tsi.source_system = 'API_TENNIS'
            WHERE m.match_id = %s
            """,
            (match.player_1_id, match.player_2_id, match.match_id),
        ).fetchone()
        if row is None:
            continue
        loaded[match.match_id] = _Candidate(
            match=match,
            player_1_name=row[0],
            player_2_name=row[1],
            tournament_name=row[2],
            tournament_source_id=row[3],
        )
    return loaded


def _orient_candidate(
    candidate: _Candidate,
    evidence: PinnapiEventEvidence,
) -> OrientedPinnapiMatch | None:
    if _participant_name_compatible(candidate.player_1_name, candidate.player_2_name):
        return None
    if _participant_name_compatible(evidence.home_name, evidence.away_name):
        return None

    direct = (
        _participant_name_compatible(evidence.home_name, candidate.player_1_name)
        and _participant_name_compatible(evidence.away_name, candidate.player_2_name)
    )
    reverse = (
        _participant_name_compatible(evidence.home_name, candidate.player_2_name)
        and _participant_name_compatible(evidence.away_name, candidate.player_1_name)
    )
    if direct == reverse:
        return None

    if direct:
        return OrientedPinnapiMatch(
            match_id=candidate.match.match_id,
            match_version_id=candidate.match.match_version_id,
            provider_event_id=evidence.provider_event_id_text,
            home_canonical_player_id=candidate.match.player_1_id,
            away_canonical_player_id=candidate.match.player_2_id,
            home_selection_side="PLAYER_1",
            away_selection_side="PLAYER_2",
        )
    return OrientedPinnapiMatch(
        match_id=candidate.match.match_id,
        match_version_id=candidate.match.match_version_id,
        provider_event_id=evidence.provider_event_id_text,
        home_canonical_player_id=candidate.match.player_2_id,
        away_canonical_player_id=candidate.match.player_1_id,
        home_selection_side="PLAYER_2",
        away_selection_side="PLAYER_1",
    )


def _start_within_first_mapping_tolerance(
    canonical_start_at: datetime | None,
    evidence: PinnapiEventEvidence,
) -> bool:
    return _start_within_tolerance(
        canonical_start_at,
        evidence,
        MAX_FIRST_MAPPING_START_DELTA,
    )


def _start_within_variant_first_mapping_tolerance(
    canonical_start_at: datetime | None,
    evidence: PinnapiEventEvidence,
) -> bool:
    return _start_within_tolerance(
        canonical_start_at,
        evidence,
        MAX_VARIANT_FIRST_MAPPING_START_DELTA,
    )


def _start_within_unique_participant_tolerance(
    canonical_start_at: datetime | None,
    evidence: PinnapiEventEvidence,
) -> bool:
    return _start_within_tolerance(
        canonical_start_at,
        evidence,
        MAX_UNIQUE_PARTICIPANT_START_DELTA,
    )


def _start_within_tolerance(
    canonical_start_at: datetime | None,
    evidence: PinnapiEventEvidence,
    tolerance: timedelta,
) -> bool:
    if canonical_start_at is None:
        return False
    delta = abs(canonical_start_at.astimezone(timezone.utc) - evidence.scheduled_start_utc)
    return delta <= tolerance


def _tour_scope_compatible(candidate: _Candidate, evidence: PinnapiEventEvidence) -> bool:
    league_name = normalize_pinnapi_participant_name(evidence.league_name or "")
    if "challenger" in league_name and candidate.match.tour_scope != "CHALLENGER":
        return False
    if "challenger" not in league_name and candidate.match.tour_scope == "CHALLENGER":
        return "challenger" in normalize_pinnapi_participant_name(candidate.tournament_name)
    return True


def _tournament_compatible(candidate: _Candidate, evidence: PinnapiEventEvidence) -> bool:
    if not _tour_scope_compatible(candidate, evidence):
        return False
    league_name = normalize_pinnapi_participant_name(evidence.league_name or "")
    if league_name:
        tournament_name = normalize_pinnapi_participant_name(candidate.tournament_name)
        if (
            league_name != tournament_name
            and league_name not in tournament_name
            and tournament_name not in league_name
        ):
            return False
    return True


def _existing_pinnapi_mapping(conn: psycopg.Connection, provider_event_id: str) -> int | None:
    rows = conn.execute(
        """
        SELECT match_id
        FROM match_source_ids
        WHERE source_system = %s
          AND source_match_id = %s
        ORDER BY match_source_id
        LIMIT 2
        """,
        (PINNAPI_SOURCE_SYSTEM, provider_event_id),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        raise LiveOddsError("pinnapi_event_identity_conflict")
    return rows[0][0]


def _persist_pinnapi_mapping(
    conn: psycopg.Connection,
    *,
    provider_event_id: str,
    candidate: _Candidate,
) -> None:
    existing = _existing_pinnapi_mapping(conn, provider_event_id)
    if existing is not None:
        if existing != candidate.match.match_id:
            raise LiveOddsError("pinnapi_event_identity_conflict")
        return
    conn.execute(
        """
        INSERT INTO match_source_ids (
            match_id,
            match_version_id,
            source_system,
            source_match_id,
            source_record_locator,
            source_file_name
        )
        VALUES (%s, %s, %s, %s, %s, 'pinnapi_reconciliation')
        ON CONFLICT (source_system, source_match_id) WHERE source_match_id IS NOT NULL DO NOTHING
        """,
        (
            candidate.match.match_id,
            candidate.match.match_version_id,
            PINNAPI_SOURCE_SYSTEM,
            provider_event_id,
            f"pinnapi:event_id:{provider_event_id}",
        ),
    )
    persisted = _existing_pinnapi_mapping(conn, provider_event_id)
    if persisted != candidate.match.match_id:
        raise LiveOddsError("pinnapi_event_identity_conflict")