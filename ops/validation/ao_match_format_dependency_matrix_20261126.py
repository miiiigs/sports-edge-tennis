"""Offline AO match-format dependency matrix.

Deterministic counterfactual: identical players, ratings, history, surface,
cutoff and model configuration; only ``best_of_sets`` changes. No database,
no provider request, no production write. It executes the unchanged production
Agent 1, Agent 2 and Manager code paths.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sports_edge_tennis.agents.agent1_surface_elo import (
    HistoricalMatchObservation,
)
from sports_edge_tennis.agents.agent1_surface_elo import (
    PredictionRequest as Agent1Request,
)
from sports_edge_tennis.agents.agent1_surface_elo import (
    predict as agent1_predict,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    ScoringRuleVersion,
    match_probability,
)
from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.data.prediction_persistence import (
    PersistedPredictiveAgentPrediction,
)
from sports_edge_tennis.manager.consensus import evaluate_consensus
from sports_edge_tennis.manager.contracts import (
    AGENT3_ABSTENTION_MAX_PROBABILITY_SPREAD,
    MANAGER_POLICY_KEY,
    MIN_ACTIVE_AGENT_PROBABILITY,
    THREE_AGENT_MAX_PROBABILITY_SPREAD,
    ManagerPolicyV1,
    consensus_mean,
)

AS_OF = datetime(2027, 1, 19, 0, 0, tzinfo=UTC)
P1_SERVE = Decimal("0.65")
P2_SERVE = Decimal("0.62")


def independent_set_match_probability(set_win_probability: float, best_of_sets: int) -> float:
    import math

    sets_to_win = best_of_sets // 2 + 1
    probability = 0.0
    for losses in range(sets_to_win):
        wins = sets_to_win
        probability += (
            math.comb(wins + losses - 1, losses)
            * set_win_probability**wins
            * (1.0 - set_win_probability) ** losses
        )
    return probability


def _history() -> list[HistoricalMatchObservation]:
    start = AS_OF - timedelta(days=20)
    observations: list[HistoricalMatchObservation] = []
    for index in range(6):
        observations.append(
            HistoricalMatchObservation(
                match_id=1000 + index,
                player_1_id=1,
                player_2_id=2,
                surface="HARD",
                tour_scope="ATP_MAIN",
                competition_tier="GRAND_SLAM",
                best_of_sets=3,
                qualifying_flag=False,
                match_start_at=start + timedelta(days=index * 3),
                outcome_status="COMPLETED",
                winner_side=1,
                source_observed_at=start + timedelta(days=index * 3, hours=3),
                as_of=start + timedelta(days=index * 3, hours=4),
            )
        )
    return observations


def _agent1_probability(best_of_sets: int) -> Decimal:
    request = Agent1Request(
        player_1_id=1,
        player_2_id=2,
        surface="HARD",
        tour_scope="ATP_MAIN",
        competition_tier="GRAND_SLAM",
        best_of_sets=best_of_sets,
        qualifying_flag=False,
        as_of=AS_OF,
    )
    return agent1_predict(request, _history()).win_probability


def _agent2_probability(rule: ScoringRuleVersion) -> Decimal:
    return sum(
        match_probability(
            player_1_serve_probability=P1_SERVE,
            player_2_serve_probability=P2_SERVE,
            scoring_rule_version=rule,
            first_server=server,
        )
        for server in (1, 2)
    ) / 2


def _prediction(agent_code: str, probability: Decimal) -> PersistedPredictiveAgentPrediction:
    return PersistedPredictiveAgentPrediction(
        prediction_id=1,
        prediction_run_id=1,
        match_id=1,
        agent_code=agent_code,
        prediction_status="PREDICTED",
        selection_side=1,
        win_probability=probability,
        probability_representation=AGENT_PROBABILITY_REPRESENTATIONS[agent_code],
        model_version="offline_counterfactual",
        feature_version="offline_counterfactual",
        config_version="offline_counterfactual",
        parameter_schema_version="offline_counterfactual",
        agent_config_version_id=1,
        as_of=AS_OF,
    )


def _manager_probability(agent2_probability: Decimal) -> Decimal:
    policy = ManagerPolicyV1(
        manager_policy_version_id=1,
        policy_key=MANAGER_POLICY_KEY,
        policy_version="v1",
        three_agent_max_probability_spread=THREE_AGENT_MAX_PROBABILITY_SPREAD,
        agent3_abstention_max_probability_spread=AGENT3_ABSTENTION_MAX_PROBABILITY_SPREAD,
        min_active_agent_probability=MIN_ACTIVE_AGENT_PROBABILITY,
        allow_agent3_abstention=True,
    )
    result = evaluate_consensus(
        [
            _prediction("AGENT_1_SURFACE_ELO", Decimal("0.660000")),
            _prediction("AGENT_2_HIERARCHICAL_MARKOV", agent2_probability),
            _prediction("AGENT_3_INTRANSITIVITY", Decimal("0.670000")),
        ],
        match_id=1,
        prediction_run_id=1,
        policy=policy,
        as_of=AS_OF,
    )
    if result.manager_status != "CONSENSUS" or result.consensus_probability is None:
        raise RuntimeError(f"unexpected Manager status {result.manager_status}")
    return result.consensus_probability


def build_matrix() -> dict[str, object]:
    bo3 = _agent2_probability(ScoringRuleVersion.BO3_TB7_ALL_SETS)
    bo5 = _agent2_probability(ScoringRuleVersion.BO5_DECIDER_TB10_AT_6_6)
    a1_bo3 = _agent1_probability(3)
    a1_bo5 = _agent1_probability(5)
    m_bo3 = _manager_probability(bo3)
    m_bo5 = _manager_probability(bo5)
    mean_delta = consensus_mean(
        (Decimal("0.660000"), bo5, Decimal("0.670000"))
    ) - consensus_mean((Decimal("0.660000"), bo3, Decimal("0.670000")))
    return {
        "version": "AO_MATCH_FORMAT_DEPENDENCY_MATRIX_20261126_V1",
        "mode": "OFFLINE_DETERMINISTIC_NO_DB_NO_PROVIDER",
        "counterfactual": {
            "changed_field": "best_of_sets",
            "held_constant": [
                "players",
                "ratings/history",
                "surface",
                "prediction_cutoff",
                "model_configuration",
            ],
            "agent2_serve_probabilities": {
                "player_1": str(P1_SERVE),
                "player_2": str(P2_SERVE),
            },
            "as_of": AS_OF.isoformat(),
        },
        "independent_set_reference": {
            "set_win_probability": 0.60,
            "bo3": round(independent_set_match_probability(0.60, 3), 12),
            "bo5": round(independent_set_match_probability(0.60, 5), 12),
            "note": "Mathematical reference only, not an Agent implementation requirement.",
        },
        "agent1": {
            "format_dependent": a1_bo3 != a1_bo5,
            "bo3_probability": str(a1_bo3),
            "bo5_probability": str(a1_bo5),
            "delta": str(a1_bo5 - a1_bo3),
            "reason": "A1 validates best_of_sets but its Elo expectation and probability use rating and surface only.",
        },
        "agent2": {
            "format_dependent": bo3 != bo5,
            "bo3_probability": str(bo3),
            "bo5_probability": str(bo5),
            "delta": str(bo5 - bo3),
            "conversion_count": 1,
            "reason": "The serve-based Markov engine resolves games, tiebreaks, sets and the match in one model; changing the scoring rule changes match length once.",
        },
        "manager": {
            "bo3_consensus": str(m_bo3),
            "bo5_consensus": str(m_bo5),
            "delta": str(m_bo5 - m_bo3),
            "mean_effect": str(mean_delta),
            "delta_equals_mean_effect": (m_bo5 - m_bo3) == mean_delta,
            "second_format_adjustment": False,
            "reason": "Manager takes the unweighted mean of active agent probabilities and applies no match-format conversion.",
        },
        "conclusion": [
            "Agent 1 is inherently format-insensitive by design, not because history is absent.",
            "Agent 2 is format-sensitive only through its single Markov scoring-rule conversion.",
            "Manager is a probability aggregator and does not duplicate the Agent 2 conversion.",
            "Correcting AO history from BO3 to BO5 cannot move Agent 1 because A1 never reads the value in its probability path.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)
    matrix = build_matrix()
    with open(args.out_json, "w", encoding="utf-8") as handle:
        json.dump(matrix, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(matrix, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
