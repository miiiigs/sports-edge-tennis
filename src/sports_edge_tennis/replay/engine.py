"""Deterministic point-in-time replay engine.

The engine is a pure computation over already-PIT-loaded inputs. It performs no
database reads or writes. It reuses the *current* production logic directly:

- Agent 1 surface Elo: ``predict_from_replay`` with the current default config
- Agent 2 hierarchical Markov: ``predict`` with the current default config
- Agent 3 intransitivity: ``predict`` with the current default config
- Manager v1: ``manager.consensus.evaluate_consensus``
- Market v1: ``market.evaluator.evaluate_market_match``
- Veto v1: ``veto.service.evaluate_veto_context`` + kill-only decision rewrite
- Staking: quarter-Kelly (already invoked inside the Market evaluator)

Odds never enter A1/A2/A3. The result is used only for post-prediction scoring.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Callable, Mapping, Sequence

from sports_edge_tennis.agents.adapters import (
    adapt_agent1_result,
    adapt_agent2_result,
    adapt_agent3_result,
)
from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as AGENT1_DEFAULT_CONFIG,
    predict_from_replay as predict_agent1,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG as AGENT2_DEFAULT_CONFIG,
    predict as predict_agent2,
)
from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG as AGENT3_DEFAULT_CONFIG,
    predict as predict_agent3,
)
from sports_edge_tennis.agents.contracts import PredictiveAgentResultV1
from sports_edge_tennis.data.prediction_persistence import (
    PersistedPredictiveAgentPrediction,
)
from sports_edge_tennis.manager.consensus import evaluate_consensus
from sports_edge_tennis.manager.contracts import ManagerConsensusResultV1
from sports_edge_tennis.market.contracts import (
    MarketDecisionV1,
    PersistedManagerConsensusRow,
)
from sports_edge_tennis.market.evaluator import evaluate_market_match
from sports_edge_tennis.replay.classification import (
    classify_model_pick,
    manager_rejection_outcome,
)
from sports_edge_tennis.replay.contracts import (
    PROVISIONAL_HISTORICAL_RECOVERY_NOT_FROZEN,
    REPLAY_ID,
    ModelPickLedgerRow,
    OpportunityRecord,
    ReplayExclusion,
    ReplayMatchInput,
    ReplayOutcome,
    ReplayResult,
    ReplayRunInput,
    ReplayRunResult,
    ValueBetLedgerRow,
)
from sports_edge_tennis.replay.pit import (
    PitIncompleteError,
    PointInTimeViolation,
    assert_decision_ordering,
    assert_odds_visible_at,
    assert_result_not_visible_at_prediction,
    require_tz_aware,
)
from sports_edge_tennis.runner.inputs import (
    build_agent1_request,
    build_agent2_request,
    build_agent3_request,
)
from sports_edge_tennis.settlement.service import classify_match_winner_settlement
from sports_edge_tennis.veto.integration import _mark_decision_vetoed
from sports_edge_tennis.veto.service import evaluate_veto_context


_SCORE_TO_OUTCOME: Mapping[str, ReplayOutcome] = {
    "WON": "WIN",
    "LOST": "LOSS",
    "VOID": "VOID",
    "CANCELLED": "CANCELLED",
}


def _probability(adapted: PredictiveAgentResultV1) -> Decimal | None:
    if adapted.prediction_status != "PREDICTED":
        return None
    return adapted.selected_probability


def _adapt_agent_results(run: ReplayRunInput, match: ReplayMatchInput) -> dict[str, PredictiveAgentResultV1]:
    if run.agent1_replay is None or run.agent2_snapshot is None or run.agent3_snapshot is None:
        raise PitIncompleteError("agent history snapshot is not PIT-available for this run")

    target = match.target
    prediction_as_of = run.decision_time.prediction_as_of

    a1_result = predict_agent1(
        build_agent1_request(target, prediction_as_of),
        run.agent1_replay,
        config=AGENT1_DEFAULT_CONFIG,
    )
    a2_result = predict_agent2(
        build_agent2_request(target, run.agent2_snapshot, prediction_as_of),
        config=AGENT2_DEFAULT_CONFIG,
    )
    a3_result = predict_agent3(
        build_agent3_request(target, run.agent3_snapshot, prediction_as_of),
        config=AGENT3_DEFAULT_CONFIG,
    )

    adapted = {
        adapted_result.agent_code: adapted_result
        for adapted_result in (
            adapt_agent1_result(a1_result, config=AGENT1_DEFAULT_CONFIG),
            adapt_agent2_result(a2_result),
            adapt_agent3_result(a3_result),
        )
    }
    for result in adapted.values():
        if result.as_of != prediction_as_of:
            raise PointInTimeViolation(
                "agent result as_of does not match the run prediction cutoff"
            )
    return adapted


def _manager_input_row(
    adapted: PredictiveAgentResultV1,
    *,
    match_id: int,
    run_sequence: int,
) -> PersistedPredictiveAgentPrediction:
    return PersistedPredictiveAgentPrediction(
        prediction_id=0,
        prediction_run_id=run_sequence,
        match_id=match_id,
        agent_code=adapted.agent_code,
        prediction_status=adapted.prediction_status,
        selection_side=adapted.selected_side,
        win_probability=adapted.selected_probability,
        probability_representation=adapted.probability_representation,
        model_version=adapted.model_version,
        feature_version=adapted.feature_version,
        config_version=adapted.config_version,
        parameter_schema_version=adapted.parameter_schema_version,
        agent_config_version_id=0,
        as_of=adapted.as_of,
    )


def _manager_row_for_market(
    manager_result: ManagerConsensusResultV1,
    *,
    run_sequence: int,
) -> PersistedManagerConsensusRow:
    return PersistedManagerConsensusRow(
        manager_consensus_result_id=0,
        prediction_run_id=run_sequence,
        match_id=manager_result.match_id,
        manager_policy_version_id=manager_result.manager_policy_version_id,
        manager_status=manager_result.manager_status,
        selected_side=manager_result.selected_side,
        consensus_probability=manager_result.consensus_probability,
        active_agent_count=manager_result.active_agent_count,
        abstaining_agent_count=manager_result.abstaining_agent_count,
        probability_spread=manager_result.probability_spread,
        reason_code=manager_result.reason_code,
        as_of=manager_result.as_of,
    )


def _score_outcome(
    *,
    selected_side: int | None,
    actual_result,
) -> ReplayOutcome:
    if actual_result is None:
        return "PENDING"
    if selected_side not in (1, 2):
        return "PENDING"
    settlement_status = classify_match_winner_settlement(
        outcome_status=actual_result.outcome_status,
        winner_side=actual_result.winner_side,
        selection_side=selected_side,
    )
    return _SCORE_TO_OUTCOME[settlement_status]


def _units(outcome: ReplayOutcome, *, price: Decimal | None) -> Decimal | None:
    """One-unit flat ledger convention, identical to the production ledger.

    WIN -> price - 1, LOSS -> -1, VOID/CANCELLED -> 0, PENDING -> None.
    """

    if outcome == "PENDING":
        return None
    if outcome == "WIN":
        if price is None:
            return None
        return price - Decimal("1")
    if outcome == "LOSS":
        return Decimal("-1")
    return Decimal("0")


def _clv(
    match: ReplayMatchInput,
    *,
    decision_as_of: datetime,
    price_taken: Decimal | None,
) -> tuple[Decimal | None, str]:
    """Return PIT-valid CLV only when genuinely captured historical evidence exists."""

    closing_price = match.hist_closing_decimal_price
    closing_observed_at = match.hist_closing_observed_at
    if closing_price is None or closing_observed_at is None or price_taken is None:
        return None, "NOT_AVAILABLE_PIT"
    require_tz_aware(closing_observed_at, field_name="hist_closing_observed_at")
    if closing_observed_at < decision_as_of:
        raise PointInTimeViolation("historical closing evidence predates the decision")
    if closing_price <= 1:
        raise PointInTimeViolation("historical closing evidence is not a valid price")
    return (price_taken / closing_price) - Decimal("1"), "AVAILABLE"


AgentRunner = Callable[
    [ReplayRunInput, ReplayMatchInput], Mapping[str, PredictiveAgentResultV1]
]


def _evaluate_match(
    run: ReplayRunInput,
    match: ReplayMatchInput,
    agent_runner: AgentRunner,
) -> tuple[
    ModelPickLedgerRow | None,
    ValueBetLedgerRow | None,
    tuple[ReplayExclusion, ...],
    OpportunityRecord | None,
]:
    target = match.target
    decision_time = run.decision_time
    prediction_as_of = decision_time.prediction_as_of
    market_as_of = decision_time.market_as_of

    if match.identity_status == "LIVE_UNRESOLVED":
        return (
            None,
            None,
            (
                ReplayExclusion(
                    sporting_match_id=target.match_id,
                    reason="IDENTITY_NOT_AVAILABLE_PIT",
                    detail="identity evidence was not provably available at the replay cutoff",
                    run_id=run.run_id,
                ),
            ),
            OpportunityRecord(
                sporting_match_id=target.match_id,
                run_id=run.run_id,
                outcome="DATA_NOT_AVAILABLE_PIT",
                detail="identity",
            ),
        )

    if match.surface_status not in ("RESOLVED", "SURFACE_KNOWN_AT_T"):
        reason = (
            "SURFACE_CONFLICT_AT_T"
            if match.surface_status == "SURFACE_CONFLICT_AT_T"
            else "SURFACE_NOT_AVAILABLE_PIT"
        )
        return (
            None,
            None,
            (
                ReplayExclusion(
                    sporting_match_id=target.match_id,
                    reason=reason,
                    detail=f"surface not provably available at T ({match.surface_status})",
                    run_id=run.run_id,
                ),
            ),
            OpportunityRecord(
                sporting_match_id=target.match_id,
                run_id=run.run_id,
                outcome="DATA_NOT_AVAILABLE_PIT",
                detail="surface",
            ),
        )

    try:
        adapted = dict(agent_runner(run, match))
    except PitIncompleteError:
        return (
            None,
            None,
            (
                ReplayExclusion(
                    sporting_match_id=target.match_id,
                    reason="AGENT_HISTORY_UNAVAILABLE_PIT",
                    detail="agent history snapshot unavailable",
                    run_id=run.run_id,
                ),
            ),
            OpportunityRecord(
                sporting_match_id=target.match_id,
                run_id=run.run_id,
                outcome="DATA_NOT_AVAILABLE_PIT",
                detail="agent_history",
            ),
        )

    manager_result = evaluate_consensus(
        [_manager_input_row(a, match_id=target.match_id, run_sequence=run.run_sequence) for a in adapted.values()],
        match_id=target.match_id,
        prediction_run_id=run.run_sequence,
        policy=run.manager_policy,
        as_of=prediction_as_of,
    )

    classification = classify_model_pick(manager_result, adapted)

    # Model Picks are scoreable when a side can be attributed. Manager consensus
    # supplies the side; the wide-spread category uses the A1/A2 agreed side.
    if manager_result.selected_side is not None:
        model_selected_side = manager_result.selected_side
    else:
        a1 = adapted.get("AGENT_1_SURFACE_ELO")
        a2 = adapted.get("AGENT_2_HIERARCHICAL_MARKOV")
        if (
            a1 is not None
            and a2 is not None
            and a1.selected_side is not None
            and a1.selected_side == a2.selected_side
        ):
            model_selected_side = a1.selected_side
        else:
            model_selected_side = None

    assert_result_not_visible_at_prediction(
        result_visible_at=(
            match.actual_result.result_visible_at if match.actual_result is not None else None
        ),
        prediction_as_of=prediction_as_of,
    )
    model_actual = (
        _score_outcome(selected_side=model_selected_side, actual_result=match.actual_result)
        if model_selected_side is not None
        else None
    )

    a1 = adapted["AGENT_1_SURFACE_ELO"]
    a2 = adapted["AGENT_2_HIERARCHICAL_MARKOV"]
    a3 = adapted["AGENT_3_INTRANSITIVITY"]

    model_pick = ModelPickLedgerRow(
        replay_id=REPLAY_ID,
        run_id=run.run_id,
        run_sequence=run.run_sequence,
        sporting_match_id=target.match_id,
        provider_event_ids=match.provider_event_ids,
        decision_as_of=market_as_of,
        prediction_as_of=prediction_as_of,
        player_a_id=target.player_1_id,
        player_b_id=target.player_2_id,
        a1_state=a1.prediction_status,
        a1_side=a1.selected_side,
        a1_probability=_probability(a1),
        a1_config=a1.config_version,
        a2_state=a2.prediction_status,
        a2_side=a2.selected_side,
        a2_probability=_probability(a2),
        a2_config=a2.config_version,
        a3_state=a3.prediction_status,
        a3_side=a3.selected_side,
        a3_probability=_probability(a3),
        a3_reason=a3.abstain_reason,
        a3_config=a3.config_version,
        manager_version=run.manager_policy.policy_version,
        manager_outcome=manager_result.manager_status,
        manager_reason=manager_result.reason_code,
        selected_side=model_selected_side,
        consensus_probability=manager_result.consensus_probability,
        classification=classification,
        pit_status="PIT_COMPLETE",
        identity_status=match.identity_status,
        surface_status=match.surface_status,
        actual_result=model_actual,
        a3_diagnostics=dict(a3.diagnostics_payload) if a3.diagnostics_payload else None,
    )

    if manager_result.manager_status != "CONSENSUS":
        return (
            model_pick,
            None,
            (),
            OpportunityRecord(
                sporting_match_id=target.match_id,
                run_id=run.run_id,
                outcome=manager_rejection_outcome(manager_result),
                detail=manager_result.reason_code,
            ),
        )

    assert_decision_ordering(prediction_as_of=prediction_as_of, market_as_of=market_as_of)
    assert_odds_visible_at(match.odds_snapshots, cutoff=market_as_of)

    if not match.odds_snapshots:
        return (
            model_pick,
            None,
            (
                ReplayExclusion(
                    sporting_match_id=target.match_id,
                    reason="MISSING_MARKET_ODDS",
                    detail="no historical odds captured for this match",
                    run_id=run.run_id,
                ),
            ),
            OpportunityRecord(
                sporting_match_id=target.match_id,
                run_id=run.run_id,
                outcome="DATA_NOT_AVAILABLE_PIT",
                detail="MISSING_MARKET_ODDS",
            ),
        )

    sharp_captured = any(
        snapshot.source_system == run.sharp_reference.source_system
        and snapshot.bookmaker_code == run.sharp_reference.bookmaker_code
        for snapshot in match.odds_snapshots
    )
    if not sharp_captured:
        return (
            model_pick,
            None,
            (
                ReplayExclusion(
                    sporting_match_id=target.match_id,
                    reason="SHARP_REFERENCE_UNAVAILABLE_PIT",
                    detail="no historical sharp reference was captured",
                    run_id=run.run_id,
                ),
            ),
            OpportunityRecord(
                sporting_match_id=target.match_id,
                run_id=run.run_id,
                outcome="DATA_NOT_AVAILABLE_PIT",
                detail="SHARP_REFERENCE_UNAVAILABLE_PIT",
            ),
        )

    market_decision = evaluate_market_match(
        manager_row=_manager_row_for_market(manager_result, run_sequence=run.run_sequence),
        policy=run.market_policy,
        competition_tier=match.competition_tier,
        sharp_reference=run.sharp_reference,
        odds_snapshots=list(match.odds_snapshots),
        decision_as_of=market_as_of,
    )

    if market_decision.decision_status != "BET":
        outcome = (
            "INSUFFICIENT_EDGE"
            if market_decision.reason_code == "EDGE_BELOW_THRESHOLD"
            else "MARKET_REJECTION"
        )
        return (
            model_pick,
            None,
            (),
            OpportunityRecord(
                sporting_match_id=target.match_id,
                run_id=run.run_id,
                outcome=outcome,
                detail=market_decision.reason_code,
            ),
        )

    veto_context = match.veto_context
    if veto_context is None and run.veto_context_factory is not None:
        veto_context = run.veto_context_factory(match, market_decision)

    if veto_context is None:
        return (
            model_pick,
            None,
            (
                ReplayExclusion(
                    sporting_match_id=target.match_id,
                    reason="VETO_EVIDENCE_UNAVAILABLE_PIT",
                    detail="veto evidence unavailable; failing closed",
                    run_id=run.run_id,
                ),
            ),
            OpportunityRecord(
                sporting_match_id=target.match_id,
                run_id=run.run_id,
                outcome="DATA_NOT_AVAILABLE_PIT",
                detail="VETO_EVIDENCE_UNAVAILABLE_PIT",
            ),
        )

    veto_evaluation = evaluate_veto_context(veto_context)
    final_decision = (
        _mark_decision_vetoed(market_decision) if veto_evaluation.vetoed else market_decision
    )
    veto_status = "VETOED" if veto_evaluation.vetoed else "PASSED"
    veto_trigger = veto_evaluation.primary_trigger

    if veto_evaluation.vetoed:
        return (
            model_pick,
            None,
            (),
            OpportunityRecord(
                sporting_match_id=target.match_id,
                run_id=run.run_id,
                outcome="VETO_REJECTION",
                detail=veto_trigger,
            ),
        )

    reference_snapshot = _reference_snapshot(final_decision, match.odds_snapshots)
    offered_observed_at = _offered_observed_at(final_decision, match.odds_snapshots)
    clv_pct, clv_status = _clv(
        match,
        decision_as_of=market_as_of,
        price_taken=final_decision.market_decimal_price,
    )
    score = _score_outcome(selected_side=final_decision.selected_side, actual_result=match.actual_result)

    value_bet = ValueBetLedgerRow(
        replay_id=REPLAY_ID,
        run_id=run.run_id,
        run_sequence=run.run_sequence,
        sporting_match_id=target.match_id,
        provider_event_ids=match.provider_event_ids,
        first_model_pick_decision_as_of=None,
        decision_as_of=market_as_of,
        prediction_as_of=prediction_as_of,
        selected_side=final_decision.selected_side,
        consensus_probability=final_decision.consensus_probability,
        bookmaker=final_decision.price_bookmaker_code,
        price_source_system=final_decision.price_source_system,
        market_observed_at=offered_observed_at,
        offered_decimal_price=final_decision.market_decimal_price,
        reference_source_system=(
            run.sharp_reference.source_system if reference_snapshot is not None else None
        ),
        reference_bookmaker=(
            run.sharp_reference.bookmaker_code if reference_snapshot is not None else None
        ),
        reference_odds=(
            reference_snapshot.decimal_price if reference_snapshot is not None else None
        ),
        reference_observed_at=(
            reference_snapshot.source_observed_at if reference_snapshot is not None else None
        ),
        edge_pct=final_decision.edge_pct,
        min_acceptable_odds=final_decision.min_acceptable_odds,
        market_status=final_decision.decision_status,
        market_reason=None,
        veto_status=veto_status,
        veto_trigger=veto_trigger,
        stake_fraction=final_decision.suggested_stake_fraction,
        staking_rule_version=final_decision.staking_rule_version,
        pit_status="PIT_COMPLETE",
        actual_result=score,
        units=_units(score, price=final_decision.market_decimal_price),
        clv_pct=clv_pct,
        clv_status=clv_status,
    )
    return (
        model_pick,
        value_bet,
        (),
        OpportunityRecord(
            sporting_match_id=target.match_id,
            run_id=run.run_id,
            outcome="QUALIFIED_VALUE_BET",
            detail=None,
        ),
    )


def _reference_snapshot(decision: MarketDecisionV1, odds_snapshots):
    if decision.selected_side is None:
        return None
    selection_type = "PLAYER_1" if decision.selected_side == 1 else "PLAYER_2"
    candidates = [
        snapshot
        for snapshot in odds_snapshots
        if snapshot.selection_type == selection_type
        and snapshot.source_observed_at is not None
    ]
    if not candidates:
        return None
    # The sharp reference used by the evaluator is the freshest complete open pair.
    return max(
        candidates,
        key=lambda snapshot: (snapshot.source_observed_at, -snapshot.odds_snapshot_id),
    )


def _offered_observed_at(decision: MarketDecisionV1, odds_snapshots):
    if decision.decision_status != "BET":
        return None
    matching = [
        snapshot
        for snapshot in odds_snapshots
        if snapshot.decimal_price == decision.market_decimal_price
        and snapshot.bookmaker_code == decision.price_bookmaker_code
        and snapshot.source_system == decision.price_source_system
    ]
    if not matching:
        return None
    return max(matching, key=lambda snapshot: snapshot.source_observed_at).source_observed_at


def run_replay_run(
    run: ReplayRunInput,
    *,
    agent_runner: AgentRunner | None = None,
) -> ReplayRunResult:
    runner = agent_runner or _adapt_agent_results
    model_picks: list[ModelPickLedgerRow] = []
    value_bets: list[ValueBetLedgerRow] = []
    exclusions: list[ReplayExclusion] = list(run.loader_exclusions)
    opportunities: list[OpportunityRecord] = [
        OpportunityRecord(
            sporting_match_id=exclusion.sporting_match_id,
            run_id=run.run_id,
            outcome="DATA_NOT_AVAILABLE_PIT",
            detail=exclusion.reason,
        )
        for exclusion in run.loader_exclusions
    ]

    for match in run.matches:
        model_pick, value_bet, match_exclusions, opportunity = _evaluate_match(run, match, runner)
        if model_pick is not None:
            model_picks.append(model_pick)
        if value_bet is not None:
            value_bets.append(value_bet)
        exclusions.extend(match_exclusions)
        if opportunity is not None:
            opportunities.append(opportunity)

    return ReplayRunResult(
        run_id=run.run_id,
        run_sequence=run.run_sequence,
        prediction_as_of=run.decision_time.prediction_as_of,
        market_as_of=run.decision_time.market_as_of,
        model_picks=tuple(model_picks),
        value_bets=tuple(value_bets),
        exclusions=tuple(exclusions),
        opportunities=tuple(opportunities),
    )


def _first_model_pick_times(
    runs: Sequence[ReplayRunResult],
) -> dict[int, datetime]:
    first: dict[int, tuple[int, datetime]] = {}
    for run in runs:
        for pick in run.model_picks:
            if pick.classification != "MANAGER_QUALIFIED":
                continue
            current = first.get(pick.sporting_match_id)
            candidate = (pick.run_sequence, pick.decision_as_of)
            if current is None or candidate[1] < current[1] or (
                candidate[1] == current[1] and candidate[0] < current[0]
            ):
                first[pick.sporting_match_id] = candidate
    return {match_id: value[1] for match_id, value in first.items()}


def run_replay(
    runs: Sequence[ReplayRunInput],
    *,
    replay_id: str = REPLAY_ID,
    provisional: bool = True,
    provisional_label: str | None = PROVISIONAL_HISTORICAL_RECOVERY_NOT_FROZEN,
    agent_runner: AgentRunner | None = None,
) -> ReplayResult:
    """Execute the replay across one or more coherent runs and aggregate ledgers."""

    ordered_runs = sorted(runs, key=lambda item: item.run_sequence)
    run_results = tuple(run_replay_run(run, agent_runner=agent_runner) for run in ordered_runs)

    first_pick_times = _first_model_pick_times(run_results)

    model_picks: list[ModelPickLedgerRow] = []
    value_bets: list[ValueBetLedgerRow] = []
    exclusions: list[ReplayExclusion] = []
    opportunities: list[OpportunityRecord] = []

    for result in run_results:
        model_picks.extend(
            sorted(result.model_picks, key=lambda row: (row.sporting_match_id, row.decision_as_of))
        )
        for value_bet in sorted(
            result.value_bets, key=lambda row: (row.sporting_match_id, row.decision_as_of)
        ):
            value_bets.append(
                replace(
                    value_bet,
                    replay_id=replay_id,
                    first_model_pick_decision_as_of=first_pick_times.get(
                        value_bet.sporting_match_id
                    ),
                )
            )
        exclusions.extend(result.exclusions)
        opportunities.extend(result.opportunities)

    normalized_picks = tuple(
        replace(pick, replay_id=replay_id) for pick in model_picks
    )

    return ReplayResult(
        replay_id=replay_id,
        runs=run_results,
        model_picks=normalized_picks,
        value_bets=tuple(value_bets),
        exclusions=tuple(exclusions),
        opportunities=tuple(opportunities),
        provisional=provisional,
        provisional_label=provisional_label if provisional else None,
        notes=(
            "Replay logic reuses current A1/A2/A3/Manager/Market/Veto/staking.",
            "Odds never enter predictive agents.",
            "Replay rows are synthetic and must never be confused with ORIGINAL LIVE bets.",
        ),
    )


__all__ = ["run_replay", "run_replay_run"]
