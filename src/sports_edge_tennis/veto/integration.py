from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import psycopg

from sports_edge_tennis.market import evaluator as market_evaluator
from sports_edge_tennis.market.contracts import (
    MarketExecutionError,
    MarketIdempotencyMismatchError,
    MarketSlateOutcome,
    SharpReferenceSpec,
)
from sports_edge_tennis.market.persistence import (
    load_competition_tier,
    load_manager_slate,
    load_market_policy_version,
    load_match_winner_odds,
    load_run_parent,
)
from sports_edge_tennis.market.policy import validate_sharp_reference
from sports_edge_tennis.veto.service import (
    evaluate_veto_for_decision,
    persist_veto_audit,
)


def _decision_key(decision) -> tuple[int, int, datetime, str]:
    return (
        decision.manager_consensus_result_id,
        decision.market_policy_version_id,
        decision.decision_as_of,
        decision.snapshot_lineage_hash,
    )


def _mark_decision_vetoed(decision):
    """Preserve priced decision evidence while clearing executable stake guidance."""
    return replace(
        decision,
        decision_status="VETOED",
        reason_code="VETO_TRIGGERED",
        staking_rule_version=None,
        staking_probability=None,
        staking_decimal_price=None,
        full_kelly_fraction=None,
        quarter_kelly_fraction=None,
        suggested_stake_fraction=None,
    )


def evaluate_and_persist_market_slate_with_veto(
    conn: psycopg.Connection,
    *,
    prediction_run_id: int,
    market_policy_version_id: int,
    decision_as_of: datetime,
    market_execution_now: datetime,
    sharp_reference: SharpReferenceSpec,
) -> MarketSlateOutcome:
    """Run Market V1, then apply a kill-only Veto before persistence.

    The existing Market evaluator remains the source of model/price truth. Veto
    evaluation is invoked only for decisions that Market would otherwise persist
    as BET. It cannot convert a NO_BET into a BET and cannot alter probabilities,
    prices, thresholds, or Manager state.

    Market internals are called through the evaluator module rather than copied
    function aliases. This preserves the existing failure-injection/rollback test
    contract and ensures a persistence failure still aborts the whole transaction.
    """
    if market_execution_now < decision_as_of:
        raise MarketExecutionError("market_execution_now must be >= decision_as_of")

    with conn.transaction():
        run_status, contract_version, run_as_of, finalized_at = load_run_parent(
            conn, prediction_run_id=prediction_run_id
        )
        if contract_version != "predictive_agent_result_v1":
            raise MarketExecutionError("Market consumes only predictive_agent_result_v1 runs")
        if run_status != "COMPLETED":
            raise MarketExecutionError("Market consumes only COMPLETED prediction runs")
        if finalized_at is None:
            raise MarketExecutionError("Market consumes only finalized prediction runs")
        if decision_as_of < run_as_of:
            raise MarketExecutionError("decision_as_of must be >= prediction_run.as_of")

        policy = load_market_policy_version(
            conn, market_policy_version_id=market_policy_version_id
        )
        if policy.type_b_enabled:
            raise MarketExecutionError("Type B is disabled in V1")
        validate_sharp_reference(policy, sharp_reference)

        manager_rows = load_manager_slate(conn, prediction_run_id=prediction_run_id)
        if not manager_rows:
            return MarketSlateOutcome(
                prediction_run_id=prediction_run_id,
                manager_result_count=0,
                new_decision_count=0,
                existing_decision_count=0,
                qualified_bet_count=0,
                status="EMPTY_SLATE",
            )

        decisions = []
        veto_evaluations: dict[tuple[int, int, datetime, str], object] = {}
        for manager_row in manager_rows:
            competition_tier = load_competition_tier(
                conn,
                match_id=manager_row.match_id,
                decision_as_of=decision_as_of,
            )
            odds_snapshots = load_match_winner_odds(
                conn,
                match_id=manager_row.match_id,
                decision_as_of=decision_as_of,
            )
            decision = market_evaluator.evaluate_market_match(
                manager_row=manager_row,
                policy=policy,
                competition_tier=competition_tier,
                sharp_reference=sharp_reference,
                odds_snapshots=odds_snapshots,
                decision_as_of=decision_as_of,
            )
            if decision.decision_status == "BET":
                evaluation = evaluate_veto_for_decision(
                    conn,
                    decision=decision,
                    sharp_reference=sharp_reference,
                )
                if evaluation.vetoed:
                    decision = _mark_decision_vetoed(decision)
                veto_evaluations[_decision_key(decision)] = evaluation
            decisions.append(decision)

        existing = market_evaluator._load_existing_market_decisions(
            conn, prediction_run_id
        )
        before_count = len(existing)
        new_decisions = []
        existing_count = 0

        for decision in decisions:
            key = _decision_key(decision)
            persisted = existing.get(key)
            if persisted is not None:
                expected = market_evaluator._canonical_decision_truth(
                    decision,
                    decision_id=persisted.decision_id,
                )
                if persisted != expected:
                    raise MarketIdempotencyMismatchError(
                        "persisted Market/Veto truth differs from the computed canonical decision"
                    )
                existing_count += 1
                evaluation = veto_evaluations.get(key)
                if evaluation is not None:
                    persist_veto_audit(
                        conn,
                        decision_id=persisted.decision_id,
                        evaluation_as_of=decision.decision_as_of,
                        evaluation=evaluation,
                    )
            else:
                new_decisions.append(decision)

        for decision in new_decisions:
            decision_id = market_evaluator._persist_decision(
                conn, decision, market_execution_now
            )
            market_evaluator._persist_lineage(conn, decision_id, decision.lineage)
            evaluation = veto_evaluations.get(_decision_key(decision))
            if evaluation is not None:
                persist_veto_audit(
                    conn,
                    decision_id=decision_id,
                    evaluation_as_of=decision.decision_as_of,
                    evaluation=evaluation,
                )

        after_count = market_evaluator._count_market_decisions(
            conn, prediction_run_id
        )
        if after_count != before_count + len(new_decisions):
            raise MarketExecutionError(
                "Market/Veto slate persistence produced an unexpected row count "
                f"({after_count} != {before_count + len(new_decisions)})"
            )

        qualified_bets = sum(
            1 for decision in decisions if decision.decision_status == "BET"
        )
        status = "PERSISTED" if new_decisions else "IDEMPOTENT_SUCCESS"
        return MarketSlateOutcome(
            prediction_run_id=prediction_run_id,
            manager_result_count=len(decisions),
            new_decision_count=len(new_decisions),
            existing_decision_count=existing_count,
            qualified_bet_count=qualified_bets,
            status=status,
        )
