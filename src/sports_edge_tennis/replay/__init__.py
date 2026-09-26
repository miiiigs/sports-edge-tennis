"""Point-in-time current-system replay harness.

This package answers one question safely:

    What would the CURRENT Sports Edge Tennis system have produced from a
    historical date onward if it had been operating then using only information
    legitimately available at each historical decision time?

It is deliberately separate from live production truth:

- replay never writes production decision tables;
- replay results are immutable, versioned artifacts plus a manifest;
- replay results are never blended with ORIGINAL LIVE production results.

The engine reuses the exact current A1/A2/A3 predictive agents, Manager v1,
Market v1, Veto v1, and quarter-Kelly staking functions. Point-in-time
correctness is enforced by the read-only loader and by an engine-level guard
that fails closed when evidence is not provably visible at the decision cutoff.
"""

from sports_edge_tennis.replay.contracts import (
    DATA_NOT_AVAILABLE_PIT,
    REPLAY_ID,
    REPLAY_SCHEMA_VERSION,
    ModelPickLedgerRow,
    ReplayActualResult,
    ReplayDecisionTime,
    ReplayExclusion,
    ReplayMatchInput,
    ReplayResult,
    ReplayRunInput,
    ReplayRunResult,
    ValueBetLedgerRow,
)
from sports_edge_tennis.replay.engine import run_replay
from sports_edge_tennis.replay.first_qualified import (
    first_current_rule_model_picks,
    first_qualified_model_picks,
)

__all__ = [
    "DATA_NOT_AVAILABLE_PIT",
    "REPLAY_ID",
    "REPLAY_SCHEMA_VERSION",
    "ModelPickLedgerRow",
    "ReplayActualResult",
    "ReplayDecisionTime",
    "ReplayExclusion",
    "ReplayMatchInput",
    "ReplayResult",
    "ReplayRunInput",
    "ReplayRunResult",
    "ValueBetLedgerRow",
    "run_replay",
    "first_current_rule_model_picks",
    "first_qualified_model_picks",
]
