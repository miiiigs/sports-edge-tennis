"""Canonical abstention vocabulary must match the code enums and the DB constraint."""

from __future__ import annotations

import re
from pathlib import Path

from sports_edge_tennis.agents.abstain_reasons import (
    AGENT_1_ABSTAIN_REASON_CODES,
    AGENT_2_ABSTAIN_REASON_CODES,
    AGENT_3_ABSTAIN_REASON_CODES,
    CANONICAL_ABSTAIN_REASON_CODES,
    canonical_reason_for_agent,
    is_canonical_abstain_reason,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    AbstainReason as Agent2AbstainReason,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    AbstainReason as Agent3AbstainReason,
)

REPO = Path(__file__).resolve().parents[1]
UP_0017 = REPO / "db" / "migrations" / "0017_prediction_abstention_reasons.up.sql"


def test_canonical_set_matches_agent_enums():
    assert AGENT_2_ABSTAIN_REASON_CODES == {r.value for r in Agent2AbstainReason}
    assert AGENT_3_ABSTAIN_REASON_CODES == {r.value for r in Agent3AbstainReason}
    assert AGENT_1_ABSTAIN_REASON_CODES == frozenset()
    assert CANONICAL_ABSTAIN_REASON_CODES == (
        AGENT_2_ABSTAIN_REASON_CODES | AGENT_3_ABSTAIN_REASON_CODES
    )


def test_membership_helpers():
    assert is_canonical_abstain_reason("WEAK_SIGNAL")
    assert is_canonical_abstain_reason("INSUFFICIENT_HISTORY")
    assert not is_canonical_abstain_reason(None)
    assert not is_canonical_abstain_reason("NOT_A_REAL_REASON")

    assert canonical_reason_for_agent("AGENT_2_HIERARCHICAL_MARKOV", "UNKNOWN_SURFACE")
    assert not canonical_reason_for_agent("AGENT_2_HIERARCHICAL_MARKOV", "WEAK_SIGNAL")
    assert canonical_reason_for_agent("AGENT_3_INTRANSITIVITY", "WEAK_SIGNAL")
    assert not canonical_reason_for_agent("AGENT_1_SURFACE_ELO", "WEAK_SIGNAL")
    assert not canonical_reason_for_agent("UNKNOWN_AGENT", "WEAK_SIGNAL")


def test_db_check_constraint_lists_exactly_the_canonical_codes():
    sql = UP_0017.read_text(encoding="utf-8")
    match = re.search(
        r"chk_predictions_abstain_reason_code.*?IN\s*\((.*?)\)\s*\)",
        sql,
        flags=re.DOTALL | re.IGNORECASE,
    )
    assert match, "could not find abstain reason check constraint"
    db_codes = set(re.findall(r"'([A-Z_]+)'", match.group(1)))
    assert db_codes == set(CANONICAL_ABSTAIN_REASON_CODES)
