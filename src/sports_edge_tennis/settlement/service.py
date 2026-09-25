"""Canonical settlement for executed Tennis Type A match-winner decisions.

Only persisted ``BET`` decisions are financially settled. ``NO_BET`` and
``VETOED`` decisions remain first-class audit history but never become wagers.

Tennis retirement and walkover handling varies by bookmaker. V1 therefore uses a
conservative provider-neutral rule: only a normally ``COMPLETED`` match settles
WON/LOST; ``RETIRED`` and ``WALKOVER`` are VOID; ``CANCELLED`` is CANCELLED.
This avoids inventing bookmaker-specific grading rules we do not possess.

``decision_settlements`` is append-only, so this service inserts only final rows.

Canonical result lineage can legitimately re-observe the *same* sporting outcome
as a new ``match_result_observations`` row (a new ``as_of`` or source
observation). When the derived settlement status is unchanged, that is an
equivalent-result reconciliation: the committed settlement is preserved as
historical truth and downstream work continues. Only a genuinely different
derived settlement status is treated as a conflict, and that fails closed with
explicit telemetry instead of rewriting historical settlement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import psycopg

SETTLEMENT_POLICY_VERSION = "tennis_match_winner_settlement_v1"
SettlementStatus = Literal["WON", "LOST", "VOID", "CANCELLED"]


class SettlementEvidenceConflict(RuntimeError):
    """Raised when immutable settlement no longer matches latest canonical result truth."""


@dataclass(frozen=True)
class SettlementOutcome:
    eligible_decisions: int = 0
    pending_results: int = 0
    inserted: int = 0
    existing: int = 0
    reconciled: int = 0
    won: int = 0
    lost: int = 0
    void: int = 0
    cancelled: int = 0


@dataclass(frozen=True)
class _ResultTruth:
    observation_id: int
    outcome_status: str
    winner_side: int | None


@dataclass(frozen=True)
class _ExistingSettlement:
    observation_id: int | None
    settlement_status: str
    observation_match_id: int | None
    observation_outcome_status: str | None
    observation_winner_side: int | None


def classify_match_winner_settlement(
    *,
    outcome_status: str,
    winner_side: int | None,
    selection_side: int,
) -> SettlementStatus:
    """Map canonical sports result truth to provider-neutral wager settlement."""
    if selection_side not in (1, 2):
        raise ValueError("selection_side must be 1 or 2")

    if outcome_status == "COMPLETED":
        if winner_side not in (1, 2):
            raise ValueError("COMPLETED result must carry winner_side 1 or 2")
        return "WON" if winner_side == selection_side else "LOST"
    if outcome_status in {"RETIRED", "WALKOVER"}:
        return "VOID"
    if outcome_status == "CANCELLED":
        return "CANCELLED"
    raise ValueError(f"unsupported terminal outcome_status: {outcome_status}")


def _latest_result_truth(conn: psycopg.Connection, *, match_id: int) -> _ResultTruth | None:
    row = conn.execute(
        """
        SELECT match_result_observation_id, outcome_status::text, winner_side
        FROM match_result_observations
        WHERE match_id = %s
          AND outcome_status IN ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
        ORDER BY as_of DESC, source_observed_at DESC, match_result_observation_id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        return None
    return _ResultTruth(
        observation_id=int(row[0]),
        outcome_status=str(row[1]),
        winner_side=int(row[2]) if row[2] is not None else None,
    )


def _settlement_notes(result: _ResultTruth, status: SettlementStatus) -> str:
    rule = {
        "WON": "completed_selected_side_won",
        "LOST": "completed_selected_side_lost",
        "VOID": "retired_or_walkover_provider_neutral_void",
        "CANCELLED": "match_cancelled",
    }[status]
    return (
        f"policy={SETTLEMENT_POLICY_VERSION};"
        f"outcome={result.outcome_status};rule={rule}"
    )


def _log(message: str) -> None:
    print(message, flush=True)


def _load_existing_settlement(
    conn: psycopg.Connection,
    *,
    decision_id: int,
) -> _ExistingSettlement | None:
    row = conn.execute(
        """
        SELECT
            ds.match_result_observation_id,
            ds.settlement_status::text,
            mro.match_id,
            mro.outcome_status::text,
            mro.winner_side
        FROM decision_settlements ds
        LEFT JOIN match_result_observations mro
          ON mro.match_result_observation_id = ds.match_result_observation_id
        WHERE ds.decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    if row is None:
        return None
    return _ExistingSettlement(
        observation_id=int(row[0]) if row[0] is not None else None,
        settlement_status=str(row[1]),
        observation_match_id=int(row[2]) if row[2] is not None else None,
        observation_outcome_status=str(row[3]) if row[3] is not None else None,
        observation_winner_side=int(row[4]) if row[4] is not None else None,
    )


def _reconcile_existing(
    conn: psycopg.Connection,
    *,
    decision_id: int,
    match_id: int,
    result: _ResultTruth,
    expected_status: SettlementStatus,
) -> Literal["EXISTING", "RECONCILED"] | None:
    """Compare committed settlement truth against latest canonical result truth.

    Returns ``None`` when no settlement exists, ``"EXISTING"`` when the committed
    row already matches the latest result observation exactly, and
    ``"RECONCILED"`` when the same derived settlement status is evidenced by a
    different canonical result observation row (equivalent-result lineage).
    Raises ``SettlementEvidenceConflict`` for a genuinely different outcome.
    """
    existing = _load_existing_settlement(conn, decision_id=decision_id)
    if existing is None:
        return None
    if existing.settlement_status != expected_status:
        raise SettlementEvidenceConflict(
            "immutable settlement conflicts with latest canonical result truth "
            f"decision_id={decision_id} existing_result_id={existing.observation_id} "
            f"latest_result_id={result.observation_id} existing_status={existing.settlement_status} "
            f"existing_outcome={existing.observation_outcome_status} "
            f"latest_outcome={result.outcome_status} expected_status={expected_status}"
        )
    if existing.observation_id == result.observation_id:
        return "EXISTING"
    if existing.observation_id is None or existing.observation_match_id != match_id:
        raise SettlementEvidenceConflict(
            "settlement lineage references a different canonical match "
            f"decision_id={decision_id} match_id={match_id} "
            f"existing_result_id={existing.observation_id} "
            f"existing_result_match_id={existing.observation_match_id} "
            f"latest_result_id={result.observation_id}"
        )
    _log(
        "SETTLEMENT_EQUIVALENT_RESULT "
        f"decision_id={decision_id} match_id={match_id} "
        f"settlement_status={existing.settlement_status} "
        f"existing_result_id={existing.observation_id} "
        f"latest_result_id={result.observation_id} "
        f"existing_outcome={existing.observation_outcome_status} "
        f"latest_outcome={result.outcome_status}"
    )
    return "RECONCILED"


def settle_type_a_match_winner_decisions(
    conn: psycopg.Connection,
    *,
    settled_at: datetime,
) -> SettlementOutcome:
    """Settle every eligible, result-mature Type A match-winner BET idempotently."""
    if settled_at.tzinfo is None or settled_at.utcoffset() is None:
        raise ValueError("settled_at must be timezone-aware")

    rows = conn.execute(
        """
        SELECT decision_id, match_id, selection_side
        FROM decisions
        WHERE signal_type = 'TYPE_A_MODEL_EDGE'
          AND decision_status = 'BET'
          AND market_type = 'MATCH_WINNER'
          AND selection_side IN (1, 2)
        ORDER BY decision_id
        """
    ).fetchall()

    pending_results = 0
    inserted = 0
    existing = 0
    reconciled = 0
    won = 0
    lost = 0
    void = 0
    cancelled = 0

    for row in rows:
        decision_id = int(row[0])
        match_id = int(row[1])
        selection_side = int(row[2])
        result = _latest_result_truth(conn, match_id=match_id)
        if result is None:
            pending_results += 1
            continue

        status = classify_match_winner_settlement(
            outcome_status=result.outcome_status,
            winner_side=result.winner_side,
            selection_side=selection_side,
        )
        existing_state = _reconcile_existing(
            conn,
            decision_id=decision_id,
            match_id=match_id,
            result=result,
            expected_status=status,
        )
        if existing_state == "EXISTING":
            existing += 1
        elif existing_state == "RECONCILED":
            reconciled += 1
        else:
            created = conn.execute(
                """
                INSERT INTO decision_settlements (
                    decision_id,
                    match_result_observation_id,
                    settlement_status,
                    settled_at,
                    settlement_notes
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (decision_id) DO NOTHING
                RETURNING decision_id
                """,
                (
                    decision_id,
                    result.observation_id,
                    status,
                    settled_at,
                    _settlement_notes(result, status),
                ),
            ).fetchone()
            if created is None:
                # A concurrent settlement worker won the insert race. Reconcile
                # its immutable truth rather than treating that as a failure.
                concurrent_state = _reconcile_existing(
                    conn,
                    decision_id=decision_id,
                    match_id=match_id,
                    result=result,
                    expected_status=status,
                )
                if concurrent_state == "RECONCILED":
                    reconciled += 1
                elif concurrent_state == "EXISTING":
                    existing += 1
                else:
                    raise AssertionError("settlement conflict row disappeared after ON CONFLICT")
            else:
                inserted += 1

        if status == "WON":
            won += 1
        elif status == "LOST":
            lost += 1
        elif status == "VOID":
            void += 1
        elif status == "CANCELLED":
            cancelled += 1

    return SettlementOutcome(
        eligible_decisions=len(rows),
        pending_results=pending_results,
        inserted=inserted,
        existing=existing,
        reconciled=reconciled,
        won=won,
        lost=lost,
        void=void,
        cancelled=cancelled,
    )
