"""Canonical, stable abstention-reason vocabulary for predictive agents.

The predictive-agent contract has always computed an abstention reason, but the
reason used to be lost before persistence. Migration
``0017_prediction_abstention_reasons`` adds ``predictions.abstain_reason_code``
and this module is the single source of the canonical string set that both the
application write path and the database check constraint use.

Only ``ABSTAINED`` rows may carry a reason. ``PREDICTED`` rows must store NULL.
Historical abstained rows may carry NULL because the reason was not captured
before 0017; no retroactive backfill is performed.
"""

from __future__ import annotations

from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    AbstainReason as Agent2AbstainReason,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    AbstainReason as Agent3AbstainReason,
)

#: Canonical reason codes for Agent 2 (Hierarchical Markov).
AGENT_2_ABSTAIN_REASON_CODES = frozenset(reason.value for reason in Agent2AbstainReason)

#: Canonical reason codes for Agent 3 (Intransitivity).
AGENT_3_ABSTAIN_REASON_CODES = frozenset(reason.value for reason in Agent3AbstainReason)

#: Agent 1 always predicts; it has no abstention vocabulary.
AGENT_1_ABSTAIN_REASON_CODES: frozenset[str] = frozenset()

#: Canonical reason codes keyed by agent code.
AGENT_ABSTAIN_REASON_CODES: dict[str, frozenset[str]] = {
    "AGENT_1_SURFACE_ELO": AGENT_1_ABSTAIN_REASON_CODES,
    "AGENT_2_HIERARCHICAL_MARKOV": AGENT_2_ABSTAIN_REASON_CODES,
    "AGENT_3_INTRANSITIVITY": AGENT_3_ABSTAIN_REASON_CODES,
}

#: Union of every canonical reason code. Mirrors the check constraint in
#: ``db/migrations/0017_prediction_abstention_reasons.up.sql``.
CANONICAL_ABSTAIN_REASON_CODES = (
    AGENT_2_ABSTAIN_REASON_CODES | AGENT_3_ABSTAIN_REASON_CODES
)


def is_canonical_abstain_reason(reason_code: str | None) -> bool:
    """True when ``reason_code`` is a member of the canonical vocabulary."""
    return reason_code is not None and reason_code in CANONICAL_ABSTAIN_REASON_CODES


def canonical_reason_for_agent(agent_code: str, reason_code: str | None) -> bool:
    """True when ``reason_code`` is valid for the given canonical agent."""
    allowed = AGENT_ABSTAIN_REASON_CODES.get(agent_code)
    if allowed is None:
        return False
    return reason_code is not None and reason_code in allowed


__all__ = [
    "AGENT_1_ABSTAIN_REASON_CODES",
    "AGENT_2_ABSTAIN_REASON_CODES",
    "AGENT_3_ABSTAIN_REASON_CODES",
    "AGENT_ABSTAIN_REASON_CODES",
    "CANONICAL_ABSTAIN_REASON_CODES",
    "canonical_reason_for_agent",
    "is_canonical_abstain_reason",
]
