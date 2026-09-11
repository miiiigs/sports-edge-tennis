from sports_edge_tennis.market.contracts import (
    CANONICAL_ROLE_ORDER,
    MARKET_FEATURE_VERSION,
    MARKET_MODEL_VERSION,
    MARKET_POLICY_KEY,
    MARKET_POLICY_VERSION,
    MarketDecisionV1,
    MarketExecutionError,
    MarketIdempotencyMismatchError,
    MarketPolicyError,
    MarketPolicyV1,
    MarketReasonCode,
    MarketSlateError,
    MarketSlateOutcome,
    MarketValueError,
    OddsSnapshotV1,
    PersistedManagerConsensusRow,
    SharpReferenceSpec,
    SnapshotRole,
)
from sports_edge_tennis.market.evaluator import evaluate_market_match
from sports_edge_tennis.market.persistence import (
    ensure_market_policy_version_v1,
    load_market_policy_version,
)
from sports_edge_tennis.market.pricing import (
    booksum,
    edge_pct,
    minimum_acceptable_price,
    power_devig,
    snapshot_lineage_hash,
)
from sports_edge_tennis.market.policy import is_fresh, resolve_threshold


def evaluate_and_persist_market_slate(*args, **kwargs):
    """Lazy Veto-enabled entry point used by the live runner.

    The deferred import avoids a package initialization cycle because the Veto
    evidence service itself depends on the stable Market contracts.
    """
    from sports_edge_tennis.veto.integration import (
        evaluate_and_persist_market_slate_with_veto,
    )

    return evaluate_and_persist_market_slate_with_veto(*args, **kwargs)


__all__ = [
    "CANONICAL_ROLE_ORDER",
    "MARKET_FEATURE_VERSION",
    "MARKET_MODEL_VERSION",
    "MARKET_POLICY_KEY",
    "MARKET_POLICY_VERSION",
    "MarketDecisionV1",
    "MarketExecutionError",
    "MarketIdempotencyMismatchError",
    "MarketPolicyError",
    "MarketPolicyV1",
    "MarketReasonCode",
    "MarketSlateError",
    "MarketSlateOutcome",
    "MarketValueError",
    "OddsSnapshotV1",
    "PersistedManagerConsensusRow",
    "SharpReferenceSpec",
    "SnapshotRole",
    "booksum",
    "edge_pct",
    "ensure_market_policy_version_v1",
    "evaluate_and_persist_market_slate",
    "evaluate_market_match",
    "is_fresh",
    "load_market_policy_version",
    "minimum_acceptable_price",
    "power_devig",
    "resolve_threshold",
    "snapshot_lineage_hash",
]
