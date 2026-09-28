"""Reviewed cross-provider result reconciliation operator.

Defaults to a read-only DRY RUN. Pass ``--apply`` to persist the reviewed repair
cohort. The operator contains ONLY the seven already-reviewed production mappings;
the actual mutation is delegated to the tested apply service
(:func:`sports_edge_tennis.data.cross_provider_reconciliation.apply_cross_provider_reconciliation`).

Safety properties:

* dry run performs ZERO writes;
* a per-mapping expected WON/LOST assertion fails closed if current database
  evidence no longer implies the reviewed outcome;
* apply mode preflights the entire cohort and aborts before writing if any
  unreconciled row is unsafe or expectation-mismatched;
* the whole cohort is applied inside one outer transaction, so an unexpected
  failure rolls back the rows written during this invocation.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.data.cross_provider_reconciliation import (
    POLICY_VERSION,
    STATUS_ALREADY_RECONCILED,
    STATUS_APPLIED,
    apply_cross_provider_reconciliation,
    evaluate_cross_provider_reconciliation,
)

MODE_DRY_RUN = "DRY_RUN"
MODE_APPLY = "APPLY"

ACTION_WOULD_APPLY = "WOULD_APPLY"
ACTION_ALREADY_RECONCILED = "ALREADY_RECONCILED"
ACTION_UNSAFE = "UNSAFE"
ACTION_EXPECTED_RESULT_MISMATCH = "EXPECTED_RESULT_MISMATCH"

RESULT_WON = "WON"
RESULT_LOST = "LOST"
RESULT_VOID = "VOID"

SELECTION_MANAGER_CONSENSUS = "MANAGER_CONSENSUS"
SELECTION_WIDE_SPREAD = "WIDE_SPREAD_A1_A2"

COHORT_FIRST_PRODUCTION = "first-production"
COHORT_WIDE_SPREAD = "wide-spread-20260924-26"


@dataclass(frozen=True)
class ReviewedMapping:
    target_match_id: int
    evidence_match_id: int
    expected_result: str
    target_source_system: str = "SPORTRADAR"
    evidence_source_system: str = "API_TENNIS"
    selection_source: str = SELECTION_MANAGER_CONSENSUS


# The seven reviewed production mappings. ``expected_result`` is an operator
# safety assertion only; actual outcome is always derived from result evidence.
REVIEWED_MAPPINGS: tuple[ReviewedMapping, ...] = (
    ReviewedMapping(130874, 130832, RESULT_LOST),
    ReviewedMapping(130885, 131089, RESULT_LOST),
    ReviewedMapping(130886, 131069, RESULT_WON),
    ReviewedMapping(130889, 131071, RESULT_LOST),
    ReviewedMapping(130898, 131139, RESULT_LOST),
    ReviewedMapping(131021, 131088, RESULT_LOST),
    ReviewedMapping(131038, 131140, RESULT_LOST),
)

FIRST_PRODUCTION_REVIEWED_COHORT: tuple[ReviewedMapping, ...] = REVIEWED_MAPPINGS

# Second reviewed cohort: A1_A2_WIDE_SPREAD tracking-only picks. Their aligned
# selected side comes from the A1/A2 agreement, NOT the Manager consensus side.
WIDE_SPREAD_REVIEWED_COHORT: tuple[ReviewedMapping, ...] = (
    ReviewedMapping(130873, 130838, RESULT_WON, selection_source=SELECTION_WIDE_SPREAD),
    ReviewedMapping(130884, 131086, RESULT_LOST, selection_source=SELECTION_WIDE_SPREAD),
    ReviewedMapping(130888, 131072, RESULT_LOST, selection_source=SELECTION_WIDE_SPREAD),
    ReviewedMapping(130896, 131093, RESULT_LOST, selection_source=SELECTION_WIDE_SPREAD),
    ReviewedMapping(131027, 131092, RESULT_VOID, selection_source=SELECTION_WIDE_SPREAD),
    ReviewedMapping(130899, 131136, RESULT_WON, selection_source=SELECTION_WIDE_SPREAD),
    ReviewedMapping(130901, 131142, RESULT_LOST, selection_source=SELECTION_WIDE_SPREAD),
)

COHORTS: dict[str, tuple[ReviewedMapping, ...]] = {
    COHORT_FIRST_PRODUCTION: FIRST_PRODUCTION_REVIEWED_COHORT,
    COHORT_WIDE_SPREAD: WIDE_SPREAD_REVIEWED_COHORT,
}


@dataclass(frozen=True)
class MappingAudit:
    target_match_id: int
    evidence_match_id: int
    classification: str | None
    apply_safe: bool
    rejection_reason: str | None
    player_order_mapping: str | None
    target_source_system: str | None
    target_source_match_id: str | None
    evidence_source_system: str | None
    evidence_source_match_id: str | None
    target_player_1_id: int | None
    target_player_2_id: int | None
    evidence_player_1_id: int | None
    evidence_player_2_id: int | None
    target_tournament_id: int | None
    evidence_tournament_id: int | None
    target_season: int | None
    evidence_season: int | None
    target_start_at: datetime | None
    evidence_start_at: datetime | None
    start_delta_minutes: float | None
    evidence_result_observation_id: int | None
    evidence_outcome_status: str | None
    evidence_winner_side: int | None
    translated_winner_side: int | None
    selected_side: int | None
    expected_result: str
    computed_result: str | None
    already_reconciled: bool
    action: str


@dataclass(frozen=True)
class OperatorSummary:
    mode: str
    reviewed: int
    safe: int
    unsafe: int
    expected_result_mismatches: int
    already_reconciled: int
    newly_applied: int
    failed: int
    expected_won: int
    expected_lost: int
    expected_void: int
    aborted: bool
    schema_present: bool = True
    blocker: str | None = None
    audits: tuple[MappingAudit, ...] = ()


def _load_selected_side(conn: psycopg.Connection, match_id: int) -> int | None:
    """Original selected side for the match's production CONSENSUS pick."""
    row = conn.execute(
        """
        SELECT selected_side
        FROM manager_consensus_results
        WHERE match_id = %s
          AND manager_status = 'CONSENSUS'
          AND selected_side IN (1, 2)
        ORDER BY as_of DESC, manager_consensus_result_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    return None if row is None else int(row[0])


def _load_wide_spread_selected_side(conn: psycopg.Connection, match_id: int) -> int | None:
    """Aligned A1/A2 wide-spread selected side, revalidating the exact path.

    Returns None (fail closed) unless the current evidence still matches the
    tracking path: NO_BET + AGENT3_ABSTENTION_SPREAD_EXCEEDED, A1/A2 PREDICTED on
    the same side, A3 ABSTAINED, and A1/A2 mean >= 0.60. Earliest qualifying pick
    mirrors the aligned DISTINCT ON (match_id) ordering.
    """
    row = conn.execute(
        """
        SELECT a1.selection_side
        FROM manager_consensus_results mcr
        JOIN predictions a1 ON a1.prediction_run_id = mcr.prediction_run_id
             AND a1.match_id = mcr.match_id AND a1.agent_code = 'AGENT_1_SURFACE_ELO'
        JOIN predictions a2 ON a2.prediction_run_id = mcr.prediction_run_id
             AND a2.match_id = mcr.match_id AND a2.agent_code = 'AGENT_2_HIERARCHICAL_MARKOV'
        JOIN predictions a3 ON a3.prediction_run_id = mcr.prediction_run_id
             AND a3.match_id = mcr.match_id AND a3.agent_code = 'AGENT_3_INTRANSITIVITY'
        WHERE mcr.match_id = %s
          AND mcr.manager_status = 'NO_BET'
          AND mcr.reason_code = 'AGENT3_ABSTENTION_SPREAD_EXCEEDED'
          AND a1.prediction_status = 'PREDICTED'
          AND a2.prediction_status = 'PREDICTED'
          AND a3.prediction_status = 'ABSTAINED'
          AND a1.selection_side IN (1, 2)
          AND a2.selection_side = a1.selection_side
          AND a1.win_probability IS NOT NULL
          AND a2.win_probability IS NOT NULL
          AND ((a1.win_probability + a2.win_probability) / 2.0) >= 0.60
        ORDER BY mcr.as_of ASC, mcr.manager_consensus_result_id ASC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    return None if row is None else int(row[0])


def _compute_result(
    outcome_status: str | None,
    translated_winner_side: int | None,
    selected_side: int | None,
) -> str | None:
    if selected_side not in (1, 2):
        return None
    if outcome_status == "COMPLETED":
        if translated_winner_side == selected_side:
            return RESULT_WON
        if translated_winner_side in (1, 2):
            return RESULT_LOST
        return None
    if outcome_status in ("RETIRED", "WALKOVER", "CANCELLED"):
        return RESULT_VOID
    return None


def _reconciliation_table_present(conn: psycopg.Connection) -> bool:
    return (
        conn.execute(
            "SELECT to_regclass('public.cross_provider_match_reconciliations')"
        ).fetchone()[0]
        is not None
    )


def _existing_reconciliation(
    conn: psycopg.Connection, *, target_match_id: int, evidence_result_observation_id: int | None
) -> bool:
    if not _reconciliation_table_present(conn):
        return False
    if evidence_result_observation_id is None:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM cross_provider_match_reconciliations
        WHERE canonical_target_match_id = %s
          AND evidence_result_observation_id = %s
          AND reconciliation_policy_version = %s
        """,
        (target_match_id, evidence_result_observation_id, POLICY_VERSION),
    ).fetchone()
    return row is not None


def audit_mapping(
    conn: psycopg.Connection, mapping: ReviewedMapping
) -> MappingAudit:
    """Read-only audit of one reviewed mapping. No writes."""
    evaluation = evaluate_cross_provider_reconciliation(
        conn,
        target_match_id=mapping.target_match_id,
        evidence_match_id=mapping.evidence_match_id,
        target_source_system=mapping.target_source_system,
        evidence_source_system=mapping.evidence_source_system,
    )
    already_reconciled = _existing_reconciliation(
        conn,
        target_match_id=mapping.target_match_id,
        evidence_result_observation_id=evaluation.evidence_result_observation_id,
    )
    if mapping.selection_source == SELECTION_WIDE_SPREAD:
        selected_side = _load_wide_spread_selected_side(conn, mapping.target_match_id)
    else:
        selected_side = _load_selected_side(conn, mapping.target_match_id)
    computed_result = _compute_result(
        evaluation.evidence_outcome_status,
        evaluation.translated_winner_side,
        selected_side,
    )

    if not evaluation.apply_safe:
        action = ACTION_UNSAFE
    elif already_reconciled:
        action = ACTION_ALREADY_RECONCILED
    elif computed_result != mapping.expected_result:
        action = ACTION_EXPECTED_RESULT_MISMATCH
    else:
        action = ACTION_WOULD_APPLY

    return MappingAudit(
        target_match_id=mapping.target_match_id,
        evidence_match_id=mapping.evidence_match_id,
        classification=evaluation.classification,
        apply_safe=evaluation.apply_safe,
        rejection_reason=evaluation.rejection_reason,
        player_order_mapping=evaluation.player_order_mapping,
        target_source_system=evaluation.target_source_system,
        target_source_match_id=evaluation.target_source_match_id,
        evidence_source_system=evaluation.evidence_source_system,
        evidence_source_match_id=evaluation.evidence_source_match_id,
        target_player_1_id=evaluation.target_player_1_id,
        target_player_2_id=evaluation.target_player_2_id,
        evidence_player_1_id=evaluation.evidence_player_1_id,
        evidence_player_2_id=evaluation.evidence_player_2_id,
        target_tournament_id=evaluation.target_tournament_id,
        evidence_tournament_id=evaluation.evidence_tournament_id,
        target_season=evaluation.target_season,
        evidence_season=evaluation.evidence_season,
        target_start_at=evaluation.target_start_at,
        evidence_start_at=evaluation.evidence_start_at,
        start_delta_minutes=evaluation.start_delta_minutes,
        evidence_result_observation_id=evaluation.evidence_result_observation_id,
        evidence_outcome_status=evaluation.evidence_outcome_status,
        evidence_winner_side=evaluation.evidence_winner_side,
        translated_winner_side=evaluation.translated_winner_side,
        selected_side=selected_side,
        expected_result=mapping.expected_result,
        computed_result=computed_result,
        already_reconciled=already_reconciled,
        action=action,
    )


def expected_result_counts(mappings: tuple[ReviewedMapping, ...]) -> dict[str, int]:
    counts = {RESULT_WON: 0, RESULT_LOST: 0, RESULT_VOID: 0}
    for mapping in mappings:
        counts[mapping.expected_result] = counts.get(mapping.expected_result, 0) + 1
    return counts


def _format_audit(audit: MappingAudit) -> str:
    return (
        f"[{audit.action}] target={audit.target_match_id} evidence={audit.evidence_match_id} "
        f"classification={audit.classification} apply_safe={audit.apply_safe} "
        f"reason={audit.rejection_reason} order={audit.player_order_mapping} "
        f"src={audit.target_source_system}:{audit.target_source_match_id}->"
        f"{audit.evidence_source_system}:{audit.evidence_source_match_id} "
        f"players={audit.target_player_1_id},{audit.target_player_2_id}/"
        f"{audit.evidence_player_1_id},{audit.evidence_player_2_id} "
        f"tournament={audit.target_tournament_id}/{audit.evidence_tournament_id} "
        f"season={audit.target_season}/{audit.evidence_season} "
        f"start={audit.target_start_at}/{audit.evidence_start_at} "
        f"delta_min={audit.start_delta_minutes} "
        f"evidence_result={audit.evidence_result_observation_id} "
        f"status={audit.evidence_outcome_status} winner={audit.evidence_winner_side} "
        f"translated_winner={audit.translated_winner_side} selected={audit.selected_side} "
        f"expected={audit.expected_result} computed={audit.computed_result}"
    )


def _summarize(
    mode: str,
    audits: tuple[MappingAudit, ...],
    *,
    aborted: bool,
    newly_applied: int,
    failed: int,
    schema_present: bool = True,
    blocker: str | None = None,
) -> OperatorSummary:
    counts = {RESULT_WON: 0, RESULT_LOST: 0, RESULT_VOID: 0}
    for audit in audits:
        counts[audit.expected_result] = counts.get(audit.expected_result, 0) + 1
    return OperatorSummary(
        mode=mode,
        reviewed=len(audits),
        safe=sum(1 for a in audits if a.apply_safe),
        unsafe=sum(1 for a in audits if a.action == ACTION_UNSAFE),
        expected_result_mismatches=sum(
            1 for a in audits if a.action == ACTION_EXPECTED_RESULT_MISMATCH
        ),
        already_reconciled=sum(1 for a in audits if a.action == ACTION_ALREADY_RECONCILED),
        newly_applied=newly_applied,
        failed=failed,
        expected_won=counts[RESULT_WON],
        expected_lost=counts[RESULT_LOST],
        expected_void=counts[RESULT_VOID],
        aborted=aborted,
        schema_present=schema_present,
        blocker=blocker,
        audits=audits,
    )


def run_operator(
    conn: psycopg.Connection,
    *,
    apply: bool = False,
    mappings: tuple[ReviewedMapping, ...] = REVIEWED_MAPPINGS,
    reviewed_at: datetime | None = None,
    emit=print,
) -> OperatorSummary:
    """Audit (and optionally apply) the reviewed cohort.

    Phase A audits every mapping read-only. In apply mode, if any unreconciled row
    is unsafe or expectation-mismatched the whole cohort is aborted before any
    write. Phase B applies every WOULD_APPLY mapping inside one outer transaction.
    """
    schema_present = _reconciliation_table_present(conn)
    if not schema_present:
        emit(
            "RECONCILIATION_SCHEMA_MISSING migration 0016 not applied; "
            "prior-reconciliation state unavailable"
        )

    audits = tuple(audit_mapping(conn, mapping) for mapping in mappings)
    for audit in audits:
        emit(_format_audit(audit))

    blocking = any(
        audit.action not in (ACTION_WOULD_APPLY, ACTION_ALREADY_RECONCILED)
        for audit in audits
    )

    if not apply:
        summary = _summarize(
            MODE_DRY_RUN,
            audits,
            aborted=False,
            newly_applied=0,
            failed=0,
            schema_present=schema_present,
        )
        emit(
            f"SUMMARY mode={summary.mode} reviewed={summary.reviewed} safe={summary.safe} "
            f"unsafe={summary.unsafe} mismatches={summary.expected_result_mismatches} "
            f"already_reconciled={summary.already_reconciled} newly_applied={summary.newly_applied} "
            f"failed={summary.failed} won={summary.expected_won} lost={summary.expected_lost} "
            f"void={summary.expected_void}"
        )
        return summary

    if blocking or not schema_present:
        summary = _summarize(
            MODE_APPLY,
            audits,
            aborted=True,
            newly_applied=0,
            failed=0,
            schema_present=schema_present,
            blocker=None if schema_present else "RECONCILIATION_SCHEMA_MISSING",
        )
        emit(
            f"SUMMARY mode={summary.mode} aborted=True reviewed={summary.reviewed} "
            f"safe={summary.safe} unsafe={summary.unsafe} "
            f"mismatches={summary.expected_result_mismatches} "
            f"already_reconciled={summary.already_reconciled} newly_applied=0 "
            f"won={summary.expected_won} lost={summary.expected_lost} void={summary.expected_void}"
        )
        return summary

    run_reviewed_at = reviewed_at or datetime.now(UTC)
    newly_applied = 0
    failed = 0
    with conn.transaction():
        for mapping, audit in zip(mappings, audits):
            if audit.action != ACTION_WOULD_APPLY:
                continue
            result = apply_cross_provider_reconciliation(
                conn,
                target_match_id=mapping.target_match_id,
                evidence_match_id=mapping.evidence_match_id,
                reviewed_at=run_reviewed_at,
                target_source_system=mapping.target_source_system,
                evidence_source_system=mapping.evidence_source_system,
            )
            if result.status == STATUS_APPLIED:
                newly_applied += 1
            elif result.status == STATUS_ALREADY_RECONCILED:
                pass
            else:
                failed += 1
                raise RuntimeError(
                    f"unexpected apply status {result.status} for target={mapping.target_match_id}"
                )

    summary = _summarize(
        MODE_APPLY,
        audits,
        aborted=False,
        newly_applied=newly_applied,
        failed=failed,
        schema_present=schema_present,
    )
    emit(
        f"SUMMARY mode={summary.mode} reviewed={summary.reviewed} safe={summary.safe} "
        f"unsafe={summary.unsafe} mismatches={summary.expected_result_mismatches} "
        f"already_reconciled={summary.already_reconciled} newly_applied={summary.newly_applied} "
        f"failed={summary.failed} won={summary.expected_won} lost={summary.expected_lost} "
        f"void={summary.expected_void}"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist the reviewed cohort (default: read-only dry run)",
    )
    parser.add_argument(
        "--cohort",
        choices=sorted(COHORTS),
        default=COHORT_FIRST_PRODUCTION,
        help="reviewed cohort to run",
    )
    args = parser.parse_args(argv)

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("RECONCILE_OPERATOR_BLOCKED missing_dsn")
        return 2

    conn = psycopg.connect(dsn, autocommit=False)
    committed = False
    try:
        summary = run_operator(conn, apply=args.apply, mappings=COHORTS[args.cohort])
        if (
            args.apply
            and not summary.aborted
            and summary.blocker is None
            and summary.failed == 0
        ):
            conn.commit()
            committed = True
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(
        "COMMIT mode="
        + (MODE_APPLY if args.apply else MODE_DRY_RUN)
        + " committed="
        + ("true" if committed else "false")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
