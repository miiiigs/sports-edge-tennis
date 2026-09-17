"""Stable diagnostic vocabulary for Agent 2 API-Tennis stats coverage.

This module is intentionally data-only. It does not alter match-stat eligibility or
model behavior; it centralizes the bounded reason codes emitted by the daily history
refresh so coverage measurements remain comparable across runs.
"""

from __future__ import annotations


A2_STATS_SKIP_REASONS = frozenset(
    {
        "MISSING_PLAYER_KEYS",
        "NO_MATCH_PERIOD_STATS",
        "FIRST_PLAYER_INCOMPLETE",
        "SECOND_PLAYER_INCOMPLETE",
        "BOTH_PLAYERS_INCOMPLETE",
        "MISSING_INGESTED_FIXTURE",
        "UNKNOWN_PARSE_GAP",
    }
)


def normalize_a2_stats_skip_reason(reason: str | None) -> str:
    """Return one stable reason code without widening stats eligibility."""
    if reason in A2_STATS_SKIP_REASONS:
        return str(reason)
    return "UNKNOWN_PARSE_GAP"


__all__ = ["A2_STATS_SKIP_REASONS", "normalize_a2_stats_skip_reason"]
