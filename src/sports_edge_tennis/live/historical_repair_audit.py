"""Read-only historical identity, result and input repair audit.

This module reproduces the P0 Tennis affected-record population and produces a
dry-run correction manifest. It never writes, merges identities, deletes rows,
fabricates results or changes model behaviour.

Design rules:

* Pending picks use the same population and reason precedence as
  :mod:`sports_edge_tennis.live.pending_result_diagnostics`.
* A result is only proposed when it is already persisted on another canonical
  match with the same two players, the same tournament edition and a start time
  within a narrow tolerance. The proposal is an append-only cross-provider
  reconciliation, not a copy of the result onto the target.
* An explicit provider ``WALKOVER`` may be proposed only when bracket evidence
  proves which player advanced. Settlement maps ``WALKOVER`` to ``VOID``; it is
  never scored as an ordinary win or loss.
* Bracket advancement alone never becomes a ``COMPLETED`` result. An unknown
  terminal type could have been a walkover, retirement or cancellation.
* Same-provider duplicate fixtures for one player at the same time are reported
  for review and are never merged by name.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row

from sports_edge_tennis.live.api_tennis_identity_audit import (
    audit_canonical_duplicate_aliases,
)
from sports_edge_tennis.live.pending_result_diagnostics import (
    REASON_NOT_STARTED,
    PendingPickDiagnosis,
    load_pending_result_diagnostics,
)

DEFAULT_LOOKBACK_DAYS = 60
DEFAULT_LIMIT = 2000
DUPLICATE_START_TOLERANCE_HOURS = 6
SAME_PLAYER_CONFLICT_TOLERANCE_HOURS = 2
BRACKET_LOOKAHEAD_DAYS = 5

ACTION_NO_ACTION_NOT_STARTED = "NO_ACTION_NOT_STARTED"
ACTION_NO_ACTION_PENDING = "NO_ACTION_PENDING"
ACTION_RECONCILE_CROSS_PROVIDER_RESULT = "RECONCILE_CROSS_PROVIDER_RESULT"
ACTION_APPEND_EXPLICIT_WALKOVER_RESULT = "APPEND_EXPLICIT_WALKOVER_RESULT"
ACTION_HOLD_DUPLICATE_FIXTURE = "HOLD_DUPLICATE_FIXTURE"
ACTION_PROVIDER_RESULT_REQUIRED = "PROVIDER_RESULT_REQUIRED"

SAFETY_EVIDENCE_ONLY = "EVIDENCE_ONLY"
SAFETY_REQUIRES_OWNER_APPROVAL = "REQUIRES_OWNER_APPROVAL"
SAFETY_BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class PendingMatchEvidence:
    match_id: int
    player_1_id: int
    player_2_id: int
    player_1: str
    player_2: str
    tournament: str
    competition_tier: str
    tournament_edition_id: int
    match_start_at: datetime
    match_status: str
    surface: str | None
    edition_surface: str | None
    round_code: str | None
    best_of_sets: int
    qualifying_flag: bool
    api_tennis_event_key: str | None
    bet_decisions: int
    bet_sides: tuple[int, ...]

    @property
    def has_type_a_bet(self) -> bool:
        return self.bet_decisions > 0


@dataclass(frozen=True)
class CrossSourceResultCandidate:
    target_match_id: int
    evidence_match_id: int
    outcome_status: str
    winner_side: int | None
    scoreline_text: str | None
    source_observed_at: datetime
    evidence_source_system: str | None
    evidence_source_match_id: str | None
    target_source_system: str | None


@dataclass(frozen=True)
class SamePlayerTimeConflict:
    target_match_id: int
    other_match_id: int
    shared_player_id: int
    shared_player: str
    target_opponent: str
    other_opponent: str
    target_start_at: datetime
    other_start_at: datetime
    target_has_result: bool
    other_has_result: bool


@dataclass(frozen=True)
class BracketAdvancement:
    target_match_id: int
    advancing_side: int | None
    player_1_later_matches: tuple[int, ...]
    player_2_later_matches: tuple[int, ...]


@dataclass(frozen=True)
class CorrectionProposal:
    match_id: int
    action: str
    safety: str
    reason: str
    before: dict[str, Any]
    after: dict[str, Any] | None
    writes: tuple[str, ...]
    evidence: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "action": self.action,
            "safety": self.safety,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
            "writes": list(self.writes),
            "evidence": self.evidence,
        }


def _preserve_row_factory(function):
    @wraps(function)
    def wrapper(conn: psycopg.Connection, *args, **kwargs):
        previous = conn.row_factory
        try:
            return function(conn, *args, **kwargs)
        finally:
            conn.row_factory = previous

    return wrapper


def _latest_terminal_result_sql() -> str:
    return """
        SELECT DISTINCT ON (match_id)
            match_id, outcome_status::text AS outcome_status, winner_side,
            scoreline_text, source_observed_at, as_of
        FROM match_result_observations
        WHERE outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
        ORDER BY match_id, as_of DESC, match_result_observation_id DESC
    """


@_preserve_row_factory
def load_pending_match_evidence(
    conn: psycopg.Connection, *, match_ids: list[int]
) -> dict[int, PendingMatchEvidence]:
    if not match_ids:
        return {}
    conn.row_factory = dict_row
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, m.tournament_edition_id, mv.player_1_id, mv.player_2_id,
                mv.match_start_at, mv.match_status::text AS match_status,
                mv.surface::text AS surface, mv.round_code, mv.best_of_sets,
                mv.qualifying_flag
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            WHERE mv.match_id = ANY(%(match_ids)s)
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT latest.*, p1.canonical_name AS player_1, p2.canonical_name AS player_2,
               t.canonical_name AS tournament,
               t.competition_tier::text AS competition_tier,
               te.surface::text AS edition_surface,
               (SELECT min(msi.source_match_id)
                  FROM match_source_ids msi
                 WHERE msi.match_id = latest.match_id
                   AND msi.source_system = 'API_TENNIS') AS api_tennis_event_key,
               (SELECT count(*) FROM decisions d
                 WHERE d.match_id = latest.match_id
                   AND d.signal_type = 'TYPE_A_MODEL_EDGE'
                   AND d.decision_status = 'BET'
                   AND d.market_type = 'MATCH_WINNER'
                   AND d.selection_side IN (1, 2)) AS bet_decisions,
               (SELECT array_agg(DISTINCT d.selection_side)
                  FROM decisions d
                 WHERE d.match_id = latest.match_id
                   AND d.signal_type = 'TYPE_A_MODEL_EDGE'
                   AND d.decision_status = 'BET'
                   AND d.market_type = 'MATCH_WINNER'
                   AND d.selection_side IN (1, 2)) AS bet_sides
        FROM latest
        JOIN players p1 ON p1.player_id = latest.player_1_id
        JOIN players p2 ON p2.player_id = latest.player_2_id
        JOIN tournament_editions te
          ON te.tournament_edition_id = latest.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        """,
        {"match_ids": match_ids},
    ).fetchall()
    return {
        int(row["match_id"]): PendingMatchEvidence(
            match_id=int(row["match_id"]),
            player_1_id=int(row["player_1_id"]),
            player_2_id=int(row["player_2_id"]),
            player_1=str(row["player_1"]),
            player_2=str(row["player_2"]),
            tournament=str(row["tournament"]),
            competition_tier=str(row["competition_tier"]),
            tournament_edition_id=int(row["tournament_edition_id"]),
            match_start_at=row["match_start_at"],
            match_status=str(row["match_status"]),
            surface=row["surface"],
            edition_surface=row["edition_surface"],
            round_code=row["round_code"],
            best_of_sets=int(row["best_of_sets"]),
            qualifying_flag=bool(row["qualifying_flag"]),
            api_tennis_event_key=row["api_tennis_event_key"],
            bet_decisions=int(row["bet_decisions"] or 0),
            bet_sides=tuple(int(side) for side in (row["bet_sides"] or ())),
        )
        for row in rows
    }


@_preserve_row_factory
def find_cross_source_result_candidates(
    conn: psycopg.Connection,
    *,
    match_ids: list[int],
    tolerance_hours: int = DUPLICATE_START_TOLERANCE_HOURS,
) -> dict[int, CrossSourceResultCandidate]:
    if not match_ids:
        return {}
    conn.row_factory = dict_row
    rows = conn.execute(
        f"""
        WITH target AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, m.tournament_edition_id, mv.player_1_id,
                mv.player_2_id, mv.match_start_at
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            WHERE mv.match_id = ANY(%(match_ids)s)
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        ),
        other AS (
            SELECT DISTINCT ON (mv.match_id)
                t.match_id AS target_match_id, mv.match_id AS evidence_match_id,
                mv.player_1_id, mv.player_2_id, mv.match_start_at
            FROM target t
            JOIN matches tm ON tm.match_id = t.match_id
            JOIN match_versions mv
              ON (
                  (mv.player_1_id = t.player_1_id AND mv.player_2_id = t.player_2_id)
                  OR (mv.player_1_id = t.player_2_id AND mv.player_2_id = t.player_1_id)
              )
            JOIN matches m ON m.match_id = mv.match_id
            JOIN tournament_editions other_edition
              ON other_edition.tournament_edition_id = m.tournament_edition_id
            JOIN tournament_editions target_edition
              ON target_edition.tournament_edition_id = tm.tournament_edition_id
            WHERE mv.match_id <> t.match_id
              AND other_edition.tournament_id = target_edition.tournament_id
              AND mv.match_start_at BETWEEN
                  t.match_start_at - make_interval(hours => %(tolerance_hours)s)
                  AND t.match_start_at + make_interval(hours => %(tolerance_hours)s)
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT other.target_match_id, other.evidence_match_id,
               result.outcome_status, result.winner_side, result.scoreline_text,
               result.source_observed_at,
               (SELECT msi.source_system FROM match_source_ids msi
                 WHERE msi.match_id = other.evidence_match_id
                 ORDER BY msi.match_source_id LIMIT 1) AS evidence_source_system,
               (SELECT msi.source_match_id FROM match_source_ids msi
                 WHERE msi.match_id = other.evidence_match_id
                 ORDER BY msi.match_source_id LIMIT 1) AS evidence_source_match_id,
               (SELECT msi.source_system FROM match_source_ids msi
                 WHERE msi.match_id = other.target_match_id
                 ORDER BY msi.match_source_id LIMIT 1) AS target_source_system
        FROM other
        JOIN ({_latest_terminal_result_sql()}) result
          ON result.match_id = other.evidence_match_id
        WHERE NOT EXISTS (
            SELECT 1 FROM match_result_observations tr
            WHERE tr.match_id = other.target_match_id
              AND tr.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
        )
        """,
        {"match_ids": match_ids, "tolerance_hours": tolerance_hours},
    ).fetchall()
    candidates: dict[int, CrossSourceResultCandidate] = {}
    for row in rows:
        target_id = int(row["target_match_id"])
        if target_id in candidates:
            continue
        candidates[target_id] = CrossSourceResultCandidate(
            target_match_id=target_id,
            evidence_match_id=int(row["evidence_match_id"]),
            outcome_status=str(row["outcome_status"]),
            winner_side=int(row["winner_side"]) if row["winner_side"] else None,
            scoreline_text=row["scoreline_text"],
            source_observed_at=row["source_observed_at"],
            evidence_source_system=row["evidence_source_system"],
            evidence_source_match_id=row["evidence_source_match_id"],
            target_source_system=row["target_source_system"],
        )
    return candidates


@_preserve_row_factory
def find_same_player_time_conflicts(
    conn: psycopg.Connection,
    *,
    match_ids: list[int],
    tolerance_hours: int = SAME_PLAYER_CONFLICT_TOLERANCE_HOURS,
) -> dict[int, SamePlayerTimeConflict]:
    if not match_ids:
        return {}
    conn.row_factory = dict_row
    rows = conn.execute(
        """
        WITH target AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, te.tournament_id, mv.player_1_id,
                mv.player_2_id, mv.match_start_at
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            JOIN tournament_editions te
              ON te.tournament_edition_id = m.tournament_edition_id
            WHERE mv.match_id = ANY(%(match_ids)s)
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        ),
        other AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, te.tournament_id, mv.player_1_id,
                mv.player_2_id, mv.match_start_at
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            JOIN tournament_editions te
              ON te.tournament_edition_id = m.tournament_edition_id
            WHERE m.tournament_edition_id IN (
                SELECT tm.tournament_edition_id
                FROM matches tm
                WHERE tm.match_id = ANY(%(match_ids)s)
            )
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT t.match_id AS target_match_id, o.match_id AS other_match_id,
               CASE WHEN t.player_1_id = o.player_1_id OR t.player_1_id = o.player_2_id
                    THEN t.player_1_id ELSE t.player_2_id END AS shared_player_id,
               target_shared.canonical_name AS shared_player,
               target_opponent.canonical_name AS target_opponent,
               other_opponent.canonical_name AS other_opponent,
               t.match_start_at AS target_start_at, o.match_start_at AS other_start_at,
               EXISTS (SELECT 1 FROM match_result_observations r
                        WHERE r.match_id = t.match_id
                          AND r.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED'))
                   AS target_has_result,
               EXISTS (SELECT 1 FROM match_result_observations r
                        WHERE r.match_id = o.match_id
                          AND r.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED'))
                   AS other_has_result
        FROM target t
        JOIN other o
          ON o.tournament_id = t.tournament_id
         AND o.match_id <> t.match_id
         AND (
             o.player_1_id IN (t.player_1_id, t.player_2_id)
             OR o.player_2_id IN (t.player_1_id, t.player_2_id)
         )
         AND abs(extract(epoch FROM (o.match_start_at - t.match_start_at)))
             <= %(tolerance_hours)s * 3600
         AND NOT (
             (o.player_1_id = t.player_1_id AND o.player_2_id = t.player_2_id)
             OR (o.player_1_id = t.player_2_id AND o.player_2_id = t.player_1_id)
         )
        JOIN players target_shared
          ON target_shared.player_id = CASE
              WHEN t.player_1_id = o.player_1_id OR t.player_1_id = o.player_2_id
              THEN t.player_1_id ELSE t.player_2_id END
        JOIN players target_opponent
          ON target_opponent.player_id = CASE
              WHEN t.player_1_id = o.player_1_id OR t.player_1_id = o.player_2_id
              THEN t.player_2_id ELSE t.player_1_id END
        JOIN players other_opponent
          ON other_opponent.player_id = CASE
              WHEN o.player_1_id IN (t.player_1_id, t.player_2_id)
              THEN o.player_2_id ELSE o.player_1_id END
        WHERE EXISTS (SELECT 1 FROM match_result_observations r
                       WHERE r.match_id = t.match_id
                         AND r.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED'))
           OR EXISTS (SELECT 1 FROM match_result_observations r
                       WHERE r.match_id = o.match_id
                         AND r.outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED'))
        """,
        {"match_ids": match_ids, "tolerance_hours": tolerance_hours},
    ).fetchall()
    conflicts: dict[int, SamePlayerTimeConflict] = {}
    for row in rows:
        target_id = int(row["target_match_id"])
        if target_id in conflicts:
            continue
        conflicts[target_id] = SamePlayerTimeConflict(
            target_match_id=target_id,
            other_match_id=int(row["other_match_id"]),
            shared_player_id=int(row["shared_player_id"]),
            shared_player=str(row["shared_player"]),
            target_opponent=str(row["target_opponent"]),
            other_opponent=str(row["other_opponent"]),
            target_start_at=row["target_start_at"],
            other_start_at=row["other_start_at"],
            target_has_result=bool(row["target_has_result"]),
            other_has_result=bool(row["other_has_result"]),
        )
    return conflicts


@_preserve_row_factory
def load_bracket_advancement(
    conn: psycopg.Connection,
    *,
    evidence: dict[int, PendingMatchEvidence],
    lookahead_days: int = BRACKET_LOOKAHEAD_DAYS,
) -> dict[int, BracketAdvancement]:
    if not evidence:
        return {}
    conn.row_factory = dict_row
    rows = conn.execute(
        """
        WITH target AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, m.tournament_edition_id, mv.player_1_id, mv.player_2_id,
                mv.match_start_at
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            WHERE mv.match_id = ANY(%(match_ids)s)
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        ),
        later AS (
            SELECT DISTINCT ON (mv.match_id)
                t.match_id AS target_match_id, mv.match_id AS later_match_id,
                mv.player_1_id, mv.player_2_id
            FROM target t
            JOIN match_versions mv
              ON mv.match_start_at > t.match_start_at
             AND mv.match_start_at <=
                 t.match_start_at + make_interval(days => %(lookahead_days)s)
             AND (
                 mv.player_1_id IN (t.player_1_id, t.player_2_id)
                 OR mv.player_2_id IN (t.player_1_id, t.player_2_id)
             )
            JOIN matches m ON m.match_id = mv.match_id
               AND m.tournament_edition_id = t.tournament_edition_id
            WHERE mv.match_id <> t.match_id
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT target_match_id, later_match_id, player_1_id, player_2_id
        FROM later
        ORDER BY target_match_id, later_match_id
        """,
        {"match_ids": sorted(evidence), "lookahead_days": lookahead_days},
    ).fetchall()
    later: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        later.setdefault(int(row["target_match_id"]), []).append(dict(row))

    result: dict[int, BracketAdvancement] = {}
    for match_id, item in evidence.items():
        p1 = item.player_1_id
        p2 = item.player_2_id
        p1_matches = tuple(
            int(r["later_match_id"])
            for r in later.get(match_id, [])
            if p1 in (int(r["player_1_id"]), int(r["player_2_id"]))
        )
        p2_matches = tuple(
            int(r["later_match_id"])
            for r in later.get(match_id, [])
            if p2 in (int(r["player_1_id"]), int(r["player_2_id"]))
        )
        if p1_matches and not p2_matches:
            side = 1
        elif p2_matches and not p1_matches:
            side = 2
        else:
            side = None
        result[match_id] = BracketAdvancement(
            target_match_id=match_id,
            advancing_side=side,
            player_1_later_matches=p1_matches,
            player_2_later_matches=p2_matches,
        )
    return result


def classify_correction(
    diagnosis: PendingPickDiagnosis,
    *,
    evidence: PendingMatchEvidence,
    cross_source: CrossSourceResultCandidate | None,
    same_player_conflict: SamePlayerTimeConflict | None,
    bracket: BracketAdvancement | None,
) -> CorrectionProposal:
    match_id = diagnosis.match_id
    before = {
        "pending_reason": diagnosis.reason,
        "match_status": evidence.match_status,
        "has_terminal_result": False,
        "bet_decisions": evidence.bet_decisions,
        "bet_sides": list(evidence.bet_sides),
    }
    base_evidence = {
        "tournament": evidence.tournament,
        "match_start_at": evidence.match_start_at.isoformat(),
        "api_tennis_event_key": evidence.api_tennis_event_key,
        "bracket_advancing_side": bracket.advancing_side if bracket else None,
        "player_1_later_matches": list(bracket.player_1_later_matches)
        if bracket
        else [],
        "player_2_later_matches": list(bracket.player_2_later_matches)
        if bracket
        else [],
    }

    if diagnosis.reason == REASON_NOT_STARTED:
        return CorrectionProposal(
            match_id=match_id,
            action=ACTION_NO_ACTION_NOT_STARTED,
            safety=SAFETY_EVIDENCE_ONLY,
            reason="Fixture has not started; no historical correction is due.",
            before=before,
            after=None,
            writes=(),
            evidence=base_evidence,
        )

    if evidence.match_status == "WALKOVER" and bracket and bracket.advancing_side:
        side = bracket.advancing_side
        return CorrectionProposal(
            match_id=match_id,
            action=ACTION_APPEND_EXPLICIT_WALKOVER_RESULT,
            safety=SAFETY_REQUIRES_OWNER_APPROVAL,
            reason=(
                "Provider reports an explicit WALKOVER and bracket evidence proves "
                "which player advanced. Settlement policy maps WALKOVER to VOID, "
                "never to an ordinary win or loss."
            ),
            before=before,
            after={
                "outcome_status": "WALKOVER",
                "winner_side": side,
                "settlement_status": "VOID",
                "scoreline_text": None,
            },
            writes=("match_result_observations",),
            evidence={
                **base_evidence,
                "walkover_winner_side": side,
            },
        )

    if cross_source is not None and (
        cross_source.evidence_source_system
        and cross_source.evidence_source_system != cross_source.target_source_system
    ):
        return CorrectionProposal(
            match_id=match_id,
            action=ACTION_RECONCILE_CROSS_PROVIDER_RESULT,
            safety=SAFETY_REQUIRES_OWNER_APPROVAL,
            reason=(
                "The same two players, canonical tournament and start window already "
                "carry a terminal result on another canonical match. Propose an "
                "append-only cross-provider reconciliation; do not copy or "
                "fabricate a result on this target."
            ),
            before=before,
            after={
                "reconciled_evidence_match_id": cross_source.evidence_match_id,
                "evidence_outcome_status": cross_source.outcome_status,
                "evidence_winner_side": cross_source.winner_side,
                "target_result_written": False,
            },
            writes=("cross_provider_match_reconciliations",),
            evidence={
                **base_evidence,
                "evidence_match_id": cross_source.evidence_match_id,
                "evidence_outcome_status": cross_source.outcome_status,
                "evidence_winner_side": cross_source.winner_side,
                "evidence_score": cross_source.scoreline_text,
                "evidence_source_system": cross_source.evidence_source_system,
                "evidence_source_match_id": cross_source.evidence_source_match_id,
                "evidence_source_observed_at": cross_source.source_observed_at.isoformat(),
            },
        )

    if same_player_conflict is not None:
        return CorrectionProposal(
            match_id=match_id,
            action=ACTION_HOLD_DUPLICATE_FIXTURE,
            safety=SAFETY_BLOCKED,
            reason=(
                "The same player appears in two same-edition fixtures at nearly the "
                "same start time with different opponents. One event is a duplicate "
                "or misidentified fixture; do not merge identities or copy results "
                "without review."
            ),
            before=before,
            after=None,
            writes=(),
            evidence={
                **base_evidence,
                "other_match_id": same_player_conflict.other_match_id,
                "shared_player": same_player_conflict.shared_player,
                "target_opponent": same_player_conflict.target_opponent,
                "other_opponent": same_player_conflict.other_opponent,
                "other_start_at": same_player_conflict.other_start_at.isoformat(),
                "target_has_result": same_player_conflict.target_has_result,
                "other_has_result": same_player_conflict.other_has_result,
            },
        )

    if diagnosis.reason in {
        "TERMINAL_FIXTURE_WITHOUT_RESULT",
        "PROVIDER_FIXTURE_NOT_REOBSERVED",
        "NO_API_TENNIS_LINK",
    }:
        return CorrectionProposal(
            match_id=match_id,
            action=ACTION_PROVIDER_RESULT_REQUIRED,
            safety=SAFETY_BLOCKED,
            reason=(
                "No persisted terminal result and no safe stored-result evidence. "
                "Bracket advancement alone does not prove COMPLETED versus "
                "WALKOVER/RETIRED/CANCELLED, so no result may be inferred."
            ),
            before=before,
            after=None,
            writes=(),
            evidence=base_evidence,
        )

    return CorrectionProposal(
        match_id=match_id,
        action=ACTION_NO_ACTION_PENDING,
        safety=SAFETY_EVIDENCE_ONLY,
        reason=f"Pending reason {diagnosis.reason}; no correction is due yet.",
        before=before,
        after=None,
        writes=(),
        evidence=base_evidence,
    )


@_preserve_row_factory
def build_historical_repair_report(
    conn: psycopg.Connection,
    *,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    reference = now or datetime.now(UTC)
    diagnostics = load_pending_result_diagnostics(
        conn, now=reference, lookback_days=lookback_days, limit=limit
    )
    actionable = [
        item for item in diagnostics.items if item.reason != REASON_NOT_STARTED
    ]
    match_ids = [item.match_id for item in actionable]
    evidence = load_pending_match_evidence(conn, match_ids=match_ids)
    cross_source = find_cross_source_result_candidates(conn, match_ids=match_ids)
    same_player = find_same_player_time_conflicts(conn, match_ids=match_ids)
    bracket = load_bracket_advancement(conn, evidence=evidence)

    proposals = [
        classify_correction(
            item,
            evidence=evidence[item.match_id],
            cross_source=cross_source.get(item.match_id),
            same_player_conflict=same_player.get(item.match_id),
            bracket=bracket.get(item.match_id),
        )
        for item in actionable
        if item.match_id in evidence
    ]

    conn.row_factory = dict_row
    surface_row = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, m.tournament_edition_id, mv.match_start_at,
                mv.surface
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT
            count(*) FILTER (
                WHERE latest.match_start_at >= now() - interval '120 days'
                  AND latest.surface IS NULL
                  AND te.surface IS NULL
            ) AS recent_surface_gap_matches,
            count(*) FILTER (
                WHERE latest.surface IS NULL AND te.surface IS NULL
            ) AS all_surface_gap_matches
        FROM latest
        JOIN tournament_editions te
          ON te.tournament_edition_id = latest.tournament_edition_id
        """
    ).fetchone()
    slam_row = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, m.tournament_edition_id, mv.qualifying_flag,
                mv.best_of_sets
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT count(*) AS grand_slam_main_draw_not_best_of_5
        FROM latest
        JOIN tournament_editions te
          ON te.tournament_edition_id = latest.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        WHERE (
            t.canonical_name ILIKE '%australian open%'
            OR t.canonical_name ILIKE '%roland garros%'
            OR t.canonical_name ILIKE '%wimbledon%'
            OR t.canonical_name ILIKE '%us open%'
        )
          AND latest.qualifying_flag = false
          AND latest.best_of_sets <> 5
        """
    ).fetchone()
    prematch_row = conn.execute(
        """
        WITH first_pred AS (
            SELECT DISTINCT ON (p.match_id)
                p.match_id, p.as_of
            FROM predictions p
            WHERE p.as_of >= now() - interval '30 days'
            ORDER BY p.match_id, p.as_of, p.prediction_id
        ),
        pit AS (
            SELECT first_pred.match_id, first_pred.as_of, mv.match_version_id,
                   mv.surface, mv.round_code, mv.player_1_id, mv.player_2_id,
                   mv.match_start_at
            FROM first_pred
            LEFT JOIN LATERAL (
                SELECT mv0.* FROM match_versions mv0
                WHERE mv0.match_id = first_pred.match_id
                  AND mv0.as_of <= first_pred.as_of
                  AND mv0.source_observed_at <= first_pred.as_of
                ORDER BY mv0.as_of DESC, mv0.source_observed_at DESC,
                         mv0.match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
        ),
        latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, mv.player_1_id, mv.player_2_id
            FROM match_versions mv
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT
            count(*) AS matches,
            count(*) FILTER (WHERE pit.match_version_id IS NULL) AS no_pit_version,
            count(*) FILTER (WHERE pit.surface IS NULL) AS pit_surface_null,
            count(*) FILTER (WHERE pit.round_code IS NULL) AS pit_round_null,
            count(*) FILTER (
                WHERE latest.player_1_id IS NOT NULL
                  AND (pit.player_1_id, pit.player_2_id)
                      <> (latest.player_1_id, latest.player_2_id)
            ) AS participants_changed
        FROM pit
        LEFT JOIN latest ON latest.match_id = pit.match_id
        """
    ).fetchone()
    conn.row_factory = tuple_row
    alias_groups = audit_canonical_duplicate_aliases(conn)

    by_action = Counter(p.action for p in proposals)
    by_safety = Counter(p.safety for p in proposals)
    return {
        "generated_at": reference.isoformat(),
        "read_only": True,
        "pending": {
            "total": diagnostics.total,
            "by_reason": diagnostics.by_reason,
            "by_age": diagnostics.by_age,
            "by_flag": diagnostics.by_flag,
            "recovery_window": {
                "start": diagnostics.recovery_window_start.isoformat(),
                "end": diagnostics.recovery_window_end.isoformat(),
            },
        },
        "repair_manifest": {
            "by_action": dict(sorted(by_action.items())),
            "by_safety": dict(sorted(by_safety.items())),
            "proposals": [p.to_payload() for p in proposals],
        },
        "data_quality": {
            "surface_gap": dict(surface_row) if surface_row else {},
            "grand_slam_main_draw_not_best_of_5": (
                int(slam_row["grand_slam_main_draw_not_best_of_5"]) if slam_row else 0
            ),
            "prematch_first_prediction_30d": dict(prematch_row) if prematch_row else {},
            "duplicate_tennis_data_alias_groups": len(alias_groups),
        },
    }


def report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=str)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only historical identity/result repair audit"
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.dsn:
        print("HISTORICAL_REPAIR_AUDIT_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    with psycopg.connect(
        args.dsn, options="-c default_transaction_read_only=on"
    ) as conn:
        report = build_historical_repair_report(
            conn, lookback_days=args.lookback_days, limit=args.limit
        )
    text = report_json(report)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"HISTORICAL_REPAIR_AUDIT wrote={args.output}", flush=True)
    else:
        print(text, flush=True)
    print(
        "HISTORICAL_REPAIR_AUDIT "
        f"pending={report['pending']['total']} "
        f"proposals={len(report['repair_manifest']['proposals'])} "
        f"actions={report['repair_manifest']['by_action']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
