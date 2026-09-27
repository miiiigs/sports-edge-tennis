"""Read-only cross-provider reconciliation evaluator.

Policy ``cross_provider_exact_match_v1`` decides whether a target canonical match and
an evidence canonical match are safely the same real sporting event, and how the
evidence terminal result would translate onto the target match.

This module performs ZERO writes. It only issues SELECT statements. The atomic apply
step (persisting provenance + appending an equivalent target result) lives in a later
service and is intentionally not implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg

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
    conn: psycopg.Connection, match_id: int
) -> _SourceEvidence | str:
    """Return the deterministic provider identity, or a rejection reason string."""
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
        return _reject(target_match_id, evidence_match_id, REASON_IDENTITY_CONFLICT)

    if target.tournament_id != evidence.tournament_id:
        return _reject(target_match_id, evidence_match_id, REASON_TOURNAMENT_MISMATCH)
    if target.season_year != evidence.season_year:
        return _reject(target_match_id, evidence_match_id, REASON_SEASON_MISMATCH)
    if target.tour_scope != evidence.tour_scope:
        return _reject(target_match_id, evidence_match_id, REASON_SCOPE_MISMATCH)
    if target.competition_tier != evidence.competition_tier:
        return _reject(target_match_id, evidence_match_id, REASON_TIER_MISMATCH)

    start_delta_minutes = abs(
        (target.match_start_at - evidence.match_start_at).total_seconds()
    ) / 60.0
    if start_delta_minutes > MAX_START_DELTA_MINUTES:
        return _reject(target_match_id, evidence_match_id, REASON_TIME_MISMATCH)

    return target, evidence, mapping


def evaluate_cross_provider_reconciliation(
    conn: psycopg.Connection,
    *,
    target_match_id: int,
    evidence_match_id: int,
) -> CrossProviderReconciliationEvaluation:
    """Evaluate a proposed target/evidence pair. Read-only and deterministic."""
    base = _evaluate(
        conn, target_match_id=target_match_id, evidence_match_id=evidence_match_id
    )
    if isinstance(base, CrossProviderReconciliationEvaluation):
        return base
    target, evidence, mapping = base

    target_source = _load_source_evidence(conn, target_match_id)
    if isinstance(target_source, str):
        return _reject(target_match_id, evidence_match_id, target_source)
    evidence_source = _load_source_evidence(conn, evidence_match_id)
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
