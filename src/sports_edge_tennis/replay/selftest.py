"""Deterministic synthetic self-test for the replay harness.

This is NOT a production replay. It uses a fixed in-memory snapshot to exercise
the full replay pipeline (agents -> manager -> market -> veto -> staking ->
ledgers -> manifest -> artifact) without a database.

It is used to prove determinism/reproducibility and to validate the artifact
pipeline. Numbers produced here describe the synthetic fixture only and must
never be presented as CURRENT_SYSTEM_REPLAY_20260910_V1 performance.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sports_edge_tennis.agents.contracts import (
    AGENT1_RAW_PROBABILITY_V1,
    AGENT2_RAW_PROBABILITY_V1,
    AGENT3_RAW_PROBABILITY_V1,
    PredictiveAgentResultV1,
)
from sports_edge_tennis.manager.contracts import MANAGER_POLICY_KEY, ManagerPolicyV1
from sports_edge_tennis.market.contracts import (
    MarketPolicyV1,
    OddsSnapshotV1,
    SharpReferenceSpec,
)
from sports_edge_tennis.market.staking import STAKING_RULE_VERSION
from sports_edge_tennis.replay.artifacts import write_replay_artifact
from sports_edge_tennis.replay.contracts import (
    PROVISIONAL_HISTORICAL_RECOVERY_NOT_FROZEN,
    REPLAY_ID,
    REPLAY_SCHEMA_VERSION,
    ReplayActualResult,
    ReplayDecisionTime,
    ReplayMatchInput,
    ReplayRunInput,
)
from sports_edge_tennis.replay.engine import run_replay
from sports_edge_tennis.replay.manifest import (
    ManifestMetadata,
    ReplayVersionSet,
    build_manifest,
)
from sports_edge_tennis.runner.contracts import ResolvedTargetV1
from sports_edge_tennis.veto.contracts import VetoContext
from sports_edge_tennis.veto.service import VETO_POLICY_VERSION

UTC = timezone.utc
PREDICTION_1 = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)
MARKET_1 = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
PREDICTION_2 = datetime(2026, 9, 12, 7, 0, tzinfo=UTC)
MARKET_2 = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
MATCH_START = datetime(2026, 9, 12, 13, 0, tzinfo=UTC)
RESULT_VISIBLE = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"

MANAGER_POLICY = ManagerPolicyV1(
    manager_policy_version_id=1,
    policy_key=MANAGER_POLICY_KEY,
    policy_version="v1",
    three_agent_max_probability_spread=Decimal("0.08"),
    agent3_abstention_max_probability_spread=Decimal("0.06"),
    min_active_agent_probability=Decimal("0.5"),
    allow_agent3_abstention=True,
)
MARKET_POLICY = MarketPolicyV1(
    market_policy_version_id=1,
    policy_key="tennis_market_value",
    policy_version="v1",
    atp_main_min_edge_pct=Decimal("0.05"),
    challenger_min_edge_pct=Decimal("0.08"),
    long_price_threshold=Decimal("4.0"),
    long_price_min_edge_pct=Decimal("0.08"),
    default_devig_method="POWER",
    sharp_reference_policy_code="PINNACLE_SHARP",
    odds_max_staleness_minutes=15,
    type_b_enabled=False,
)
SHARP = SharpReferenceSpec(source_system="SHARP", bookmaker_code="PINNACLE")

_SYNTHETIC_PROFILES = {
    1: "consensus",
    2: "consensus",
    3: "wide_spread",
    4: "consensus",
    5: "consensus",
    7: "consensus",
    8: "consensus",
    9: "consensus",
    10: "consensus",
}


def _agent(code: str, side: int, probability: str, representation: str, as_of: datetime):
    return PredictiveAgentResultV1(
        agent_code=code,
        prediction_status="PREDICTED",
        selected_side=side,
        selected_probability=Decimal(probability),
        probability_representation=representation,
        abstain_reason=None,
        model_version="selftest",
        feature_version="selftest",
        config_version="selftest",
        parameter_schema_version="selftest",
        as_of=as_of,
    )


def _abstained(code: str, as_of: datetime):
    return PredictiveAgentResultV1(
        agent_code=code,
        prediction_status="ABSTAINED",
        selected_side=None,
        selected_probability=None,
        probability_representation=None,
        abstain_reason="SELFTEST_ABSTAIN",
        model_version="selftest",
        feature_version="selftest",
        config_version="selftest",
        parameter_schema_version="selftest",
        as_of=as_of,
    )


def _agent_runner(run: ReplayRunInput, match: ReplayMatchInput):
    profile = _SYNTHETIC_PROFILES[match.target.match_id]
    as_of = run.decision_time.prediction_as_of
    if profile == "wide_spread":
        return {
            A1: _agent(A1, 1, "0.60", AGENT1_RAW_PROBABILITY_V1, as_of),
            A2: _agent(A2, 1, "0.50", AGENT2_RAW_PROBABILITY_V1, as_of),
            A3: _abstained(A3, as_of),
        }
    return {
        A1: _agent(A1, 1, "0.65", AGENT1_RAW_PROBABILITY_V1, as_of),
        A2: _agent(A2, 1, "0.66", AGENT2_RAW_PROBABILITY_V1, as_of),
        A3: _agent(A3, 1, "0.64", AGENT3_RAW_PROBABILITY_V1, as_of),
    }


def _target(match_id: int) -> ResolvedTargetV1:
    return ResolvedTargetV1(
        match_id=match_id,
        match_version_id=match_id,
        player_1_id=1000 + match_id,
        player_2_id=2000 + match_id,
        surface="HARD",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        round_code="R32",
        best_of_sets=3,
        qualifying_flag=False,
        match_status="SCHEDULED",
        canonical_tournament_name="Synthetic Replay Open",
        normalized_tournament_alias="synthetic_replay_open",
        season_year=2026,
    )


def _odds(match_id: int, observed_at: datetime, offered_price: str):
    return (
        OddsSnapshotV1(1, match_id, "SHARP", "PINNACLE", "MATCH_WINNER", "PLAYER_1",
                       Decimal("2.10"), "OPEN", None, f"sharp-{match_id}", observed_at, observed_at),
        OddsSnapshotV1(2, match_id, "SHARP", "PINNACLE", "MATCH_WINNER", "PLAYER_2",
                       Decimal("1.80"), "OPEN", None, f"sharp-{match_id}", observed_at, observed_at),
        OddsSnapshotV1(3, match_id, "OFFERED", "B365", "MATCH_WINNER", "PLAYER_1",
                       Decimal(offered_price), "OPEN", None, f"offered-{match_id}", observed_at, observed_at),
    )


def _veto(*, stale: bool = False) -> VetoContext:
    observed = MARKET_1 - timedelta(hours=7) if stale else MARKET_1 - timedelta(hours=1)
    return VetoContext(
        match_id=0,
        prediction_run_id=0,
        selected_side=1,
        decision_as_of=MARKET_1,
        target_start_at=MATCH_START,
        current_match_status="SCHEDULED",
        fixture_source_observed_at=observed,
        previous_matches=(),
        sharp_move_against_pct=Decimal("0.0"),
    )


def _result(match_id: int, outcome_status: str, winner_side: int | None) -> ReplayActualResult:
    return ReplayActualResult(
        match_id=match_id,
        outcome_status=outcome_status,
        winner_side=winner_side,
        result_observation_id=1000 + match_id,
        result_visible_at=RESULT_VISIBLE,
    )


def _match(
    match_id: int,
    *,
    observed_at: datetime,
    offered_price: str | None,
    outcome_status: str = "COMPLETED",
    winner_side: int | None = 1,
    identity_status: str = "RESOLVED",
    surface_status: str = "RESOLVED",
    veto: VetoContext | None = None,
) -> ReplayMatchInput:
    return ReplayMatchInput(
        target=_target(match_id),
        competition_tier="ATP_250",
        odds_snapshots=_odds(match_id, observed_at, offered_price) if offered_price else (),
        veto_context=veto if veto is not None else _veto(),
        actual_result=_result(match_id, outcome_status, winner_side),
        identity_status=identity_status,
        surface_status=surface_status,
    )


def build_selftest_runs() -> tuple[ReplayRunInput, ...]:
    run1 = ReplayRunInput(
        run_id="selftest-run-1",
        run_sequence=1,
        decision_time=ReplayDecisionTime(PREDICTION_1, MARKET_1, "SELFTEST"),
        manager_policy=MANAGER_POLICY,
        market_policy=MARKET_POLICY,
        sharp_reference=SHARP,
        matches=(
            _match(1, observed_at=MARKET_1, offered_price="2.30"),
            _match(2, observed_at=MARKET_1, offered_price=None),
            _match(3, observed_at=MARKET_1, offered_price=None),
            _match(4, observed_at=MARKET_1, offered_price="2.30", veto=_veto(stale=True)),
            _match(5, observed_at=MARKET_1, offered_price="1.85"),
            _match(7, observed_at=MARKET_1, offered_price=None),
            _match(9, observed_at=MARKET_1, offered_price="2.30", identity_status="LIVE_UNRESOLVED"),
            _match(10, observed_at=MARKET_1, offered_price="2.30", surface_status="AMBIGUOUS_HISTORY"),
        ),
    )
    run2 = ReplayRunInput(
        run_id="selftest-run-2",
        run_sequence=2,
        decision_time=ReplayDecisionTime(PREDICTION_2, MARKET_2, "SELFTEST"),
        manager_policy=MANAGER_POLICY,
        market_policy=MARKET_POLICY,
        sharp_reference=SHARP,
        matches=(
            _match(1, observed_at=MARKET_2, offered_price="2.20"),
            _match(7, observed_at=MARKET_2, offered_price="2.10", winner_side=2),
            _match(8, observed_at=MARKET_2, offered_price="2.05", outcome_status="RETIRED", winner_side=None),
        ),
    )
    return (run1, run2)


def build_selftest_result():
    return run_replay(
        build_selftest_runs(),
        replay_id=REPLAY_ID,
        provisional=True,
        provisional_label=PROVISIONAL_HISTORICAL_RECOVERY_NOT_FROZEN,
        agent_runner=_agent_runner,
    )


def build_selftest_manifest(result, *, generated_at: datetime | None = None):
    return build_manifest(
        result,
        metadata=ManifestMetadata(
            git_sha="selftest",
            generated_at=generated_at or datetime.now(UTC),
            historical_range_start=datetime(2026, 9, 12, tzinfo=UTC).date(),
            historical_range_end=datetime(2026, 9, 12, tzinfo=UTC).date(),
            input_snapshot_ids=("SELFTEST_SNAPSHOT_V1",),
        ),
        versions=ReplayVersionSet(
            a1_config_version="selftest",
            a2_config_version="selftest",
            a3_config_version="selftest",
            manager_policy_version="v1",
            market_policy_version="v1",
            veto_policy_version=VETO_POLICY_VERSION,
            staking_rule_version=STAKING_RULE_VERSION,
        ),
        replay_schema_version=REPLAY_SCHEMA_VERSION,
    )


def write_selftest_artifact(root: Path):
    result = build_selftest_result()
    manifest = build_selftest_manifest(result)
    return write_replay_artifact(root, result=result, manifest=manifest)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]) if argv else Path("artifacts/replay-selftest")
    paths = write_selftest_artifact(root)
    print(f"SELFTEST (synthetic, not production) artifact={paths.root}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
