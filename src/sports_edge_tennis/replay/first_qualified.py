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

CURRENT_MODEL_PICK_MIN_PROBABILITY = Decimal("0.600000")
_CURRENT_MODEL_PICK_CLASSIFICATIONS: tuple[ModelPickClassification, ...] = (
    "MANAGER_QUALIFIED",
    "A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED",
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


def current_rule_model_pick_probability(
    row: ModelPickLedgerRow,
) -> Decimal | None:
    """Probability used by the current Model Pick reporting rule.

    Normal Manager-qualified picks use the persisted Manager consensus
    probability. Wide-spread A1+A2 tracking picks use the arithmetic mean of
    the two agreeing primary-model probabilities. No probability is invented
    when the required evidence is absent.
    """
    if row.classification == "MANAGER_QUALIFIED":
        return row.consensus_probability
    if row.classification == "A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED":
        if (
            row.a1_state != "PREDICTED"
            or row.a2_state != "PREDICTED"
            or row.a3_state != "ABSTAINED"
            or row.a1_probability is None
            or row.a2_probability is None
            or row.a1_side not in (1, 2)
            or row.a2_side != row.a1_side
        ):
            return None
        return (row.a1_probability + row.a2_probability) / Decimal("2")
    return None


def first_current_rule_model_picks(
    rows: Iterable[ModelPickLedgerRow],
    *,
    min_probability: Decimal = CURRENT_MODEL_PICK_MIN_PROBABILITY,
) -> tuple[ModelPickLedgerRow, ...]:
    """First Model Pick per match under the current >=60% reporting rule.

    A match may qualify later if an earlier hourly evaluation was below the
    probability floor. This mirrors prospective current-system behavior rather
    than freezing the first raw Manager/wide-spread observation.
    """
    allowed = set(_CURRENT_MODEL_PICK_CLASSIFICATIONS)
    first: dict[int, ModelPickLedgerRow] = {}
    for row in sorted(rows, key=_model_pick_sort_key):
        if row.classification not in allowed:
            continue
        probability = current_rule_model_pick_probability(row)
        if probability is None or probability < min_probability:
            continue
        if row.sporting_match_id not in first:
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
    "CURRENT_MODEL_PICK_MIN_PROBABILITY",
    "ModelPickMetrics",
    "ValueBetMetrics",
    "current_rule_model_pick_probability",
    "first_current_rule_model_picks",
    "first_qualified_model_picks",
    "first_qualified_value_bets",
    "model_pick_metrics",
    "value_bet_metrics",
]
