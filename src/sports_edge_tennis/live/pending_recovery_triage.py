"""Operator-ready assignment of already-classified pending Model Picks.

No network calls, result settlement, database writes, fake winners or voids.
This is based solely on the canonical read-only pending diagnosis audit.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from sports_edge_tennis.live.pending_result_diagnostics import (
    FLAG_OUTSIDE_RECOVERY_WINDOW,
    REASON_AWAITING_RESULT,
    REASON_FIXTURE_NOT_REOBSERVED,
    REASON_FIXTURE_POSTPONED,
    REASON_NO_API_TENNIS_LINK,
    REASON_NOT_STARTED,
    REASON_RESULT_DELAYED,
    REASON_TERMINAL_FIXTURE_WITHOUT_RESULT,
)

ACTION_AWAIT_FIXTURE = "WAIT_NOT_STARTED_OR_POSTPONED"
ACTION_AWAIT_RESULT = "WAIT_FOR_AUTHORITATIVE_TERMINAL"
ACTION_WINNERLESS_WALKOVER = "BACKUP_GATED_WINNERLESS_WALKOVER"
ACTION_TERMINAL_EVIDENCE = "VERIFY_TERMINAL_WINNER_EVIDENCE"
ACTION_MISSING_API_ID = "REVIEW_CANONICAL_API_TENNIS_ID"
ACTION_HISTORICAL_PROVIDER = "VERIFY_HISTORICAL_PROVIDER_EVENT"
ACTION_RECENT_PROVIDER = "VERIFY_RECENT_PROVIDER_EVENT"

ACTIONABLE = frozenset({
    ACTION_WINNERLESS_WALKOVER,
    ACTION_TERMINAL_EVIDENCE,
    ACTION_MISSING_API_ID,
    ACTION_HISTORICAL_PROVIDER,
    ACTION_RECENT_PROVIDER,
})


def action_for(item) -> str:
    reason = item.reason
    if reason == REASON_TERMINAL_FIXTURE_WITHOUT_RESULT:
        # A terminal WALKOVER without a usable winner is NOT an automatic loss;
        # the existing protected migration 0021 is separately release gated.
        if (item.evidence.latest_match_status or "").upper() == "WALKOVER":
            return ACTION_WINNERLESS_WALKOVER
        return ACTION_TERMINAL_EVIDENCE
    if reason == REASON_NO_API_TENNIS_LINK:
        return ACTION_MISSING_API_ID
    if reason == REASON_FIXTURE_NOT_REOBSERVED:
        if FLAG_OUTSIDE_RECOVERY_WINDOW in item.flags:
            return ACTION_HISTORICAL_PROVIDER
        return ACTION_RECENT_PROVIDER
    if reason in {REASON_NOT_STARTED, REASON_FIXTURE_POSTPONED}:
        return ACTION_AWAIT_FIXTURE
    if reason in {REASON_AWAITING_RESULT, REASON_RESULT_DELAYED}:
        return ACTION_AWAIT_RESULT
    raise ValueError("unknown pending result reason")


def render_pending_recovery_triage(items: Iterable) -> tuple[str, ...]:
    items = tuple(items)
    details = [(item.match_id, action_for(item)) for item in items]
    counts = Counter(action for _, action in details)
    summary = ",".join(f"{k}:{v}" for k, v in sorted(counts.items())) or "-"
    logged = [
        "PENDING_RECOVERY_TRIAGE "
        f"total={len(items)} actionable={sum(action in ACTIONABLE for _,action in details)} "
        f"actions={summary} "
        "mode=READ_ONLY NO_INFERRED_RESULTS"
    ]
    for match_id, action in details:
        if action in ACTIONABLE:
            logged.append(
                f"PENDING_RECOVERY_ACTION match_id={match_id} next={action} "
                "auth=REQUIRED no_forced_winner=TRUE"
            )
    return tuple(logged)
