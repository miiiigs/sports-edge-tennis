from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sports_edge_tennis.manager.contracts import ManagerPolicyV1
from sports_edge_tennis.market.contracts import (
    MarketPolicyV1,
    OddsSnapshotV1,
    SharpReferenceSpec,
)
from sports_edge_tennis.runner.contracts import ResolvedTargetV1
from sports_edge_tennis.veto.contracts import VetoContext


REPLAY_ID = "CURRENT_SYSTEM_REPLAY_20260910_V1"
REPLAY_SCHEMA_VERSION = "current_system_replay_v1"

#: Marker used whenever a required historical fact cannot be proven visible at
#: the replay decision cutoff. Missing facts are marked, never inferred.
DATA_NOT_AVAILABLE_PIT = "DATA_NOT_AVAILABLE_PIT"

#: Provisional marker. Numerical output may not be labelled official while the
#: concurrent historical recovery lane is still modifying deterministic coverage
#: for the same population.
PROVISIONAL_HISTORICAL_RECOVERY_NOT_FROZEN = (
    "PROVISIONAL - HISTORICAL RECOVERY SNAPSHOT NOT FROZEN"
)


PitStatus = Literal["PIT_COMPLETE", "DATA_NOT_AVAILABLE_PIT"]

#: Model-pick taxonomy. Category 2 is intentionally schema-supported now but is
#: NOT a Value Bet and is NOT produced by a Manager behaviour change.
ModelPickClassification = Literal[
    "MANAGER_QUALIFIED",
    "A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED",
    "NOT_MANAGER_QUALIFIED",
]

#: Where an opportunity disappears. This keeps replay analysis from collapsing
#: every "no bet" into one undifferentiated state.
OpportunityOutcome = Literal[
    "QUALIFIED_VALUE_BET",
    "INSUFFICIENT_EDGE",
    "VETO_REJECTION",
    "MARKET_REJECTION",
    "MANAGER_REJECTION",
    "AGENT_ABSTENTION",
    "DATA_NOT_AVAILABLE_PIT",
    "NOT_EVALUATED",
]

ExclusionReason = Literal[
    "TARGET_RESOLUTION_FAILED",
    "NO_SCHEDULED_PIT_VERSION",
    "DECISION_TIME_UNAVAILABLE_PIT",
    "IDENTITY_BLOCKED",
    "IDENTITY_NOT_AVAILABLE_PIT",
    "SURFACE_BLOCKED",
    "AGENT_HISTORY_UNAVAILABLE_PIT",
    "MISSING_MARKET_ODDS",
    "SHARP_REFERENCE_UNAVAILABLE_PIT",
    "VETO_EVIDENCE_UNAVAILABLE_PIT",
    "PIT_INCOMPLETE",
    "NO_ACTUAL_RESULT",
]

#: Scoring vocabulary. Deliberately distinct from settlement table statuses.
ReplayOutcome = Literal["PENDING", "WIN", "LOSS", "VOID", "CANCELLED"]
VetoStatus = Literal["NOT_APPLICABLE", "PASSED", "VETOED"]


@dataclass(frozen=True)
class ReplayDecisionTime:
    """The two PIT cutoffs used for one sporting match in one replay run."""

    prediction_as_of: datetime
    market_as_of: datetime
    source: str


@dataclass(frozen=True)
class ReplayActualResult:
    """Post-match truth used only for scoring, never as a feature."""

    match_id: int
    outcome_status: str
    winner_side: int | None
    result_observation_id: int | None
    result_visible_at: datetime


@dataclass(frozen=True)
class PitInputProof:
    """Audit evidence that a dataset is visible at a declared cutoff.

    The loader computes the maximum observed/as-of timestamps over exactly the
    rows it used. The engine re-checks the proof and fails closed if any fact was
    observed or valid after the cutoff.
    """

    kind: str
    cutoff: datetime
    max_source_observed_at: datetime | None
    max_as_of: datetime | None
    evidence_count: int
    snapshot_sha256: str
    pit_complete: bool = True


@dataclass(frozen=True)
class ReplayMatchInput:
    """Everything needed to evaluate one sporting match at one decision time."""

    target: ResolvedTargetV1
    competition_tier: str | None
    odds_snapshots: tuple[OddsSnapshotV1, ...] = ()
    odds_proof: PitInputProof | None = None
    veto_context: VetoContext | None = None
    actual_result: ReplayActualResult | None = None
    provider_event_ids: tuple[str, ...] = ()
    identity_status: str = "RESOLVED"
    surface_status: str = "RESOLVED"
    #: Historical closing/reference evidence, only when genuinely captured at
    #: the source boundary. CLV is never fabricated from current prices.
    hist_closing_decimal_price: Decimal | None = None
    hist_closing_observed_at: datetime | None = None

    @property
    def sporting_match_id(self) -> int:
        return self.target.match_id


@dataclass(frozen=True)
class ReplayRunInput:
    """One coherent replayed evaluation pass (analogous to one hourly run).

    All matches share one blind prediction cutoff and one market cutoff, exactly
    as the live full-slate runner builds one agent replay per ``as_of``.
    """

    run_id: str
    run_sequence: int
    decision_time: ReplayDecisionTime
    manager_policy: ManagerPolicyV1
    market_policy: MarketPolicyV1
    sharp_reference: SharpReferenceSpec
    matches: tuple[ReplayMatchInput, ...]
    agent1_replay: object = None
    agent2_snapshot: object = None
    agent3_snapshot: object = None
    agent1_snapshot_sha256: str | None = None
    agent2_snapshot_sha256: str | None = None
    agent3_snapshot_sha256: str | None = None
    #: Optional PIT loader hook invoked only for an otherwise-qualified BET,
    #: because Veto context depends on the market-selected side and reference
    #: price. The pure engine never performs database reads itself.
    veto_context_factory: object = None


@dataclass(frozen=True)
class ModelPickLedgerRow:
    """P1A ledger row. No betting ROI / units are attached to model picks."""

    replay_id: str
    run_id: str
    run_sequence: int
    sporting_match_id: int
    provider_event_ids: tuple[str, ...]
    decision_as_of: datetime
    prediction_as_of: datetime
    player_a_id: int
    player_b_id: int
    a1_state: str
    a1_side: int | None
    a1_probability: Decimal | None
    a1_config: str
    a2_state: str
    a2_side: int | None
    a2_probability: Decimal | None
    a2_config: str
    a3_state: str
    a3_side: int | None
    a3_probability: Decimal | None
    a3_reason: str | None
    a3_config: str
    manager_version: str
    manager_outcome: str
    manager_reason: str | None
    selected_side: int | None
    consensus_probability: Decimal | None
    classification: ModelPickClassification
    pit_status: PitStatus
    identity_status: str
    surface_status: str
    actual_result: ReplayOutcome | None


@dataclass(frozen=True)
class ValueBetLedgerRow:
    """P1B ledger row. Strict subset of valid Manager-qualified Model Picks."""

    replay_id: str
    run_id: str
    run_sequence: int
    sporting_match_id: int
    provider_event_ids: tuple[str, ...]
    first_model_pick_decision_as_of: datetime | None
    decision_as_of: datetime
    prediction_as_of: datetime
    selected_side: int | None
    consensus_probability: Decimal | None
    bookmaker: str | None
    price_source_system: str | None
    market_observed_at: datetime | None
    offered_decimal_price: Decimal | None
    reference_source_system: str | None
    reference_bookmaker: str | None
    reference_odds: Decimal | None
    reference_observed_at: datetime | None
    edge_pct: Decimal | None
    min_acceptable_odds: Decimal | None
    market_status: str
    market_reason: str | None
    veto_status: VetoStatus
    veto_trigger: str | None
    stake_fraction: Decimal | None
    staking_rule_version: str | None
    pit_status: PitStatus
    actual_result: ReplayOutcome
    units: Decimal | None
    clv_pct: Decimal | None
    clv_status: str


@dataclass(frozen=True)
class ReplayExclusion:
    """A sporting match excluded from replay, with a fail-closed reason."""

    sporting_match_id: int
    reason: ExclusionReason
    detail: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class OpportunityRecord:
    """Terminal funnel outcome for one evaluated sporting match in one run."""

    sporting_match_id: int
    run_id: str
    outcome: OpportunityOutcome
    detail: str | None = None


@dataclass(frozen=True)
class ReplayRunResult:
    run_id: str
    run_sequence: int
    prediction_as_of: datetime
    market_as_of: datetime
    model_picks: tuple[ModelPickLedgerRow, ...]
    value_bets: tuple[ValueBetLedgerRow, ...]
    exclusions: tuple[ReplayExclusion, ...]
    opportunities: tuple[OpportunityRecord, ...]


@dataclass(frozen=True)
class ReplayResult:
    replay_id: str
    runs: tuple[ReplayRunResult, ...]
    model_picks: tuple[ModelPickLedgerRow, ...]
    value_bets: tuple[ValueBetLedgerRow, ...]
    exclusions: tuple[ReplayExclusion, ...]
    opportunities: tuple[OpportunityRecord, ...]
    provisional: bool = True
    provisional_label: str | None = None
    notes: tuple[str, ...] = ()


__all__ = [
    "REPLAY_ID",
    "REPLAY_SCHEMA_VERSION",
    "DATA_NOT_AVAILABLE_PIT",
    "PROVISIONAL_HISTORICAL_RECOVERY_NOT_FROZEN",
    "PitStatus",
    "ModelPickClassification",
    "OpportunityOutcome",
    "ExclusionReason",
    "ReplayOutcome",
    "VetoStatus",
    "ReplayDecisionTime",
    "ReplayActualResult",
    "PitInputProof",
    "ReplayMatchInput",
    "ReplayRunInput",
    "ModelPickLedgerRow",
    "ValueBetLedgerRow",
    "ReplayExclusion",
    "OpportunityRecord",
    "ReplayRunResult",
    "ReplayResult",
]
