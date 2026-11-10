"""Read-only qualified >=70% parallel display for original Model Picks.

This mirrors the frozen PIT_CONFIDENCE_GE70_V1 research gate over *existing*
first-issued original-aligned dashboard cards. It does not issue, publish,
re-grade or remove any prediction, or modify Manager/Market/Veto logic.
The dashboard's canonical original result totals remain the acceptance source.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sports_edge_tennis.dashboard.picks import PickCard

POLICY_ID = "PIT_CONFIDENCE_GE70_V1"
CONFIDENCE_FLOOR = Decimal("0.70")


@dataclass(frozen=True)
class QualifiedParallelSummary:
    original: int
    retained: int
    won: int
    lost: int
    void: int
    pending: int
    match_ids: tuple[int, ...]

    @property
    def graded(self) -> int:
        return self.won + self.lost

    @property
    def win_rate(self) -> float | None:
        return self.won / self.graded if self.graded else None

    @property
    def settled(self) -> bool:
        return self.pending == 0

    @property
    def coverage(self) -> float | None:
        return self.retained / self.original if self.original else None

    def as_dict(self) -> dict[str, object]:
        return {
            "policy": POLICY_ID,
            "research_only": True,
            "live_selection_changed": False,
            "original": self.original,
            "retained": self.retained,
            "won": self.won,
            "lost": self.lost,
            "void": self.void,
            "pending": self.pending,
            "graded": self.graded,
            "win_rate": self.win_rate,
            "settled": self.settled,
            "coverage": self.coverage,
        }


def _original_probability(pick: PickCard) -> Decimal | None:
    """Manager first-issued confidence, or original A1/A2 tracking average."""
    raw = (
        pick.primary_average_probability
        if pick.is_wide_spread
        else pick.win_probability
    )
    if raw is None:
        return None
    try:
        value = Decimal(raw)
    except (TypeError, InvalidOperation, ValueError):
        return None
    return value if value.is_finite() and Decimal("0") <= value <= Decimal("1") else None


def build_qualified_parallel(
    picks: Iterable[PickCard],
) -> QualifiedParallelSummary:
    """Compute shadow counts from already-issued original picks, no new picks.

    Unknown/unsupported result states stay PENDING; never infer a winner.
    Duplicate match cards are ignored for the shadow to avoid double-counting.
    Missing/invalid original confidence fails closed and never qualifies.
    """
    originals = tuple(picks)
    included: list[PickCard] = []
    seen: set[int] = set()
    for pick in originals:
        if pick.match_id in seen:
            continue
        seen.add(pick.match_id)
        probability = _original_probability(pick)
        if probability is not None and probability >= CONFIDENCE_FLOOR:
            included.append(pick)

    return QualifiedParallelSummary(
        original=len(originals),
        retained=len(included),
        won=sum(p.result_status == "WON" for p in included),
        lost=sum(p.result_status == "LOST" for p in included),
        void=sum(p.result_status == "VOID" for p in included),
        pending=sum(p.result_status not in {"WON", "LOST", "VOID"} for p in included),
        match_ids=tuple(p.match_id for p in included),
    )
