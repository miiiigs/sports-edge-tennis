"""Scheduler-neutral settlement planning with explicit source/rule gates.

This module does not call a provider, write a settlement or infer a bookmaker
grade. It decides *whether* a settlement action is allowed, from persisted
evidence, so the same rules can be tested without a scheduler and reused by the
integration settlement adapter.

Separate pathways:

* ``SPORTING`` - official result only, canonical participant order required;
* ``BOOKMAKER`` - only from an explicitly approved bookmaker rule and an
  explicitly supplied market grade. Retirement, walkover and default are never
  converted into a bookmaker result here.

Guards: unapproved source, participant-order mismatch, result observed before
the fixture start, reschedule after the result, stale outcome, unsupported
status, duplicate revision, and correction with an explicit predecessor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Mapping

PATHWAY_SPORTING = "SPORTING"
PATHWAY_BOOKMAKER = "BOOKMAKER"

ACTION_NONE = "NONE"
ACTION_SPORTING = "SETTLE_SPORTING"
ACTION_BOOKMAKER = "SETTLE_BOOKMAKER"
ACTION_CORRECTION = "CORRECT_SETTLEMENT"
ACTION_BLOCKED = "BLOCKED"

REASON_NOT_DUE = "NOT_DUE"
REASON_SPORTING_READY = "SPORTING_READY"
REASON_BOOKMAKER_READY = "BOOKMAKER_READY"
REASON_ALREADY_SETTLED = "ALREADY_SETTLED_SAME_REVISION"
REASON_CORRECTION = "NEWER_RESULT_REVISION"
REASON_UNAPPROVED_SOURCE = "UNAPPROVED_SOURCE"
REASON_PARTICIPANT_ORDER = "PARTICIPANT_ORDER_MISMATCH"
REASON_RESULT_BEFORE_START = "RESULT_BEFORE_SCHEDULED_START"
REASON_RESCHEDULED_AFTER_RESULT = "RESCHEDULED_AFTER_RESULT"
REASON_STALE_RESULT = "STALE_RESULT"
REASON_UNSUPPORTED_STATUS = "UNSUPPORTED_RESULT_STATUS"
REASON_BOOKMAKER_RULE_UNAPPROVED = "BOOKMAKER_RULE_UNAPPROVED"
REASON_BOOKMAKER_GRADE_MISSING = "BOOKMAKER_GRADE_MISSING"
REASON_BOOKMAKER_INFERENCE_REFUSED = "BOOKMAKER_INFERENCE_REFUSED"

SPORTING_STATUSES = frozenset(
    {"COMPLETED", "RETIRED", "WALKOVER", "DEFAULTED", "CANCELLED", "UNRESOLVED"}
)
BOOKMAKER_GRADES = frozenset(
    {"WON", "LOST", "VOID", "CANCELLED", "UNRESOLVED", "NO_SELECTION"}
)


@dataclass(frozen=True)
class SettlementCandidate:
    decision_id: int
    match_id: int
    scheduled_start: datetime
    original_start: datetime
    revised_start: datetime | None
    actual_start: datetime | None
    result_status: str
    result_observed_at: datetime | None
    result_revision: int
    source_system: str
    source_dataset: str
    source_reference: str | None
    source_approved: bool
    source_participants: tuple[int, int] | None
    canonical_participants: tuple[int, int]
    winner_player_id: int | None
    selection_player_id: int | None
    current_settlement_revision: int | None = None
    current_sporting_grade: str | None = None
    bookmaker: str | None = None
    bookmaker_rule_version: str | None = None
    bookmaker_rule_reference: str | None = None
    bookmaker_grade: str | None = None
    approved_bookmaker_rules: Mapping[tuple[str, str], str] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        for name in ("scheduled_start", "original_start"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        for name in ("revised_start", "actual_start", "result_observed_at"):
            value = getattr(self, name)
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{name} must be timezone-aware")
        if self.result_status not in SPORTING_STATUSES:
            raise ValueError(f"unknown result status {self.result_status!r}")
        if self.result_revision < 1:
            raise ValueError("result_revision must be positive")
        if len(self.canonical_participants) != 2 or (
            self.canonical_participants[0] == self.canonical_participants[1]
        ):
            raise ValueError("canonical participants must be two distinct ids")
        if self.selection_player_id is not None and (
            self.selection_player_id not in self.canonical_participants
        ):
            raise ValueError("selection is outside the canonical fixture")


@dataclass(frozen=True)
class SettlementDecision:
    decision_id: int
    pathway: str
    action: str
    reason: str
    grade: str | None
    supersedes_revision: int | None
    detail: str
    settled_at: datetime | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "pathway": self.pathway,
            "action": self.action,
            "reason": self.reason,
            "grade": self.grade,
            "supersedes_revision": self.supersedes_revision,
            "detail": self.detail,
        }


def _start(candidate: SettlementCandidate) -> datetime:
    return candidate.actual_start or candidate.revised_start or candidate.scheduled_start


def _grade_for_result(candidate: SettlementCandidate) -> str | None:
    if candidate.result_status == "UNRESOLVED":
        return None
    if candidate.selection_player_id is None:
        return "NO_SELECTION"
    if candidate.result_status == "CANCELLED":
        return "CANCELLED"
    if candidate.result_status == "COMPLETED":
        if candidate.winner_player_id is None:
            return None
        return (
            "WON"
            if candidate.winner_player_id == candidate.selection_player_id
            else "LOST"
        )
    return "VOID"


def plan_sporting_settlement(
    candidate: SettlementCandidate,
    *,
    as_of: datetime,
    stale_after: timedelta = timedelta(hours=24),
) -> SettlementDecision:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if candidate.result_status == "UNRESOLVED" or candidate.result_observed_at is None:
        return SettlementDecision(
            candidate.decision_id,
            PATHWAY_SPORTING,
            ACTION_NONE,
            REASON_NOT_DUE,
            None,
            None,
            "no settled result visible",
        )
    if not candidate.source_approved or not candidate.source_reference:
        return _blocked(candidate, PATHWAY_SPORTING, REASON_UNAPPROVED_SOURCE)
    if candidate.source_participants is None or set(
        candidate.source_participants
    ) != set(candidate.canonical_participants):
        return _blocked(candidate, PATHWAY_SPORTING, REASON_PARTICIPANT_ORDER)
    if (
        candidate.result_status != "CANCELLED"
        and candidate.revised_start is not None
        and candidate.result_observed_at < candidate.revised_start
        and candidate.actual_start is None
    ):
        return _blocked(
            candidate, PATHWAY_SPORTING, REASON_RESCHEDULED_AFTER_RESULT
        )
    if (
        candidate.result_status != "CANCELLED"
        and candidate.result_observed_at < _start(candidate)
    ):
        return _blocked(candidate, PATHWAY_SPORTING, REASON_RESULT_BEFORE_START)
    if as_of - candidate.result_observed_at > stale_after:
        return _blocked(candidate, PATHWAY_SPORTING, REASON_STALE_RESULT)
    if candidate.result_status == "COMPLETED" and candidate.winner_player_id is None:
        return _blocked(candidate, PATHWAY_SPORTING, REASON_UNSUPPORTED_STATUS)
    if (
        candidate.winner_player_id is not None
        and candidate.winner_player_id not in candidate.canonical_participants
    ):
        return _blocked(candidate, PATHWAY_SPORTING, REASON_PARTICIPANT_ORDER)
    grade = _grade_for_result(candidate)
    if grade is None:
        return SettlementDecision(
            candidate.decision_id,
            PATHWAY_SPORTING,
            ACTION_NONE,
            REASON_UNSUPPORTED_STATUS,
            None,
            None,
            "result has no sporting grade",
        )
    if candidate.current_settlement_revision == candidate.result_revision:
        return SettlementDecision(
            candidate.decision_id,
            PATHWAY_SPORTING,
            ACTION_NONE,
            REASON_ALREADY_SETTLED,
            candidate.current_sporting_grade,
            None,
            "same result revision already settled",
        )
    if candidate.current_settlement_revision is not None:
        if candidate.result_revision <= candidate.current_settlement_revision:
            return _blocked(
                candidate, PATHWAY_SPORTING, REASON_UNSUPPORTED_STATUS,
                "revision does not advance the incumbent",
            )
        return SettlementDecision(
            candidate.decision_id,
            PATHWAY_SPORTING,
            ACTION_CORRECTION,
            REASON_CORRECTION,
            grade,
            candidate.current_settlement_revision,
            "newer official result revision",
        )
    return SettlementDecision(
        candidate.decision_id,
        PATHWAY_SPORTING,
        ACTION_SPORTING,
        REASON_SPORTING_READY,
        grade,
        None,
        "official result ready",
    )


def plan_bookmaker_settlement(
    candidate: SettlementCandidate,
    *,
    as_of: datetime,
    stale_after: timedelta = timedelta(hours=24),
) -> SettlementDecision:
    if candidate.bookmaker is None:
        return SettlementDecision(
            candidate.decision_id,
            PATHWAY_BOOKMAKER,
            ACTION_NONE,
            REASON_BOOKMAKER_GRADE_MISSING,
            None,
            None,
            "no bookmaker market",
        )
    rule_key = (candidate.bookmaker, candidate.bookmaker_rule_version or "")
    approved_reference = candidate.approved_bookmaker_rules.get(rule_key)
    if (
        not approved_reference
        or approved_reference != candidate.bookmaker_rule_reference
    ):
        return _blocked(
            candidate, PATHWAY_BOOKMAKER, REASON_BOOKMAKER_RULE_UNAPPROVED
        )
    if candidate.bookmaker_grade is None:
        return SettlementDecision(
            candidate.decision_id,
            PATHWAY_BOOKMAKER,
            ACTION_NONE,
            REASON_BOOKMAKER_GRADE_MISSING,
            None,
            None,
            "explicit bookmaker grade required; none inferred",
        )
    if candidate.bookmaker_grade not in BOOKMAKER_GRADES:
        return _blocked(
            candidate, PATHWAY_BOOKMAKER, REASON_BOOKMAKER_INFERENCE_REFUSED
        )
    if candidate.result_observed_at is None:
        return SettlementDecision(
            candidate.decision_id,
            PATHWAY_BOOKMAKER,
            ACTION_NONE,
            REASON_NOT_DUE,
            None,
            None,
            "no bookmaker settlement evidence yet",
        )
    if as_of - candidate.result_observed_at > stale_after:
        return _blocked(candidate, PATHWAY_BOOKMAKER, REASON_STALE_RESULT)
    if candidate.result_observed_at < _start(candidate):
        return _blocked(candidate, PATHWAY_BOOKMAKER, REASON_RESULT_BEFORE_START)
    if candidate.current_settlement_revision == candidate.result_revision:
        return SettlementDecision(
            candidate.decision_id,
            PATHWAY_BOOKMAKER,
            ACTION_NONE,
            REASON_ALREADY_SETTLED,
            candidate.bookmaker_grade,
            None,
            "same settlement revision already graded",
        )
    return SettlementDecision(
        candidate.decision_id,
        PATHWAY_BOOKMAKER,
        ACTION_BOOKMAKER,
        REASON_BOOKMAKER_READY,
        candidate.bookmaker_grade,
        candidate.current_settlement_revision,
        "approved bookmaker rule and explicit grade",
    )


def plan_settlements(
    candidates: list[SettlementCandidate],
    *,
    as_of: datetime,
    stale_after: timedelta = timedelta(hours=24),
) -> list[SettlementDecision]:
    decisions: list[SettlementDecision] = []
    for candidate in sorted(candidates, key=lambda item: item.decision_id):
        decisions.append(
            plan_sporting_settlement(
                candidate, as_of=as_of, stale_after=stale_after
            )
        )
        decisions.append(
            plan_bookmaker_settlement(
                candidate, as_of=as_of, stale_after=stale_after
            )
        )
    return decisions


def _blocked(
    candidate: SettlementCandidate,
    pathway: str,
    reason: str,
    detail: str | None = None,
) -> SettlementDecision:
    return SettlementDecision(
        candidate.decision_id,
        pathway,
        ACTION_BLOCKED,
        reason,
        None,
        None,
        detail or reason.lower().replace("_", " "),
    )
