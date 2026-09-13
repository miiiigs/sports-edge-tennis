"""Canonical settlement for executed Tennis Type A match-winner decisions.

Only persisted ``BET`` decisions are financially settled. ``NO_BET`` and
``VETOED`` decisions remain first-class audit history but never become wagers.

Tennis retirement and walkover handling varies by bookmaker. V1 therefore uses a
conservative provider-neutral rule: only a normally ``COMPLETED`` match settles
WON/LOST; ``RETIRED`` and ``WALKOVER`` are VOID; ``CANCELLED`` is CANCELLED.
This avoids inventing bookmaker-specific grading rules we do not possess.

``decision_settlements`` is append-only, so this service inserts only final rows.
If later result truth differs from already-settled evidence, the service fails
closed with an explicit conflict instead of rewriting historical settlement.
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
    won: int = 0
    lost: int = 0
    void: int = 0
    cancelled: int = 0


@dataclass(frozen=True)
class _ResultTruth:
    observation_id: int
    outcome_status: str
    winner_side: int | None


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


def _verify_existing(
    conn: psycopg.Connection,
    *,
    decision_id: int,
    result: _ResultTruth,
    expected_status: SettlementStatus,
) -> bool:
    row = conn.execute(
        """
        SELECT match_result_observation_id, settlement_status::text
        FROM decision_settlements
        WHERE decision_id = %s
        """,
        (decision_id,),
    ).fetchone()
    if row is None:
        return False
    existing_result_id = int(row[0]) if row[0] is not None else None
    existing_status = str(row[1])
    if existing_result_id != result.observation_id or existing_status != expected_status:
        raise SettlementEvidenceConflict(
            "immutable settlement conflicts with latest canonical result truth "
            f"decision_id={decision_id} existing_result_id={existing_result_id} "
            f"latest_result_id={result.observation_id} existing_status={existing_status} "
            f"expected_status={expected_status}"
        )
    return True


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
        if _verify_existing(
            conn,
            decision_id=decision_id,
            result=result,
            expected_status=status,
        ):
            existing += 1
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
                # A concurrent settlement worker won the insert race. Verify its
                # immutable truth rather than treating that as a failure.
                if not _verify_existing(
                    conn,
                    decision_id=decision_id,
                    result=result,
                    expected_status=status,
                ):
                    raise AssertionError("settlement conflict row disappeared after ON CONFLICT")
                existing += 1
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
        won=won,
        lost=lost,
        void=void,
        cancelled=cancelled,
    )
