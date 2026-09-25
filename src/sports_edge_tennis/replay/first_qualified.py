"""First-qualified-per-sporting-match accounting.

Hourly production/replay runs must not duplicate performance observations. The
same sporting match may appear in many runs; it enters the ledger once, at its
first qualification.

Serveral timing scenarios are covered by the ordering:

- a match first Manager-qualifies in run 1 but only Value-Bet-qualifies in run 2;
- odds move after first qualification and trigger a later qualification;
- later hourly runs produce additional qualifications for the same match;
- cancelled / walkover / retired matches keep their single first observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from sports_edge_tennis.replay.contracts import (
    ModelPickClassification,
    ModelPickLedgerRow,
    ValueBetLedgerRow,
)

_DEFAULT_MODEL_PICK_CLASSIFICATIONS: tuple[ModelPickClassification, ...] = (
    "MANAGER_QUALIFIED",
)


@dataclass(frozen=True)
class ModelPickMetrics:
    count: int
    wins: int
    losses: int
    voids: int
    cancelled: int
    pending: int
    hit_rate: float | None


@dataclass(frozen=True)
class ValueBetMetrics:
    bets: int
    settled: int
    pending: int
    wins: int
    losses: int
    voids: int
    cancelled: int
    profit_units: Decimal
    roi: Decimal | None
    hit_rate: float | None


def _model_pick_sort_key(row: ModelPickLedgerRow):
    return (row.sporting_match_id, row.decision_as_of, row.run_sequence)


def _value_bet_sort_key(row: ValueBetLedgerRow):
    return (row.sporting_match_id, row.decision_as_of, row.run_sequence)


def first_qualified_model_picks(
    rows: Iterable[ModelPickLedgerRow],
    *,
    classifications: Sequence[ModelPickClassification] = _DEFAULT_MODEL_PICK_CLASSIFICATIONS,
) -> tuple[ModelPickLedgerRow, ...]:
    allowed = set(classifications)
    first: dict[int, ModelPickLedgerRow] = {}
    for row in sorted(rows, key=_model_pick_sort_key):
        if row.classification not in allowed:
            continue
        existing = first.get(row.sporting_match_id)
        if existing is None:
            first[row.sporting_match_id] = row
    return tuple(first[match_id] for match_id in sorted(first))


def first_qualified_value_bets(
    rows: Iterable[ValueBetLedgerRow],
) -> tuple[ValueBetLedgerRow, ...]:
    first: dict[int, ValueBetLedgerRow] = {}
    for row in sorted(rows, key=_value_bet_sort_key):
        existing = first.get(row.sporting_match_id)
        if existing is None:
            first[row.sporting_match_id] = row
    return tuple(first[match_id] for match_id in sorted(first))


def model_pick_metrics(
    rows: Iterable[ModelPickLedgerRow],
) -> ModelPickMetrics:
    wins = losses = voids = cancelled = pending = 0
    count = 0
    for row in rows:
        count += 1
        if row.actual_result == "WIN":
            wins += 1
        elif row.actual_result == "LOSS":
            losses += 1
        elif row.actual_result == "VOID":
            voids += 1
        elif row.actual_result == "CANCELLED":
            cancelled += 1
        else:
            pending += 1
    decided = wins + losses
    return ModelPickMetrics(
        count=count,
        wins=wins,
        losses=losses,
        voids=voids,
        cancelled=cancelled,
        pending=pending,
        hit_rate=(wins / decided) if decided else None,
    )


def value_bet_metrics(rows: Iterable[ValueBetLedgerRow]) -> ValueBetMetrics:
    bets = settled = pending = wins = losses = voids = cancelled = 0
    profit = Decimal("0")
    for row in rows:
        bets += 1
        if row.actual_result == "WIN":
            wins += 1
            settled += 1
        elif row.actual_result == "LOSS":
            losses += 1
            settled += 1
        elif row.actual_result == "VOID":
            voids += 1
            settled += 1
        elif row.actual_result == "CANCELLED":
            cancelled += 1
            settled += 1
        else:
            pending += 1
        if row.units is not None:
            profit += row.units
    decided = wins + losses
    return ValueBetMetrics(
        bets=bets,
        settled=settled,
        pending=pending,
        wins=wins,
        losses=losses,
        voids=voids,
        cancelled=cancelled,
        profit_units=profit,
        roi=(profit / Decimal(settled)) if settled else None,
        hit_rate=(wins / decided) if decided else None,
    )


__all__ = [
    "ModelPickMetrics",
    "ValueBetMetrics",
    "first_qualified_model_picks",
    "first_qualified_value_bets",
    "model_pick_metrics",
    "value_bet_metrics",
]
