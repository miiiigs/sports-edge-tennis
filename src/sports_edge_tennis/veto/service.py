from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import psycopg

from sports_edge_tennis.market.contracts import MarketDecisionV1, SharpReferenceSpec
from sports_edge_tennis.veto.contracts import (
    PreviousMatchEvidence,
    VetoContext,
    VetoEvaluation,
)

VETO_POLICY_VERSION = "tennis_veto_v1"
_FIXTURE_MAX_AGE = timedelta(hours=6)
_ABSENCE_THRESHOLD = timedelta(weeks=6)
_TIGHT_TURNAROUND = timedelta(hours=18)
_FIVE_SET_TURNAROUND = timedelta(hours=24)
_ODDS_MOVE_THRESHOLD = Decimal("0.15")

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS decision_veto_audits (
    decision_id BIGINT PRIMARY KEY REFERENCES decisions(decision_id),
    veto_policy_version TEXT NOT NULL CHECK (btrim(veto_policy_version) <> ''),
    evaluation_as_of TIMESTAMPTZ NOT NULL,
    vetoed BOOLEAN NOT NULL,
    triggered_codes JSONB NOT NULL,
    unavailable_codes JSONB NOT NULL,
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DO $$
BEGIN
    IF to_regprocedure('raise_append_only_mutation()') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_trigger WHERE tgname = 'trg_decision_veto_audits_append_only'
       ) THEN
        CREATE TRIGGER trg_decision_veto_audits_append_only
        BEFORE UPDATE OR DELETE ON decision_veto_audits
        FOR EACH ROW EXECUTE FUNCTION raise_append_only_mutation();
    END IF;
END;
$$;
"""


class VetoEvidenceError(RuntimeError):
    """Raised when a qualified decision cannot be tied to canonical Veto evidence."""


def _load_target_context(
    conn: psycopg.Connection,
    *,
    match_id: int,
    as_of,
) -> tuple[int, int, object, str, object]:
    row = conn.execute(
        """
        SELECT
            player_1_id,
            player_2_id,
            match_start_at,
            match_status::text,
            source_observed_at
        FROM match_versions
        WHERE match_id = %s
          AND as_of <= %s
          AND source_observed_at <= %s
        ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
        LIMIT 1
        """,
        (match_id, as_of, as_of),
    ).fetchone()
    if row is None or row[2] is None:
        raise VetoEvidenceError("qualified decision has no PIT-visible canonical target match version")
    return int(row[0]), int(row[1]), row[2], str(row[3]), row[4]


def _load_previous_match(
    conn: psycopg.Connection,
    *,
    player_id: int,
    player_side_in_target: int,
    target_match_id: int,
    target_start_at,
    as_of,
) -> PreviousMatchEvidence | None:
    row = conn.execute(
        """
        WITH latest_versions AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id,
                mv.player_1_id,
                mv.player_2_id,
                mv.match_start_at
            FROM match_versions mv
            WHERE mv.as_of <= %s
              AND mv.source_observed_at <= %s
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        ),
        latest_results AS (
            SELECT DISTINCT ON (mro.match_id)
                mro.match_id,
                mro.outcome_status::text,
                mro.retired_side,
                mro.sets_won_player_1,
                mro.sets_won_player_2
            FROM match_result_observations mro
            WHERE mro.as_of <= %s
              AND mro.source_observed_at <= %s
            ORDER BY mro.match_id, mro.as_of DESC, mro.source_observed_at DESC,
                     mro.match_result_observation_id DESC
        )
        SELECT
            lv.match_id,
            lv.player_1_id,
            lv.player_2_id,
            lv.match_start_at,
            lr.outcome_status,
            lr.retired_side,
            lr.sets_won_player_1,
            lr.sets_won_player_2
        FROM latest_versions lv
        JOIN latest_results lr ON lr.match_id = lv.match_id
        WHERE lv.match_id <> %s
          AND lv.match_start_at IS NOT NULL
          AND lv.match_start_at < %s
          AND (%s = lv.player_1_id OR %s = lv.player_2_id)
        ORDER BY lv.match_start_at DESC, lv.match_id DESC
        LIMIT 1
        """,
        (as_of, as_of, as_of, as_of, target_match_id, target_start_at, player_id, player_id),
    ).fetchone()
    if row is None:
        return None

    side_in_previous = 1 if int(row[1]) == player_id else 2
    retired_side = int(row[5]) if row[5] is not None else None
    return PreviousMatchEvidence(
        player_id=player_id,
        player_side_in_target=player_side_in_target,
        match_id=int(row[0]),
        match_start_at=row[3],
        outcome_status=str(row[4]),
        player_retired=(str(row[4]) == "RETIRED" and retired_side == side_in_previous),
        sets_won_player_1=int(row[6]) if row[6] is not None else None,
        sets_won_player_2=int(row[7]) if row[7] is not None else None,
    )


def _load_sharp_price_at(
    conn: psycopg.Connection,
    *,
    match_id: int,
    selected_side: int,
    cutoff,
    sharp_reference: SharpReferenceSpec,
) -> Decimal | None:
    selection_type = "PLAYER_1" if selected_side == 1 else "PLAYER_2"
    row = conn.execute(
        """
        SELECT decimal_price
        FROM odds_snapshots
        WHERE match_id = %s
          AND source_system = %s
          AND bookmaker_code = %s
          AND market_type = 'MATCH_WINNER'
          AND selection_type = %s
          AND market_status = 'OPEN'
          AND source_observed_at <= %s
          AND as_of <= %s
        ORDER BY source_observed_at DESC, as_of DESC, odds_snapshot_id DESC
        LIMIT 1
        """,
        (
            match_id,
            sharp_reference.source_system,
            sharp_reference.bookmaker_code,
            selection_type,
            cutoff,
            cutoff,
        ),
    ).fetchone()
    return Decimal(row[0]) if row is not None else None


def load_veto_context(
    conn: psycopg.Connection,
    *,
    decision: MarketDecisionV1,
    sharp_reference: SharpReferenceSpec,
) -> VetoContext:
    if decision.decision_status != "BET":
        raise VetoEvidenceError("Veto V1 only evaluates an otherwise-qualified BET")
    if decision.selected_side not in (1, 2):
        raise VetoEvidenceError("qualified decision is missing selected_side")

    p1, p2, target_start, current_status, fixture_observed_at = _load_target_context(
        conn,
        match_id=decision.match_id,
        as_of=decision.decision_as_of,
    )
    previous = tuple(
        evidence
        for evidence in (
            _load_previous_match(
                conn,
                player_id=p1,
                player_side_in_target=1,
                target_match_id=decision.match_id,
                target_start_at=target_start,
                as_of=decision.decision_as_of,
            ),
            _load_previous_match(
                conn,
                player_id=p2,
                player_side_in_target=2,
                target_match_id=decision.match_id,
                target_start_at=target_start,
                as_of=decision.decision_as_of,
            ),
        )
        if evidence is not None
    )

    run_row = conn.execute(
        """
        SELECT COALESCE(prediction_finalized_at, as_of)
        FROM prediction_runs
        WHERE prediction_run_id = %s
        """,
        (decision.prediction_run_id,),
    ).fetchone()
    if run_row is None:
        raise VetoEvidenceError("qualified decision has no prediction run")

    baseline = _load_sharp_price_at(
        conn,
        match_id=decision.match_id,
        selected_side=decision.selected_side,
        cutoff=run_row[0],
        sharp_reference=sharp_reference,
    )
    current = decision.sharp_reference_price
    sharp_move = None
    if baseline is not None and current is not None and baseline > 0:
        sharp_move = (Decimal(current) / baseline) - Decimal("1")

    return VetoContext(
        match_id=decision.match_id,
        prediction_run_id=decision.prediction_run_id,
        selected_side=decision.selected_side,
        decision_as_of=decision.decision_as_of,
        target_start_at=target_start,
        current_match_status=current_status,
        fixture_source_observed_at=fixture_observed_at,
        previous_matches=previous,
        sharp_move_against_pct=sharp_move,
    )


def evaluate_veto_context(context: VetoContext) -> VetoEvaluation:
    triggered: list[str] = []
    unavailable: list[str] = [
        "INJURY_72H",
        "PREVIOUS_MATCH_MEDICAL_TIMEOUT",
        "PREVIOUS_MATCH_DURATION",
        "HISTORICAL_SOURCE_FRESHNESS_48H",
    ]
    evidence: list[dict[str, object]] = []

    def trigger(code: str) -> None:
        if code not in triggered:
            triggered.append(code)

    if context.current_match_status != "SCHEDULED":
        trigger("CURRENT_MATCH_NOT_SCHEDULED")
    evidence.append(
        {
            "kind": "TARGET_MATCH",
            "status": context.current_match_status,
            "scheduled_start": context.target_start_at.isoformat(),
            "fixture_source_observed_at": context.fixture_source_observed_at.isoformat(),
        }
    )

    fixture_age = context.decision_as_of - context.fixture_source_observed_at
    if fixture_age > _FIXTURE_MAX_AGE:
        trigger("FIXTURE_FEED_STALE")
    evidence.append({"kind": "FIXTURE_AGE_SECONDS", "value": int(fixture_age.total_seconds())})

    if not context.previous_matches:
        unavailable.append("EXACT_PREVIOUS_MATCH_END_TIME")

    for previous in context.previous_matches:
        gap = context.target_start_at - previous.match_start_at
        gap_seconds = int(gap.total_seconds())
        if previous.player_retired:
            trigger("PREVIOUS_MATCH_RETIRED")
        if gap >= _ABSENCE_THRESHOLD:
            trigger("RETURN_FROM_6W_ABSENCE")
        if timedelta(0) <= gap <= _TIGHT_TURNAROUND:
            # A previous match that started inside 18h necessarily ended even
            # more recently. This is a safe sufficient condition without
            # inventing an exact end timestamp.
            trigger("TIGHT_TURNAROUND_18H")
        elif "EXACT_PREVIOUS_MATCH_END_TIME" not in unavailable:
            unavailable.append("EXACT_PREVIOUS_MATCH_END_TIME")
        if (
            previous.completed_sets == 5
            and timedelta(0) <= gap <= _FIVE_SET_TURNAROUND
        ):
            trigger("FIVE_SET_UNDER_24H")
        evidence.append(
            {
                "kind": "PREVIOUS_MATCH",
                "player_id": previous.player_id,
                "player_side_in_target": previous.player_side_in_target,
                "match_id": previous.match_id,
                "match_start_at": previous.match_start_at.isoformat(),
                "gap_seconds": gap_seconds,
                "outcome_status": previous.outcome_status,
                "player_retired": previous.player_retired,
                "completed_sets": previous.completed_sets,
            }
        )

    if context.sharp_move_against_pct is None:
        unavailable.append("ODDS_MOVE_BASELINE")
    else:
        if context.sharp_move_against_pct > _ODDS_MOVE_THRESHOLD:
            trigger("ODDS_MOVED_AGAINST_15PCT")
        evidence.append(
            {
                "kind": "SHARP_MOVE_AGAINST_PCT",
                "value": str(context.sharp_move_against_pct),
            }
        )

    return VetoEvaluation(
        vetoed=bool(triggered),
        triggered_codes=tuple(triggered),
        unavailable_codes=tuple(dict.fromkeys(unavailable)),
        evidence=tuple(evidence),
    )


def evaluate_veto_for_decision(
    conn: psycopg.Connection,
    *,
    decision: MarketDecisionV1,
    sharp_reference: SharpReferenceSpec,
) -> VetoEvaluation:
    return evaluate_veto_context(
        load_veto_context(conn, decision=decision, sharp_reference=sharp_reference)
    )


def ensure_veto_audit_ledger(conn: psycopg.Connection) -> None:
    conn.execute(_AUDIT_DDL)


def persist_veto_audit(
    conn: psycopg.Connection,
    *,
    decision_id: int,
    evaluation_as_of,
    evaluation: VetoEvaluation,
) -> None:
    ensure_veto_audit_ledger(conn)
    triggered = list(evaluation.triggered_codes)
    unavailable = list(evaluation.unavailable_codes)
    evidence = list(evaluation.evidence)
    inserted = conn.execute(
        """
        INSERT INTO decision_veto_audits (
            decision_id,
            veto_policy_version,
            evaluation_as_of,
            vetoed,
            triggered_codes,
            unavailable_codes,
            evidence
        )
        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
        ON CONFLICT (decision_id) DO NOTHING
        RETURNING decision_id
        """,
        (
            decision_id,
            VETO_POLICY_VERSION,
            evaluation_as_of,
            evaluation.vetoed,
            json.dumps(triggered),
            json.dumps(unavailable),
            json.dumps(evidence, sort_keys=True),
        ),
    ).fetchone()
    if inserted is not None:
        return

    existing = conn.execute(
        """
        SELECT veto_policy_version, evaluation_as_of, vetoed,
               triggered_codes, unavailable_codes, evidence
        FROM decision_veto_audits
        WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    expected = (
        VETO_POLICY_VERSION,
        evaluation_as_of,
        evaluation.vetoed,
        triggered,
        unavailable,
        evidence,
    )
    if existing is None or tuple(existing) != expected:
        raise VetoEvidenceError("persisted Veto audit differs from canonical evaluation")
