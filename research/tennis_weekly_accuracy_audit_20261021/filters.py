"""Predeclared selective filters evaluated on a common eligible cohort.

The filters are not fitted to this week. Each one restates a failure pattern
already documented in the model error forensics research, so the forward
holdout is a stability check rather than a search over thresholds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .dataset import Pick

MARKET_DISAGREEMENT_THRESHOLD = 0.20
HIGH_CONFIDENCE_THRESHOLD = 0.80


@dataclass(frozen=True)
class SelectionFilter:
    name: str
    description: str
    rejects: Callable[[Pick], bool]

    def keeps(self, pick: Pick) -> bool:
        return not self.rejects(pick)


def market_disagreement(pick: Pick) -> bool:
    """Model is more than 20pp above the pre-pick sharp implied probability."""

    return (
        pick.model_minus_sharp is not None
        and pick.model_minus_sharp > MARKET_DISAGREEMENT_THRESHOLD
    )


def qualification_mismatch(pick: Pick) -> bool:
    return pick.qualification_mismatch


def high_confidence_without_market(pick: Pick) -> bool:
    return (
        pick.probability >= HIGH_CONFIDENCE_THRESHOLD and pick.model_minus_sharp is None
    )


def wide_spread(pick: Pick) -> bool:
    """Negative control: the A1/A2 spread gate did not predict failures."""

    return pick.probability_spread is not None and pick.probability_spread >= 0.05


def thin_surface_history(pick: Pick) -> bool:
    return pick.prior_surface_matches is not None and pick.prior_surface_matches < 2


PREDECLARED_FILTERS: tuple[SelectionFilter, ...] = (
    SelectionFilter(
        "R1_MARKET_DISAGREEMENT_20",
        "Reject picks more than 20pp above the pre-pick sharp implied probability.",
        market_disagreement,
    ),
    SelectionFilter(
        "R2_QUALIFICATION_MISMATCH",
        "Reject picks whose tournament name and qualifying flag disagree.",
        qualification_mismatch,
    ),
    SelectionFilter(
        "R3_HIGH_CONFIDENCE_NO_SHARP",
        "Reject picks at or above 80% with no pre-pick sharp snapshot.",
        high_confidence_without_market,
    ),
    SelectionFilter(
        "R4_COMBINED_PRIMARY",
        "Reject on R1, R2, or R3.",
        lambda pick: (
            market_disagreement(pick)
            or qualification_mismatch(pick)
            or high_confidence_without_market(pick)
        ),
    ),
    SelectionFilter(
        "C1_WIDE_SPREAD_CONTROL",
        "Control: reject A1/A2 spread at or above 0.05 (not supported by evidence).",
        wide_spread,
    ),
    SelectionFilter(
        "C2_THIN_SURFACE_CONTROL",
        "Control: reject fewer than two prior surface matches (not supported by evidence).",
        thin_surface_history,
    ),
)


def filter_by_name(name: str) -> SelectionFilter:
    for item in PREDECLARED_FILTERS:
        if item.name == name:
            return item
    raise KeyError(name)


__all__ = [
    "HIGH_CONFIDENCE_THRESHOLD",
    "MARKET_DISAGREEMENT_THRESHOLD",
    "PREDECLARED_FILTERS",
    "SelectionFilter",
    "filter_by_name",
    "high_confidence_without_market",
    "market_disagreement",
    "qualification_mismatch",
    "thin_surface_history",
    "wide_spread",
]
