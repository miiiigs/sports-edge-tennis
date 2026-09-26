"""Shared client-facing live-history accounting boundary.

This is not a replay and never rewrites historical decisions. It defines the
reporting window requested for the stabilized Sports Edge Tennis live record:
from 2026-09-10 onward, using the existing first-qualified-per-sporting-match
ledgers for Model Picks and Value Bets.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, TypeVar

ALIGNED_LIVE_HISTORY_START = date(2026, 9, 10)

T = TypeVar("T")


def since_aligned_start(rows: Iterable[T]) -> tuple[T, ...]:
    """Keep rows whose decision_date is inside the aligned live window."""
    return tuple(
        row
        for row in rows
        if getattr(row, "decision_date") >= ALIGNED_LIVE_HISTORY_START
    )


__all__ = ["ALIGNED_LIVE_HISTORY_START", "since_aligned_start"]
