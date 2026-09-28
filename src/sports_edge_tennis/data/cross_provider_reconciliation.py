"""Read-only cross-provider reconciliation evaluator.

Policy ``cross_provider_exact_match_v1`` decides whether a target canonical match and
an evidence canonical match are safely the same real sporting event, and how the
evidence terminal result would translate onto the target match.

This module performs ZERO writes. It only issues SELECT statements. The atomic apply
step (persisting provenance + appending an equivalent target result) lives in a later
service and is intentionally not implemented here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

POLICY_VERSION = "cross_provider_exact_match_v1"
MAX_START_DELTA_MINUTES = 15

CLASSIFICATION_EXACT = "EXACT_CROSS_PROVIDER_MATCH"
CLASSIFICATION_REVERSED = "PLAYER_ORDER_REVERSED_EXACT"

PLAYER_ORDER_DIRECT = "DIRECT"
PLAYER_ORDER_REVERSED = "REVERSED"

TERMINAL_RESULT_STATUSES = ("COMPLETED", "RETIRED", "WALKOVER", "CANCELLED")

# Fail-closed rejection reasons owned by the evaluator.
REASON_MATCH_NOT_FOUND = "MATCH_NOT_FOUND"
REASON_IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
REASON_TOURNAMENT_MISMATCH = "TOURNAMENT_MISMATCH"
REASON_SEASON_MISMATCH = "SEASON_MISMATCH"
REASON_SCOPE_MISMATCH = "SCOPE_MISMATCH"
REASON_TIER_MISMATCH = "TIER_MISMATCH"
REASON_TIME_MISMATCH = "TIME_MISMATCH"
REASON_SOURCE_IDENTITY_MISSING = "SOURCE_IDENTITY_MISSING"
REASON_SOURCE_IDENTITY_AMBIGUOUS = "SOURCE_IDENTITY_AMBIGUOUS"
REASON_RESULT_MISSING = "RESULT_MISSING"

# Apply statuses.
STATUS_APPLIED = "APPLIED"
STATUS_ALREADY_RECONCILED = "ALREADY_RECONCILED"
STATUS_EXISTING_EQUIVALENT_RESULT = "EXISTING_EQUIVALENT_RESULT"
STATUS_UNSAFE_EVALUATION = "UNSAFE_EVALUATION"
STATUS_RESULT_CONFLICT = "RESULT_CONFLICT"


@dataclass(frozen=True)
class CrossProviderReconciliationEvaluation:
    """Immutable evidence bundle produced by the evaluator."""

    target_match_id: int
    evidence_match_id: int
    apply_safe: bool
    rejection_reason: str | None = None
    classification: str | None = None
    player_order_mapping: str | None = None

    target_player_1_id: int | None = None
    target_player_2_id: int | None = None
    evidence_player_1_id: int | None = None
    evidence_player_2_id: int | None = None

    target_tournament_id: int | None = None
    evidence_tournament_id: int | None = None
    target_tournament_edition_id: int | None = None
    evidence_tournament_edition_id: int | None = None
    target_season: int | None = None
    evidence_season: int | None = None
    target_start_at: datetime | None = None
    evidence_start_at: datetime | None = None
    start_delta_minutes: float | None = None
    target_tour_scope: str | None = None
    evidence_tour_scope: str | None = None
    target_competition_tier: str | None = None
    evidence_competition_tier: str | None = None

    target_source_system: str | None = None
    target_source_match_id: str | None = None
    evidence_source_system: str | None = None
    evidence_source_match_id: str | None = None

    evidence_result_observation_id: int | None = None
    evidence_outcome_status: str | None = None
    evidence_winner_side: int | None = None
    evidence_retired_side: int | None = None
    evidence_sets_won_player_1: int | None = None
    evidence_sets_won_player_2: int | None = None
    evidence_games_won_player_1: int | None = None
    evidence_games_won_player_2: int | None = None
    evidence_scoreline_text: str | None = None
    evidence_source_observed_at: datetime | None = None
    evidence_as_of: datetime | None = None

    translated_winner_side: int | None = None
    translated_retired_side: int | None = None
    translated_sets_won_player_1: int | None = None
    translated_sets_won_player_2: int | None = None
    translated_games_won_player_1: int | None = None
    translated_games_won_player_2: int | None = None


@dataclass(frozen=True)
class _MatchEvidence:
    match_version_id: int
    player_1_id: int
    player_2_id: int
    match_start_at: datetime
    tour_scope: str
    competition_tier: str
    tournament_edition_id: int
    tournament_id: int
    season_year: int


@dataclass(frozen=True)
class _SourceEvidence:
    source_system: str
    source_match_id: str


@dataclass(frozen=True)
class _TerminalResult:
    observation_id: int
    outcome_status: str
    winner_side: int | None
    retired_side: int | None
    sets_won_player_1: int | None
    sets_won_player_2: int | None
    games_won_player_1: int | None
    games_won_player_2: int | None
    scoreline_text: str | None
    source_observed_at: datetime
    as_of: datetime


def _load_match_evidence(
    conn: psycopg.Connection, match_id: int
) -> _MatchEvidence | None:
    """Load the deterministic latest persisted fixture for a match.

    Ordering: latest ``as_of`` first, then latest ``source_observed_at``, then the
    highest ``match_version_id`` so repeated calls are stable.
    """
    row = conn.execute(
        """
        SELECT
            mv.match_version_id,
            mv.player_1_id,
            mv.player_2_id,
            mv.match_start_at,
            mv.tour_scope::text,
            mv.competition_tier::text,
            te.tournament_edition_id,
            te.tournament_id,
            te.season_year
        FROM match_versions mv
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te
            ON te.tournament_edition_id = m.tournament_edition_id
        WHERE mv.match_id = %s
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        return None
    return _MatchEvidence(
        match_version_id=int(row[0]),
        player_1_id=int(row[1]),
        player_2_id=int(row[2]),
        match_start_at=row[3],
        tour_scope=str(row[4]),
        competition_tier=str(row[5]),
        tournament_edition_id=int(row[6]),
        tournament_id=int(row[7]),
        season_year=int(row[8]),
    )


def _load_source_evidence(
    conn: psycopg.Connection, match_id: int, *, source_system: str | None = None
) -> _SourceEvidence | str:
    """Return the deterministic provider identity, or a rejection reason string.

    When ``source_system`` is given, only that provider's rows are considered, so
    unrelated provider mappings on the same canonical match never create ambiguity.
    Without an explicit provider, the match must carry exactly one usable row.
    """
    if source_system is not None:
        rows = conn.execute(
            """
            SELECT source_system, source_match_id
            FROM match_source_ids
            WHERE match_id = %s
              AND source_system = %s
              AND source_match_id IS NOT NULL
            ORDER BY match_source_id
            """,
            (match_id, source_system),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT source_system, source_match_id
            FROM match_source_ids
            WHERE match_id = %s AND source_match_id IS NOT NULL
            ORDER BY match_source_id
            """,
            (match_id,),
        ).fetchall()
    if not rows:
        return REASON_SOURCE_IDENTITY_MISSING
    if len(rows) > 1:
        return REASON_SOURCE_IDENTITY_AMBIGUOUS
    return _SourceEvidence(source_system=str(rows[0][0]), source_match_id=str(rows[0][1]))


def _load_terminal_result(
    conn: psycopg.Connection, match_id: int
) -> _TerminalResult | None:
    """Load the authoritative latest terminal result observation (not settlement)."""
    row = conn.execute(
        """
        SELECT
            match_result_observation_id,
            outcome_status::text,
            winner_side,
            retired_side,
            sets_won_player_1,
            sets_won_player_2,
            games_won_player_1,
            games_won_player_2,
            scoreline_text,
            source_observed_at,
            as_of
        FROM match_result_observations
        WHERE match_id = %s AND outcome_status::text = ANY(%s)
        ORDER BY as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id, list(TERMINAL_RESULT_STATUSES)),
    ).fetchone()
    if row is None:
        return None
    return _TerminalResult(
        observation_id=int(row[0]),
        outcome_status=str(row[1]),
        winner_side=None if row[2] is None else int(row[2]),
        retired_side=None if row[3] is None else int(row[3]),
        sets_won_player_1=None if row[4] is None else int(row[4]),
        sets_won_player_2=None if row[5] is None else int(row[5]),
        games_won_player_1=None if row[6] is None else int(row[6]),
        games_won_player_2=None if row[7] is None else int(row[7]),
        scoreline_text=row[8],
        source_observed_at=row[9],
        as_of=row[10],
    )


def _reject(
    target_match_id: int, evidence_match_id: int, reason: str
) -> CrossProviderReconciliationEvaluation:
    return CrossProviderReconciliationEvaluation(
        target_match_id=target_match_id,
        evidence_match_id=evidence_match_id,
        apply_safe=False,
        rejection_reason=reason,
    )


def _flip(side: int | None) -> int | None:
    if side is None:
        return None
    return 2 if side == 1 else 1


@dataclass(frozen=True)
class SportingIdentity:
    """Provider-neutral sporting identity compared by ``cross_provider_exact_match_v1``.

    Shared by historical reconciliation and prospective canonical reuse so the two
    gates can never drift apart.
    """

    player_1_id: int
    player_2_id: int
    tournament_id: int | None
    season_year: int | None
    tour_scope: str | None
    competition_tier: str | None
    match_start_at: datetime | None


@dataclass(frozen=True)
class SportingIdentityComparison:
    safe: bool
    reason: str | None
    player_order_mapping: str | None
    start_delta_minutes: float | None


def compare_sporting_identity(
    target: SportingIdentity,
    evidence: SportingIdentity,
    *,
    max_start_delta_minutes: int = MAX_START_DELTA_MINUTES,
) -> SportingIdentityComparison:
    """Pure ``cross_provider_exact_match_v1`` comparison. Performs no I/O."""
    if (
        target.player_1_id == evidence.player_1_id
        and target.player_2_id == evidence.player_2_id
    ):
        mapping = PLAYER_ORDER_DIRECT
    elif (
        target.player_1_id == evidence.player_2_id
        and target.player_2_id == evidence.player_1_id
    ):
        mapping = PLAYER_ORDER_REVERSED
    else:
        return SportingIdentityComparison(False, REASON_IDENTITY_CONFLICT, None, None)

    if target.tournament_id is None or evidence.tournament_id is None:
        return SportingIdentityComparison(False, REASON_TOURNAMENT_MISMATCH, mapping, None)
    if target.tournament_id != evidence.tournament_id:
        return SportingIdentityComparison(False, REASON_TOURNAMENT_MISMATCH, mapping, None)
    if target.season_year is None or evidence.season_year is None:
        return SportingIdentityComparison(False, REASON_SEASON_MISMATCH, mapping, None)
    if target.season_year != evidence.season_year:
        return SportingIdentityComparison(False, REASON_SEASON_MISMATCH, mapping, None)
    if target.tour_scope != evidence.tour_scope:
        return SportingIdentityComparison(False, REASON_SCOPE_MISMATCH, mapping, None)
    if target.competition_tier != evidence.competition_tier:
        return SportingIdentityComparison(False, REASON_TIER_MISMATCH, mapping, None)
    if target.match_start_at is None or evidence.match_start_at is None:
        return SportingIdentityComparison(False, REASON_TIME_MISMATCH, mapping, None)

    start_delta_minutes = (
        abs((target.match_start_at - evidence.match_start_at).total_seconds()) / 60.0
    )
    if start_delta_minutes > max_start_delta_minutes:
        return SportingIdentityComparison(
            False, REASON_TIME_MISMATCH, mapping, start_delta_minutes
        )
    return SportingIdentityComparison(True, None, mapping, start_delta_minutes)


def _identity_from_evidence(evidence: _MatchEvidence) -> SportingIdentity:
    return SportingIdentity(
        player_1_id=evidence.player_1_id,
        player_2_id=evidence.player_2_id,
        tournament_id=evidence.tournament_id,
        season_year=evidence.season_year,
        tour_scope=evidence.tour_scope,
        competition_tier=evidence.competition_tier,
        match_start_at=evidence.match_start_at,
    )


def _evaluate(
    conn: psycopg.Connection,
    *,
    target_match_id: int,
    evidence_match_id: int,
) -> tuple[_MatchEvidence, _MatchEvidence, str] | CrossProviderReconciliationEvaluation:
    if target_match_id == evidence_match_id:
        return _reject(target_match_id, evidence_match_id, REASON_IDENTITY_CONFLICT)

    target = _load_match_evidence(conn, target_match_id)
    evidence = _load_match_evidence(conn, evidence_match_id)
    if target is None or evidence is None:
        return _reject(target_match_id, evidence_match_id, REASON_MATCH_NOT_FOUND)

    comparison = compare_sporting_identity(
        _identity_from_evidence(target), _identity_from_evidence(evidence)
    )
    if not comparison.safe:
        return _reject(
            target_match_id,
            evidence_match_id,
            comparison.reason or REASON_IDENTITY_CONFLICT,
        )
    return target, evidence, comparison.player_order_mapping or PLAYER_ORDER_DIRECT


def evaluate_cross_provider_reconciliation(
    conn: psycopg.Connection,
    *,
    target_match_id: int,
    evidence_match_id: int,
    target_source_system: str | None = None,
    evidence_source_system: str | None = None,
) -> CrossProviderReconciliationEvaluation:
    """Evaluate a proposed target/evidence pair. Read-only and deterministic.

    ``target_source_system`` / ``evidence_source_system`` optionally pin the
    provider identity to use; without them the match must carry exactly one row.
    """
    base = _evaluate(
        conn, target_match_id=target_match_id, evidence_match_id=evidence_match_id
    )
    if isinstance(base, CrossProviderReconciliationEvaluation):
        return base
    target, evidence, mapping = base

    target_source = _load_source_evidence(
        conn, target_match_id, source_system=target_source_system
    )
    if isinstance(target_source, str):
        return _reject(target_match_id, evidence_match_id, target_source)
    evidence_source = _load_source_evidence(
        conn, evidence_match_id, source_system=evidence_source_system
    )
    if isinstance(evidence_source, str):
        return _reject(target_match_id, evidence_match_id, evidence_source)

    result = _load_terminal_result(conn, evidence_match_id)
    if result is None:
        return _reject(target_match_id, evidence_match_id, REASON_RESULT_MISSING)

    if mapping == PLAYER_ORDER_DIRECT:
        classification = CLASSIFICATION_EXACT
        translated_winner_side = result.winner_side
        translated_retired_side = result.retired_side
        translated_sets_1 = result.sets_won_player_1
        translated_sets_2 = result.sets_won_player_2
        translated_games_1 = result.games_won_player_1
        translated_games_2 = result.games_won_player_2
    else:
        classification = CLASSIFICATION_REVERSED
        translated_winner_side = _flip(result.winner_side)
        translated_retired_side = _flip(result.retired_side)
        translated_sets_1 = result.sets_won_player_2
        translated_sets_2 = result.sets_won_player_1
        translated_games_1 = result.games_won_player_2
        translated_games_2 = result.games_won_player_1

    return CrossProviderReconciliationEvaluation(
        target_match_id=target_match_id,
        evidence_match_id=evidence_match_id,
        apply_safe=True,
        rejection_reason=None,
        classification=classification,
        player_order_mapping=mapping,
        target_player_1_id=target.player_1_id,
        target_player_2_id=target.player_2_id,
        evidence_player_1_id=evidence.player_1_id,
        evidence_player_2_id=evidence.player_2_id,
        target_tournament_id=target.tournament_id,
        evidence_tournament_id=evidence.tournament_id,
        target_tournament_edition_id=target.tournament_edition_id,
        evidence_tournament_edition_id=evidence.tournament_edition_id,
        target_season=target.season_year,
        evidence_season=evidence.season_year,
        target_start_at=target.match_start_at,
        evidence_start_at=evidence.match_start_at,
        start_delta_minutes=abs(
            (target.match_start_at - evidence.match_start_at).total_seconds()
        )
        / 60.0,
        target_tour_scope=target.tour_scope,
        evidence_tour_scope=evidence.tour_scope,
        target_competition_tier=target.competition_tier,
        evidence_competition_tier=evidence.competition_tier,
        target_source_system=target_source.source_system,
        target_source_match_id=target_source.source_match_id,
        evidence_source_system=evidence_source.source_system,
        evidence_source_match_id=evidence_source.source_match_id,
        evidence_result_observation_id=result.observation_id,
        evidence_outcome_status=result.outcome_status,
        evidence_winner_side=result.winner_side,
        evidence_retired_side=result.retired_side,
        evidence_sets_won_player_1=result.sets_won_player_1,
        evidence_sets_won_player_2=result.sets_won_player_2,
        evidence_games_won_player_1=result.games_won_player_1,
        evidence_games_won_player_2=result.games_won_player_2,
        evidence_scoreline_text=result.scoreline_text,
        evidence_source_observed_at=result.source_observed_at,
        evidence_as_of=result.as_of,
        translated_winner_side=translated_winner_side,
        translated_retired_side=translated_retired_side,
        translated_sets_won_player_1=translated_sets_1,
        translated_sets_won_player_2=translated_sets_2,
        translated_games_won_player_1=translated_games_1,
        translated_games_won_player_2=translated_games_2,
    )


# --------------------------------------------------- prospective canonical reuse
#
# Historical reconciliation attaches an already-existing duplicate. Prospective
# reuse prevents the duplicate from ever being created: when a provider supplies a
# previously unseen source id whose sporting identity is exactly one existing
# canonical match, ingestion reuses that match instead of creating a second one.

#: Providers whose unknown source ids attempt deterministic v1 canonical reuse.
PROSPECTIVE_REUSE_SOURCE_SYSTEMS = ("API_TENNIS",)

#: Bounded pre-filter around the incoming start time. Candidates outside this
#: window are not loaded; the exact v1 gate is still the 15-minute tolerance.
CANDIDATE_START_SEARCH_MINUTES = 180

REUSE_STATUS_REUSED = "REUSED"
REUSE_STATUS_NO_SAFE_CANDIDATE = "NO_SAFE_CANDIDATE"
REUSE_STATUS_AMBIGUOUS = "AMBIGUOUS"


class CrossProviderIdentityAmbiguous(RuntimeError):
    """An unknown provider event matches more than one safe canonical match.

    Raised so ingestion fails closed rather than guessing or creating a duplicate.
    """


@dataclass(frozen=True)
class CanonicalReuseResolution:
    status: str
    match_id: int | None
    orientation: str | None
    start_delta_minutes: float | None
    candidate_count: int
    reason: str | None = None


def _bounded_candidate_rows(
    conn: psycopg.Connection,
    *,
    incoming: SportingIdentity,
    search_minutes: int,
) -> list[tuple]:
    assert incoming.match_start_at is not None
    return conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier
            FROM match_versions mv
            WHERE mv.match_start_at IS NOT NULL
              AND mv.match_start_at
                  BETWEEN %(start)s - (%(search)s * INTERVAL '1 minute')
                      AND %(start)s + (%(search)s * INTERVAL '1 minute')
              AND (
                  (mv.player_1_id = %(p1)s AND mv.player_2_id = %(p2)s)
                  OR (mv.player_1_id = %(p2)s AND mv.player_2_id = %(p1)s)
              )
            ORDER BY
                mv.match_id,
                mv.as_of DESC,
                mv.source_observed_at DESC,
                mv.match_version_id DESC
        )
        SELECT
            latest.match_id,
            latest.player_1_id,
            latest.player_2_id,
            latest.match_start_at,
            latest.tour_scope,
            latest.competition_tier,
            te.tournament_id,
            te.season_year
        FROM latest
        JOIN matches m ON m.match_id = latest.match_id
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        ORDER BY latest.match_id
        """,
        {
            "start": incoming.match_start_at,
            "search": search_minutes,
            "p1": incoming.player_1_id,
            "p2": incoming.player_2_id,
        },
    ).fetchall()


def resolve_cross_provider_canonical_reuse(
    conn: psycopg.Connection,
    *,
    incoming: SportingIdentity,
    search_minutes: int = CANDIDATE_START_SEARCH_MINUTES,
    max_start_delta_minutes: int = MAX_START_DELTA_MINUTES,
) -> CanonicalReuseResolution:
    """Read-only, bounded v1 lookup for a previously unseen provider event.

    Zero safe candidates returns ``NO_SAFE_CANDIDATE`` (normal creation). Exactly
    one returns ``REUSED``. Two or more returns ``AMBIGUOUS`` so the caller fails
    closed. The 15-minute tolerance is the exact gate; the wider pre-filter only
    bounds the candidate scan.
    """
    if incoming.match_start_at is None:
        return CanonicalReuseResolution(
            REUSE_STATUS_NO_SAFE_CANDIDATE, None, None, None, 0, REASON_TIME_MISMATCH
        )
    if incoming.tournament_id is None:
        return CanonicalReuseResolution(
            REUSE_STATUS_NO_SAFE_CANDIDATE,
            None,
            None,
            None,
            0,
            REASON_TOURNAMENT_MISMATCH,
        )

    rows = _bounded_candidate_rows(
        conn, incoming=incoming, search_minutes=search_minutes
    )
    safe: list[tuple[int, SportingIdentityComparison]] = []
    reasons: Counter[str] = Counter()
    for row in rows:
        candidate = SportingIdentity(
            player_1_id=int(row[1]),
            player_2_id=int(row[2]),
            tournament_id=int(row[6]),
            season_year=int(row[7]),
            tour_scope=str(row[4]),
            competition_tier=str(row[5]),
            match_start_at=row[3],
        )
        comparison = compare_sporting_identity(
            incoming, candidate, max_start_delta_minutes=max_start_delta_minutes
        )
        if comparison.safe:
            safe.append((int(row[0]), comparison))
            continue
        reasons[comparison.reason or "UNKNOWN"] += 1

    if len(safe) == 1:
        match_id, comparison = safe[0]
        return CanonicalReuseResolution(
            REUSE_STATUS_REUSED,
            match_id,
            comparison.player_order_mapping,
            comparison.start_delta_minutes,
            len(rows),
        )
    if len(safe) > 1:
        return CanonicalReuseResolution(
            REUSE_STATUS_AMBIGUOUS,
            None,
            None,
            None,
            len(rows),
            REASON_IDENTITY_CONFLICT,
        )
    dominant = reasons.most_common(1)[0][0] if reasons else REASON_IDENTITY_CONFLICT
    return CanonicalReuseResolution(
        REUSE_STATUS_NO_SAFE_CANDIDATE, None, None, None, len(rows), dominant
    )


@dataclass(frozen=True)
class CrossProviderReconciliationApplyResult:
    """Immutable outcome of an apply attempt."""

    status: str
    target_match_id: int
    evidence_match_id: int
    already_reconciled: bool
    existing_equivalent_result: bool
    classification: str | None = None
    reconciliation_id: int | None = None
    target_result_observation_id: int | None = None
    evidence_result_observation_id: int | None = None
    player_order_mapping: str | None = None
    translated_outcome_status: str | None = None
    translated_winner_side: int | None = None
    translated_retired_side: int | None = None
    reason: str | None = None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _find_existing_reconciliation(
    conn: psycopg.Connection,
    *,
    target_match_id: int,
    evidence_result_observation_id: int | None,
) -> int | None:
    if evidence_result_observation_id is None:
        return None
    row = conn.execute(
        """
        SELECT reconciliation_id
        FROM cross_provider_match_reconciliations
        WHERE canonical_target_match_id = %s
          AND evidence_result_observation_id = %s
          AND reconciliation_policy_version = %s
        """,
        (target_match_id, evidence_result_observation_id, POLICY_VERSION),
    ).fetchone()
    return None if row is None else int(row[0])


def _results_equivalent(
    existing: _TerminalResult,
    *,
    outcome_status: str | None,
    winner_side: int | None,
    retired_side: int | None,
) -> bool:
    """Semantic equivalence: outcome status + winner + retired side only.

    Optional score fields (sets/games/scoreline) are supplementary evidence and are
    deliberately NOT required to match.
    """
    return (
        existing.outcome_status == outcome_status
        and existing.winner_side == winner_side
        and existing.retired_side == retired_side
    )


def _evidence_payload(
    evaluation: CrossProviderReconciliationEvaluation, *, reviewed_at: datetime
) -> dict[str, object]:
    return {
        "policy_version": POLICY_VERSION,
        "target_match_id": evaluation.target_match_id,
        "evidence_match_id": evaluation.evidence_match_id,
        "target_source_system": evaluation.target_source_system,
        "target_source_match_id": evaluation.target_source_match_id,
        "evidence_source_system": evaluation.evidence_source_system,
        "evidence_source_match_id": evaluation.evidence_source_match_id,
        "target_player_1_id": evaluation.target_player_1_id,
        "target_player_2_id": evaluation.target_player_2_id,
        "evidence_player_1_id": evaluation.evidence_player_1_id,
        "evidence_player_2_id": evaluation.evidence_player_2_id,
        "player_order_mapping": evaluation.player_order_mapping,
        "target_tournament_id": evaluation.target_tournament_id,
        "evidence_tournament_id": evaluation.evidence_tournament_id,
        "target_tournament_edition_id": evaluation.target_tournament_edition_id,
        "evidence_tournament_edition_id": evaluation.evidence_tournament_edition_id,
        "target_season": evaluation.target_season,
        "evidence_season": evaluation.evidence_season,
        "target_start_at": _iso(evaluation.target_start_at),
        "evidence_start_at": _iso(evaluation.evidence_start_at),
        "start_delta_minutes": evaluation.start_delta_minutes,
        "target_tour_scope": evaluation.target_tour_scope,
        "evidence_tour_scope": evaluation.evidence_tour_scope,
        "target_competition_tier": evaluation.target_competition_tier,
        "evidence_competition_tier": evaluation.evidence_competition_tier,
        "evidence_result_observation_id": evaluation.evidence_result_observation_id,
        "evidence_outcome_status": evaluation.evidence_outcome_status,
        "evidence_winner_side": evaluation.evidence_winner_side,
        "evidence_retired_side": evaluation.evidence_retired_side,
        "translated_winner_side": evaluation.translated_winner_side,
        "translated_retired_side": evaluation.translated_retired_side,
        "evidence_source_observed_at": _iso(evaluation.evidence_source_observed_at),
        "evidence_as_of": _iso(evaluation.evidence_as_of),
        "reviewed_at": _iso(reviewed_at),
    }


def _insert_reconciliation(
    conn: psycopg.Connection,
    *,
    evaluation: CrossProviderReconciliationEvaluation,
    reviewed_at: datetime,
) -> int:
    payload = _evidence_payload(evaluation, reviewed_at=reviewed_at)
    row = conn.execute(
        """
        INSERT INTO cross_provider_match_reconciliations (
            canonical_target_match_id,
            evidence_match_id,
            target_source_system,
            target_source_match_id,
            evidence_source_system,
            evidence_source_match_id,
            evidence_result_observation_id,
            classification,
            player_order_mapping,
            reconciliation_policy_version,
            reviewed_at,
            evidence_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING reconciliation_id
        """,
        (
            evaluation.target_match_id,
            evaluation.evidence_match_id,
            evaluation.target_source_system,
            evaluation.target_source_match_id,
            evaluation.evidence_source_system,
            evaluation.evidence_source_match_id,
            evaluation.evidence_result_observation_id,
            evaluation.classification,
            evaluation.player_order_mapping,
            POLICY_VERSION,
            reviewed_at,
            Jsonb(payload),
        ),
    ).fetchone()
    return int(row[0])


def _insert_target_result(
    conn: psycopg.Connection,
    *,
    evaluation: CrossProviderReconciliationEvaluation,
    reviewed_at: datetime,
) -> int:
    # Free-form scoreline text is orientation-dependent; only copy it under DIRECT
    # orientation. Structured sets/games are translated separately.
    scoreline = (
        evaluation.evidence_scoreline_text
        if evaluation.player_order_mapping == PLAYER_ORDER_DIRECT
        else None
    )
    # Persistence time must be >= the knowledge time; never backdate. GREATEST keeps
    # ingested_at >= reviewed_at even if the DB clock is briefly behind a future stamp.
    row = conn.execute(
        """
        INSERT INTO match_result_observations (
            match_id,
            outcome_status,
            winner_side,
            retired_side,
            sets_won_player_1,
            sets_won_player_2,
            games_won_player_1,
            games_won_player_2,
            scoreline_text,
            source_observed_at,
            as_of,
            ingested_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            GREATEST(clock_timestamp(), %s)
        )
        RETURNING match_result_observation_id
        """,
        (
            evaluation.target_match_id,
            evaluation.evidence_outcome_status,
            evaluation.translated_winner_side,
            evaluation.translated_retired_side,
            evaluation.translated_sets_won_player_1,
            evaluation.translated_sets_won_player_2,
            evaluation.translated_games_won_player_1,
            evaluation.translated_games_won_player_2,
            scoreline,
            reviewed_at,
            reviewed_at,
            reviewed_at,
        ),
    ).fetchone()
    return int(row[0])


def _already_reconciled_result(
    evaluation: CrossProviderReconciliationEvaluation, reconciliation_id: int
) -> CrossProviderReconciliationApplyResult:
    return CrossProviderReconciliationApplyResult(
        status=STATUS_ALREADY_RECONCILED,
        target_match_id=evaluation.target_match_id,
        evidence_match_id=evaluation.evidence_match_id,
        already_reconciled=True,
        existing_equivalent_result=False,
        classification=evaluation.classification,
        reconciliation_id=reconciliation_id,
        evidence_result_observation_id=evaluation.evidence_result_observation_id,
        player_order_mapping=evaluation.player_order_mapping,
        translated_outcome_status=evaluation.evidence_outcome_status,
        translated_winner_side=evaluation.translated_winner_side,
        translated_retired_side=evaluation.translated_retired_side,
    )


def apply_cross_provider_reconciliation(
    conn: psycopg.Connection,
    *,
    target_match_id: int,
    evidence_match_id: int,
    reviewed_at: datetime,
    target_source_system: str | None = None,
    evidence_source_system: str | None = None,
) -> CrossProviderReconciliationApplyResult:
    """Atomically persist a proven cross-provider reconciliation.

    Always re-evaluates before mutating. Only writes to
    ``cross_provider_match_reconciliations`` and ``match_result_observations``.
    Uses ``conn.transaction()`` so the reconciliation provenance insert and the
    target result append commit or roll back together (nested savepoints are used
    when the caller already holds an open transaction).
    """
    evaluation = evaluate_cross_provider_reconciliation(
        conn,
        target_match_id=target_match_id,
        evidence_match_id=evidence_match_id,
        target_source_system=target_source_system,
        evidence_source_system=evidence_source_system,
    )
    if not evaluation.apply_safe:
        return CrossProviderReconciliationApplyResult(
            status=STATUS_UNSAFE_EVALUATION,
            target_match_id=target_match_id,
            evidence_match_id=evidence_match_id,
            already_reconciled=False,
            existing_equivalent_result=False,
            classification=evaluation.classification,
            player_order_mapping=evaluation.player_order_mapping,
            reason=evaluation.rejection_reason,
        )

    evidence_observation_id = evaluation.evidence_result_observation_id
    existing_reconciliation_id = _find_existing_reconciliation(
        conn,
        target_match_id=target_match_id,
        evidence_result_observation_id=evidence_observation_id,
    )
    if existing_reconciliation_id is not None:
        return _already_reconciled_result(evaluation, existing_reconciliation_id)

    target_result = _load_terminal_result(conn, target_match_id)
    if target_result is not None and not _results_equivalent(
        target_result,
        outcome_status=evaluation.evidence_outcome_status,
        winner_side=evaluation.translated_winner_side,
        retired_side=evaluation.translated_retired_side,
    ):
        return CrossProviderReconciliationApplyResult(
            status=STATUS_RESULT_CONFLICT,
            target_match_id=target_match_id,
            evidence_match_id=evidence_match_id,
            already_reconciled=False,
            existing_equivalent_result=False,
            classification=evaluation.classification,
            evidence_result_observation_id=evidence_observation_id,
            player_order_mapping=evaluation.player_order_mapping,
            reason="target terminal result contradicts translated evidence result",
        )

    try:
        with conn.transaction():
            raced = _find_existing_reconciliation(
                conn,
                target_match_id=target_match_id,
                evidence_result_observation_id=evidence_observation_id,
            )
            if raced is not None:
                return _already_reconciled_result(evaluation, raced)

            reconciliation_id = _insert_reconciliation(
                conn, evaluation=evaluation, reviewed_at=reviewed_at
            )

            if target_result is not None:
                return CrossProviderReconciliationApplyResult(
                    status=STATUS_EXISTING_EQUIVALENT_RESULT,
                    target_match_id=target_match_id,
                    evidence_match_id=evidence_match_id,
                    already_reconciled=False,
                    existing_equivalent_result=True,
                    classification=evaluation.classification,
                    reconciliation_id=reconciliation_id,
                    target_result_observation_id=target_result.observation_id,
                    evidence_result_observation_id=evidence_observation_id,
                    player_order_mapping=evaluation.player_order_mapping,
                    translated_outcome_status=evaluation.evidence_outcome_status,
                    translated_winner_side=evaluation.translated_winner_side,
                    translated_retired_side=evaluation.translated_retired_side,
                )

            target_observation_id = _insert_target_result(
                conn, evaluation=evaluation, reviewed_at=reviewed_at
            )
            return CrossProviderReconciliationApplyResult(
                status=STATUS_APPLIED,
                target_match_id=target_match_id,
                evidence_match_id=evidence_match_id,
                already_reconciled=False,
                existing_equivalent_result=False,
                classification=evaluation.classification,
                reconciliation_id=reconciliation_id,
                target_result_observation_id=target_observation_id,
                evidence_result_observation_id=evidence_observation_id,
                player_order_mapping=evaluation.player_order_mapping,
                translated_outcome_status=evaluation.evidence_outcome_status,
                translated_winner_side=evaluation.translated_winner_side,
                translated_retired_side=evaluation.translated_retired_side,
            )
    except psycopg.errors.UniqueViolation:
        existing = _find_existing_reconciliation(
            conn,
            target_match_id=target_match_id,
            evidence_result_observation_id=evidence_observation_id,
        )
        if existing is not None:
            return _already_reconciled_result(evaluation, existing)
        raise
