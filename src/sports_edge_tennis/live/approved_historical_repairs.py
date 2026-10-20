"""Owner-approved, append-only historical repair preparation.

This module never runs automatically. It builds a deterministic dry-run plan, and
only writes when the operator passes both the exact approval phrase and the plan
SHA-256. Every write is append-only and idempotent.

Two repairs are supported:

* ``132211-walkover-void``: append a WALKOVER result with no winner and no
  scoreline. Settlement policy maps WALKOVER to VOID. This requires migration
  ``0021_walkover_winner_optional`` so a provider-reported walkover does not
  require inventing a winner.
* ``131036-reconcile-131137``: append a manual cross-provider reconciliation
  record linking the unresolved target to the verified evidence result. It does
  not copy the result, merge matches, or change player identity. The automatic
  ``cross_provider_exact_match_v1`` policy rejects this pair because the start
  difference is 40 minutes; the owner must explicitly accept the manual policy.

Match 130717 remains intentionally unsupported and blocked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from sports_edge_tennis.data.cross_provider_reconciliation import (
    POLICY_VERSION as AUTO_POLICY_VERSION,
)
from sports_edge_tennis.data.cross_provider_reconciliation import (
    evaluate_cross_provider_reconciliation,
)

REPAIR_WALKOVER = "132211-walkover-void"
REPAIR_RECONCILE = "131036-reconcile-131137"
REPAIR_130717 = "130717-unresolved"

APPROVAL_PHRASE = "OWNER_APPROVED_TENNIS_HISTORICAL_REPAIR_20261020"
MANUAL_RECONCILIATION_POLICY = "owner_approved_historical_duplicate_v1"
WALKOVER_SOURCE_OBSERVED_AT = datetime(2026, 10, 17, 8, 12, 18, 698820, tzinfo=UTC)
RECONCILIATION_REVIEWED_AT = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)

STATUS_READY = "READY_FOR_OWNER_APPROVAL"
STATUS_ALREADY_APPLIED = "ALREADY_APPLIED"
STATUS_BLOCKED = "BLOCKED"
STATUS_APPLIED = "APPLIED"
STATUS_STALE_PLAN = "STALE_PLAN"
STATUS_UNAUTHORIZED = "UNAUTHORIZED"


@dataclass(frozen=True)
class PlannedStatement:
    sql: str
    params: tuple[Any, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "params": [str(value) for value in self.params],
        }


@dataclass(frozen=True)
class RepairPlan:
    repair_id: str
    status: str
    target_match_id: int
    evidence_match_id: int | None
    apply_safe: bool
    blockers: tuple[str, ...]
    owner_questions: tuple[str, ...]
    before: dict[str, Any]
    after: dict[str, Any] | None
    evidence: dict[str, Any]
    statements: tuple[PlannedStatement, ...]
    plan_sha256: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "repair_id": self.repair_id,
            "status": self.status,
            "target_match_id": self.target_match_id,
            "evidence_match_id": self.evidence_match_id,
            "apply_safe": self.apply_safe,
            "blockers": list(self.blockers),
            "owner_questions": list(self.owner_questions),
            "before": self.before,
            "after": self.after,
            "evidence": self.evidence,
            "statements": [statement.to_payload() for statement in self.statements],
            "plan_sha256": self.plan_sha256,
        }


def _preserve_row_factory(function):
    def wrapper(conn: psycopg.Connection, *args, **kwargs):
        previous = conn.row_factory
        try:
            return function(conn, *args, **kwargs)
        finally:
            conn.row_factory = previous

    return wrapper


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _match_snapshot(conn: psycopg.Connection, match_id: int) -> dict[str, Any] | None:
    conn.row_factory = dict_row
    row = conn.execute(
        """
        SELECT mv.match_id, mv.match_version_id, mv.player_1_id, mv.player_2_id,
               p1.canonical_name AS player_1, p2.canonical_name AS player_2,
               mv.match_start_at, mv.round_code, mv.match_status::text AS match_status,
               mv.surface::text AS surface, mv.best_of_sets, mv.qualifying_flag,
               mv.source_observed_at, mv.as_of,
               te.tournament_edition_id, te.tournament_id, te.season_year,
               t.canonical_name AS tournament, t.competition_tier::text AS competition_tier,
               mv.tour_scope::text AS tour_scope
        FROM match_versions mv
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te
          ON te.tournament_edition_id = m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id = te.tournament_id
        JOIN players p1 ON p1.player_id = mv.player_1_id
        JOIN players p2 ON p2.player_id = mv.player_2_id
        WHERE mv.match_id = %s
        ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    return None if row is None else dict(row)


def _source_ids(conn: psycopg.Connection, match_id: int) -> list[dict[str, Any]]:
    conn.row_factory = dict_row
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT source_system, source_match_id
            FROM match_source_ids
            WHERE match_id = %s
            ORDER BY source_system, source_match_id
            """,
            (match_id,),
        ).fetchall()
    ]


def _all_terminal_results(
    conn: psycopg.Connection, match_id: int
) -> list[dict[str, Any]]:
    conn.row_factory = dict_row
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT match_result_observation_id, outcome_status::text AS outcome_status,
                   winner_side, scoreline_text, source_observed_at, as_of
            FROM match_result_observations
            WHERE match_id = %s
              AND outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
            ORDER BY as_of, match_result_observation_id
            """,
            (match_id,),
        ).fetchall()
    ]


def _bets(conn: psycopg.Connection, match_id: int) -> list[dict[str, Any]]:
    conn.row_factory = dict_row
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT decision_id, decision_status, selection_side
            FROM decisions
            WHERE match_id = %s
              AND signal_type = 'TYPE_A_MODEL_EDGE'
              AND market_type = 'MATCH_WINNER'
            ORDER BY decision_id
            """,
            (match_id,),
        ).fetchall()
    ]


def _settlement_count(conn: psycopg.Connection, decision_ids: list[int]) -> int:
    if not decision_ids:
        return 0
    row = conn.execute(
        "SELECT count(*) AS count FROM decision_settlements WHERE decision_id = ANY(%s)",
        (decision_ids,),
    ).fetchone()
    return int(row["count"])


def _migration_0021_applied(conn: psycopg.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'match_result_observations'::regclass
          AND conname = 'chk_match_result_terminal_winner'
        """
    ).fetchone()
    return row is not None


def _existing_reconciliation(
    conn: psycopg.Connection,
    *,
    target_match_id: int,
    evidence_result_observation_id: int,
    policy_version: str,
) -> int | None:
    row = conn.execute(
        """
        SELECT reconciliation_id
        FROM cross_provider_match_reconciliations
        WHERE canonical_target_match_id = %s
          AND evidence_result_observation_id = %s
          AND reconciliation_policy_version = %s
        """,
        (target_match_id, evidence_result_observation_id, policy_version),
    ).fetchone()
    return None if row is None else int(row["reconciliation_id"])


def _stable_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Drop runtime-only fields so the owner can approve a stable plan hash."""
    stable = dict(evidence)
    stable.pop("reviewed_at", None)
    return stable


def _plan_payload(
    *,
    repair_id: str,
    target_match_id: int,
    evidence_match_id: int | None,
    evidence: dict[str, Any],
    statements: tuple[PlannedStatement, ...],
) -> dict[str, Any]:
    return {
        "repair_id": repair_id,
        "target_match_id": target_match_id,
        "evidence_match_id": evidence_match_id,
        "evidence": _stable_evidence(evidence),
        "statements": [{"sql": statement.sql} for statement in statements],
    }


@_preserve_row_factory
def build_walkover_plan(
    conn: psycopg.Connection, *, now: datetime | None = None
) -> RepairPlan:
    reference = now or datetime.now(UTC)
    target_id = 132211
    snapshot = _match_snapshot(conn, target_id)
    if snapshot is None:
        raise RuntimeError("match 132211 not found")
    results = _all_terminal_results(conn, target_id)
    decisions = _bets(conn, target_id)
    bet_rows = [d for d in decisions if d["decision_status"] == "BET"]
    bet_ids = [int(d["decision_id"]) for d in bet_rows]
    settlements = _settlement_count(conn, bet_ids)
    walkover_rows = conn.execute(
        """
        SELECT min(source_observed_at) AS first_observed, min(as_of) AS first_as_of
        FROM match_versions
        WHERE match_id = %s AND match_status = 'WALKOVER'
        """,
        (target_id,),
    ).fetchone()
    earliest_observed = walkover_rows["first_observed"] if walkover_rows else None
    already_applied = (
        len(results) == 1
        and results[0]["outcome_status"] == "WALKOVER"
        and results[0]["winner_side"] is None
    )

    blockers: list[str] = []
    if snapshot["match_status"] != "WALKOVER":
        blockers.append(f"LATEST_STATUS_NOT_WALKOVER:{snapshot['match_status']}")
    if results and not already_applied:
        blockers.append("TERMINAL_RESULT_ALREADY_EXISTS")
    if not _migration_0021_applied(conn):
        blockers.append("MIGRATION_0021_NOT_APPLIED")
    if len(bet_rows) != 12 or any(int(d["selection_side"] or 0) != 1 for d in bet_rows):
        blockers.append(
            f"EXPECTED_12_SIDE_1_BETS:found={len(bet_rows)}"
            f":sides={sorted({int(d['selection_side'] or 0) for d in bet_rows})}"
        )
    if settlements:
        blockers.append(f"SETTLEMENTS_ALREADY_EXIST:{settlements}")
    if earliest_observed is None:
        blockers.append("NO_EXPLICIT_WALKOVER_OBSERVATION")

    observed_at = earliest_observed or WALKOVER_SOURCE_OBSERVED_AT
    statements = (
        PlannedStatement(
            sql="""
            WITH run AS (
                INSERT INTO ingest_runs (
                    source_system, source_dataset, started_at, finished_at, status,
                    source_version, rows_seen, rows_inserted
                )
                VALUES (
                    'OPERATOR_CORRECTION', 'approved_historical_repair_walkover_v1',
                    %s, %s, 'COMPLETED', 'phase2_20261020', 1, 1
                )
                RETURNING ingest_run_id
            )
            INSERT INTO match_result_observations (
                match_id, outcome_status, winner_side, scoreline_text,
                source_observed_at, as_of, ingest_run_id
            )
            SELECT %s, 'WALKOVER', NULL, NULL, %s, %s, run.ingest_run_id
            FROM run
            WHERE NOT EXISTS (
                SELECT 1 FROM match_result_observations
                WHERE match_id = %s
                  AND outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
            )
            RETURNING match_result_observation_id
            """,
            params=(
                reference,
                reference,
                target_id,
                observed_at,
                observed_at,
                target_id,
            ),
        ),
    )
    evidence = {
        "match": snapshot,
        "source_ids": _source_ids(conn, target_id),
        "explicit_walkover_first_observed_at": _iso(observed_at),
        "explicit_walkover_latest_observed_at": _iso(snapshot["source_observed_at"]),
        "bet_decision_ids": bet_ids,
        "bet_count": len(bet_rows),
        "settlement_count": settlements,
        "provider_winner_present": False,
        "provider_score_present": False,
        "bracket_note": (
            "132233 (J. Kym v M. Brunold) was played after this walkover and Kym "
            "lost it; D. Stricker has no later same-tournament match."
        ),
    }
    payload = _plan_payload(
        repair_id=REPAIR_WALKOVER,
        target_match_id=target_id,
        evidence_match_id=None,
        evidence=evidence,
        statements=statements,
    )
    status = (
        STATUS_ALREADY_APPLIED
        if already_applied
        else (STATUS_READY if not blockers else STATUS_BLOCKED)
    )
    return RepairPlan(
        repair_id=REPAIR_WALKOVER,
        status=status,
        target_match_id=target_id,
        evidence_match_id=None,
        apply_safe=not blockers and not already_applied,
        blockers=tuple(blockers),
        owner_questions=(
            (
                "Do you approve appending WALKOVER with winner_side=NULL and no "
                "scoreline for match 132211?"
            ),
            (
                "Do you approve applying migration 0021_walkover_winner_optional first, "
                "which only relaxes WALKOVER and keeps COMPLETED/RETIRED winner-required?"
            ),
            (
                "Do you approve settlement mapping the 12 existing side-1 BET decisions "
                "to VOID, with no WON/LOST outcome?"
            ),
        ),
        before={
            "terminal_result": None,
            "latest_status": snapshot["match_status"],
            "bet_count": len(bet_rows),
            "bet_sides": sorted({int(d["selection_side"] or 0) for d in bet_rows}),
            "settlement_count": settlements,
        },
        after={
            "outcome_status": "WALKOVER",
            "winner_side": None,
            "scoreline_text": None,
            "settlement_status": "VOID",
            "bet_count_unchanged": len(bet_rows),
            "prediction_rows_unchanged": True,
        },
        evidence=evidence,
        statements=statements,
        plan_sha256=_sha256(payload),
    )


def _normalised_round(value: str | None) -> str:
    if not value:
        return ""
    mapping = {
        "final": "F",
        "semifinal": "SF",
        "quarterfinal": "QF",
        "round_of_16": "R16",
        "round_of_32": "R32",
        "round_of_64": "R64",
        "round_of_128": "R128",
    }
    return mapping.get(value.strip().lower(), value.strip().upper())


@_preserve_row_factory
def build_reconciliation_plan(
    conn: psycopg.Connection, *, now: datetime | None = None
) -> RepairPlan:
    reference = now or datetime.now(UTC)
    target_id = 131036
    evidence_id = 131137
    target = _match_snapshot(conn, target_id)
    evidence_match = _match_snapshot(conn, evidence_id)
    if target is None or evidence_match is None:
        raise RuntimeError("target or evidence match not found")
    target_results = _all_terminal_results(conn, target_id)
    evidence_results = _all_terminal_results(conn, evidence_id)
    target_decisions = _bets(conn, target_id)
    target_bets = [d for d in target_decisions if d["decision_status"] == "BET"]
    target_sources = {
        row["source_system"]: row["source_match_id"]
        for row in _source_ids(conn, target_id)
    }
    evidence_sources = {
        row["source_system"]: row["source_match_id"]
        for row in _source_ids(conn, evidence_id)
    }
    if not evidence_results:
        raise RuntimeError("evidence match 131137 has no terminal result")
    evidence_result = evidence_results[-1]
    existing = _existing_reconciliation(
        conn,
        target_match_id=target_id,
        evidence_result_observation_id=int(
            evidence_result["match_result_observation_id"]
        ),
        policy_version=MANUAL_RECONCILIATION_POLICY,
    )

    blockers: list[str] = []
    if target_results:
        blockers.append("TARGET_ALREADY_HAS_TERMINAL_RESULT")
    if target_bets:
        blockers.append(f"TARGET_HAS_BET_DECISIONS:{len(target_bets)}")
    if (
        target["player_1_id"] != evidence_match["player_1_id"]
        or target["player_2_id"] != evidence_match["player_2_id"]
    ):
        blockers.append("PLAYER_ID_OR_ORIENTATION_MISMATCH")
    if target["tournament_id"] != evidence_match["tournament_id"]:
        blockers.append("TOURNAMENT_ID_MISMATCH")
    if target["season_year"] != evidence_match["season_year"]:
        blockers.append("SEASON_MISMATCH")
    if target["tour_scope"] != evidence_match["tour_scope"]:
        blockers.append("TOUR_SCOPE_MISMATCH")
    if target["competition_tier"] != evidence_match["competition_tier"]:
        blockers.append("COMPETITION_TIER_MISMATCH")
    if _normalised_round(target["round_code"]) != _normalised_round(
        evidence_match["round_code"]
    ):
        blockers.append("ROUND_MISMATCH")
    if target["match_start_at"].date() != evidence_match["match_start_at"].date():
        blockers.append("DATE_MISMATCH")
    start_delta_minutes = (
        abs(
            (
                target["match_start_at"] - evidence_match["match_start_at"]
            ).total_seconds()
        )
        / 60.0
    )
    if start_delta_minutes > 6 * 60:
        blockers.append(f"START_DELTA_TOO_LARGE:{start_delta_minutes}")
    if "SPORTRADAR" not in target_sources:
        blockers.append("TARGET_SPORTRADAR_SOURCE_MISSING")
    if "API_TENNIS" not in evidence_sources:
        blockers.append("EVIDENCE_API_TENNIS_SOURCE_MISSING")
    if evidence_result["outcome_status"] not in {
        "COMPLETED",
        "RETIRED",
        "WALKOVER",
        "CANCELLED",
    }:
        blockers.append("EVIDENCE_RESULT_NOT_TERMINAL")

    conn.row_factory = tuple_row
    try:
        automatic = evaluate_cross_provider_reconciliation(
            conn,
            target_match_id=target_id,
            evidence_match_id=evidence_id,
            target_source_system="SPORTRADAR",
            evidence_source_system="API_TENNIS",
        )
    finally:
        conn.row_factory = dict_row
    automatic_payload = {
        "policy_version": AUTO_POLICY_VERSION,
        "apply_safe": automatic.apply_safe,
        "rejection_reason": automatic.rejection_reason,
        "start_delta_minutes": automatic.start_delta_minutes,
    }

    payload_evidence = {
        "policy_version": MANUAL_RECONCILIATION_POLICY,
        "approval_note": (
            "Manual owner-approved duplicate review. The automatic policy rejects "
            "this pair on a 40-minute start difference; the owner must accept the "
            "same players, tournament, season, round and evidence result."
        ),
        "automatic_policy": automatic_payload,
        "target_match_id": target_id,
        "evidence_match_id": evidence_id,
        "target_source_system": "SPORTRADAR",
        "target_source_match_id": target_sources.get("SPORTRADAR"),
        "evidence_source_system": "API_TENNIS",
        "evidence_source_match_id": evidence_sources.get("API_TENNIS"),
        "target_player_1_id": target["player_1_id"],
        "target_player_2_id": target["player_2_id"],
        "evidence_player_1_id": evidence_match["player_1_id"],
        "evidence_player_2_id": evidence_match["player_2_id"],
        "player_order_mapping": "DIRECT",
        "target_tournament_id": target["tournament_id"],
        "evidence_tournament_id": evidence_match["tournament_id"],
        "target_tournament_edition_id": target["tournament_edition_id"],
        "evidence_tournament_edition_id": evidence_match["tournament_edition_id"],
        "target_season": target["season_year"],
        "evidence_season": evidence_match["season_year"],
        "target_start_at": _iso(target["match_start_at"]),
        "evidence_start_at": _iso(evidence_match["match_start_at"]),
        "start_delta_minutes": start_delta_minutes,
        "target_round": target["round_code"],
        "evidence_round": evidence_match["round_code"],
        "target_status": target["match_status"],
        "evidence_status": evidence_match["match_status"],
        "evidence_result_observation_id": evidence_result[
            "match_result_observation_id"
        ],
        "evidence_outcome_status": evidence_result["outcome_status"],
        "evidence_winner_side": evidence_result["winner_side"],
        "evidence_scoreline_text": evidence_result["scoreline_text"],
        "evidence_source_observed_at": _iso(evidence_result["source_observed_at"]),
        "evidence_as_of": _iso(evidence_result["as_of"]),
        "reviewed_at": _iso(reference),
        "target_result_written": False,
        "identity_merge": False,
    }
    statements = (
        PlannedStatement(
            sql="""
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
            VALUES (
                %s, %s, 'SPORTRADAR', %s, 'API_TENNIS', %s, %s,
                'EXACT_CROSS_PROVIDER_MATCH', 'DIRECT', %s, %s, %s
            )
            ON CONFLICT (
                canonical_target_match_id,
                evidence_result_observation_id,
                reconciliation_policy_version
            ) DO NOTHING
            RETURNING reconciliation_id
            """,
            params=(
                target_id,
                evidence_id,
                target_sources.get("SPORTRADAR"),
                evidence_sources.get("API_TENNIS"),
                evidence_result["match_result_observation_id"],
                MANUAL_RECONCILIATION_POLICY,
                reference,
                Jsonb(payload_evidence),
            ),
        ),
    )
    status = (
        STATUS_ALREADY_APPLIED
        if existing
        else (STATUS_READY if not blockers else STATUS_BLOCKED)
    )
    payload = _plan_payload(
        repair_id=REPAIR_RECONCILE,
        target_match_id=target_id,
        evidence_match_id=evidence_id,
        evidence=payload_evidence,
        statements=statements,
    )
    return RepairPlan(
        repair_id=REPAIR_RECONCILE,
        status=status,
        target_match_id=target_id,
        evidence_match_id=evidence_id,
        apply_safe=not blockers and existing is None,
        blockers=tuple(blockers),
        owner_questions=(
            (
                "Do you accept a manual reconciliation when the automatic policy "
                "rejects the 40-minute start difference?"
            ),
            (
                "Do you approve appending only the reconciliation provenance row, with "
                "no target result copy, no match merge and no player identity change?"
            ),
            (
                "Do you confirm that 131036 has no BET decisions, so this correction "
                "does not change any settled or unsettled wager?"
            ),
        ),
        before={
            "target_terminal_result": None,
            "target_bet_count": len(target_bets),
            "evidence_result_observation_id": evidence_result[
                "match_result_observation_id"
            ],
            "automatic_policy_rejection": automatic.rejection_reason,
        },
        after={
            "reconciliation_policy_version": MANUAL_RECONCILIATION_POLICY,
            "reconciliation_rows_added": 1,
            "target_result_written": False,
            "identity_merge": False,
            "target_bet_count_unchanged": len(target_bets),
        },
        evidence=payload_evidence,
        statements=statements,
        plan_sha256=_sha256(payload),
    )


def build_blocked_130717_plan() -> RepairPlan:
    return RepairPlan(
        repair_id=REPAIR_130717,
        status=STATUS_BLOCKED,
        target_match_id=130717,
        evidence_match_id=None,
        apply_safe=False,
        blockers=(
            "NO_AUTHORITATIVE_TERMINAL_RESULT",
            "BRACKET_ADVANCEMENT_DOES_NOT_PROVE_COMPLETION_TYPE",
        ),
        owner_questions=(
            (
                "Do you have an authoritative result for 130717 (K. Smith v E. Winter) "
                "from a provider or official source?"
            ),
            (
                "If not, should it remain unresolved rather than being settled as "
                "WON/LOST or VOID?"
            ),
        ),
        before={
            "latest_status": "SCHEDULED",
            "bet_count": 10,
            "bet_sides": [2],
            "terminal_result": None,
        },
        after=None,
        evidence={
            "match_id": 130717,
            "player_1": "K. Smith",
            "player_2": "E. Winter",
            "match_start_at": "2026-09-21T22:40:00+00:00",
            "note": (
                "Bracket advancement proves K. Smith later played 130805, but it "
                "does not prove COMPLETED versus WALKOVER/RETIRED/CANCELLED."
            ),
        },
        statements=(),
        plan_sha256=_sha256({"repair_id": REPAIR_130717, "status": STATUS_BLOCKED}),
    )


@_preserve_row_factory
def build_plan(
    conn: psycopg.Connection, *, repair_id: str, now: datetime | None = None
) -> RepairPlan:
    if repair_id == REPAIR_WALKOVER:
        return build_walkover_plan(conn, now=now)
    if repair_id == REPAIR_RECONCILE:
        return build_reconciliation_plan(conn, now=now)
    if repair_id == REPAIR_130717:
        return build_blocked_130717_plan()
    raise ValueError(f"unsupported repair: {repair_id}")


@_preserve_row_factory
def apply_plan(
    conn: psycopg.Connection,
    *,
    repair_id: str,
    authorization: str,
    expected_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if authorization != APPROVAL_PHRASE:
        return {"status": STATUS_UNAUTHORIZED, "reason": "approval phrase mismatch"}
    fresh = build_plan(conn, repair_id=repair_id, now=now)
    if expected_sha256 != fresh.plan_sha256:
        return {
            "status": STATUS_STALE_PLAN,
            "reason": "plan hash changed; re-review the dry-run manifest",
            "fresh_plan": fresh.to_payload(),
        }
    if fresh.status == STATUS_ALREADY_APPLIED:
        return {"status": STATUS_ALREADY_APPLIED, "plan": fresh.to_payload()}
    if not fresh.apply_safe:
        return {
            "status": STATUS_BLOCKED,
            "blockers": list(fresh.blockers),
            "plan": fresh.to_payload(),
        }

    applied: list[dict[str, Any]] = []
    with conn.transaction():
        for statement in fresh.statements:
            row = conn.execute(statement.sql, statement.params).fetchone()
            applied.append({"row": list(row) if row else None})
    return {"status": STATUS_APPLIED, "applied": applied, "plan": fresh.to_payload()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Owner-approved append-only historical repair preparation"
    )
    parser.add_argument(
        "--repair",
        required=True,
        choices=(REPAIR_WALKOVER, REPAIR_RECONCILE, REPAIR_130717),
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorization")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.dsn:
        print("APPROVED_REPAIR_BLOCKED reason=NO_DATABASE", flush=True)
        return 2
    if args.apply and (not args.authorization or not args.expected_sha256):
        print(
            "APPROVED_REPAIR_BLOCKED reason=APPLY_REQUIRES_AUTHORIZATION_AND_SHA",
            flush=True,
        )
        return 2
    options = "-c default_transaction_read_only=on" if not args.apply else None
    with psycopg.connect(args.dsn, options=options) as conn:
        if args.apply:
            result = apply_plan(
                conn,
                repair_id=args.repair,
                authorization=args.authorization or "",
                expected_sha256=args.expected_sha256 or "",
            )
        else:
            result = build_plan(conn, repair_id=args.repair).to_payload()
    text = json.dumps(result, indent=2, default=str)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"APPROVED_REPAIR wrote={args.output}", flush=True)
    else:
        print(text, flush=True)
    print(
        "APPROVED_REPAIR_SUMMARY "
        f"repair={args.repair} status={result.get('status')} "
        f"apply={args.apply}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
