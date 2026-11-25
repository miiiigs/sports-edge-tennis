"""Manager interaction with corrected Agent 1 probabilities on matched populations.

The identity bridge changes Agent 1's probability and sometimes its side. That can
change the Manager's consensus decision even when Agent 2 and Agent 3 are untouched.
This module measures that interaction on the *same* matches rather than on a
re-selected slate:

* population: latest original Agent 1 target per match since the frozen window start,
  with all three agents present in the same completed, non-experiment run and a
  safely settled ``COMPLETED`` result;
* original Manager decision: re-evaluated from the stored predictions and the stored
  manager policy version, so the counterfactual differs only in Agent 1;
* counterfactual Manager decision: identical inputs except Agent 1 is replaced by the
  PIT-identified, bridged Agent 1 probability/side.

Read-only. No writes. ``can_promote=false``. The 52-target run-2548 cohort stays
quarantined and is reported separately by the existing counterfactual artifact.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.agents.contracts import AGENT1_RAW_PROBABILITY_V1
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.data.model_identity import REFERENCE_POLICY_STRICT
from sports_edge_tennis.data.prediction_persistence import (
    PersistedPredictiveAgentPrediction,
)
from sports_edge_tennis.manager.consensus import evaluate_consensus
from sports_edge_tennis.manager.contracts import ManagerPolicyV1
from sports_edge_tennis.validation.a1_identity_provenance_audit import (
    SINCE,
    _load_targets,
    _replay,
    _resolve_all,
    dsn_from_env,
)

VERSION = "A1_IDENTITY_MANAGER_INTERACTION_V1"

PREDICTION_SQL = """
SELECT p.prediction_id, p.prediction_run_id, p.match_id, p.agent_code::text,
       p.prediction_status::text, p.selection_side, p.win_probability,
       p.probability_representation, acv.model_version, acv.feature_version,
       acv.config_version, acv.parameter_schema_version,
       p.agent_config_version_id, p.as_of, p.abstain_reason_code
FROM predictions p
JOIN agent_config_versions acv USING (agent_config_version_id)
WHERE p.prediction_run_id = ANY(%(run_ids)s)
  AND p.match_id = ANY(%(match_ids)s)
ORDER BY p.match_id, p.agent_code
"""

MANAGER_SQL = """
SELECT mcr.prediction_run_id, mcr.match_id, mcr.manager_policy_version_id,
       mcr.manager_status::text, mcr.selected_side, mcr.consensus_probability,
       mcr.active_agent_count, mcr.abstaining_agent_count,
       mcr.probability_spread, mcr.reason_code::text, mcr.as_of
FROM manager_consensus_results mcr
WHERE mcr.prediction_run_id = ANY(%(run_ids)s)
  AND mcr.match_id = ANY(%(match_ids)s)
"""

POLICY_SQL = """
SELECT manager_policy_version_id, policy_key, policy_version,
       three_agent_max_probability_spread,
       agent3_abstention_max_probability_spread,
       min_active_agent_probability, allow_agent3_abstention
FROM manager_policy_versions
WHERE manager_policy_version_id = ANY(%s)
"""

OUTCOME_SQL = """
SELECT DISTINCT ON (match_id) match_id, outcome_status::text, winner_side
FROM match_result_observations
WHERE match_id = ANY(%s)
ORDER BY match_id, as_of DESC, source_observed_at DESC,
         ingested_at DESC, match_result_observation_id DESC
"""


def _make_prediction(
    row: Any,
    *,
    side: int | None = None,
    probability: Any = None,
    representation: str | None = None,
) -> PersistedPredictiveAgentPrediction:
    return PersistedPredictiveAgentPrediction(
        prediction_id=int(row[0]),
        prediction_run_id=int(row[1]),
        match_id=int(row[2]),
        agent_code=str(row[3]),
        prediction_status=str(row[4]),
        selection_side=side if side is not None else (
            int(row[5]) if row[5] is not None else None
        ),
        win_probability=(
            probability if probability is not None else row[6]
        ),
        probability_representation=representation or row[7],
        model_version=str(row[8]),
        feature_version=str(row[9]),
        config_version=str(row[10]),
        parameter_schema_version=str(row[11]),
        agent_config_version_id=int(row[12]),
        as_of=row[13],
        abstain_reason_code=row[14],
    )


def _decision_correct(decision: Any, winner_side: int | None) -> bool | None:
    if winner_side not in (1, 2):
        return None
    if decision.selected_side is None:
        return None
    return int(decision.selected_side) == int(winner_side)


def run(conn: psycopg.Connection) -> dict[str, Any]:
    targets = _load_targets(conn)
    resolved, _provenance = _resolve_all(
        conn, targets, reference_policy=REFERENCE_POLICY_STRICT
    )
    history = load_agent1_history(
        conn, cutoff=datetime.now(UTC), bridge_model_identities=True
    )
    # A1 replay only needs the surface; re-derive it with the production resolver so
    # the counterfactual differs from production in identity, not surface.
    from sports_edge_tennis.runner.targets import _resolve_model_surface

    surface_by_prediction: dict[int, str | None] = {}
    for target in targets:
        surface, _status = _resolve_model_surface(
            conn,
            tournament_id=int(target[12]),
            canonical_name=str(target[13]),
            tour_scope=str(target[14]),
            season_year=int(target[16]),
            live_surface=target[10],
            edition_surface=target[11],
            as_of=target[3],
        )
        surface_by_prediction[target[0]] = surface
    bridged_probability = _replay(history, targets, resolved, surface_by_prediction)

    target_by_match = {int(t[1]): t for t in targets}
    run_ids = sorted({int(t[2]) for t in targets})
    match_ids = sorted(target_by_match)
    predictions = conn.execute(
        PREDICTION_SQL, {"run_ids": run_ids, "match_ids": match_ids}
    ).fetchall()
    manager_rows = conn.execute(
        MANAGER_SQL, {"run_ids": run_ids, "match_ids": match_ids}
    ).fetchall()
    policies = {
        int(row[0]): row
        for row in conn.execute(
            POLICY_SQL, (sorted({int(r[2]) for r in manager_rows}),)
        ).fetchall()
    }
    outcomes = {
        int(row[0]): (str(row[1]), int(row[2]) if row[2] is not None else None)
        for row in conn.execute(OUTCOME_SQL, (match_ids,)).fetchall()
    }

    by_match: dict[int, list[Any]] = {}
    for row in predictions:
        by_match.setdefault(int(row[2]), []).append(row)
    manager_by_match = {int(row[1]): row for row in manager_rows}

    rows: list[dict[str, Any]] = []
    skipped = Counter()
    for match_id, target in target_by_match.items():
        agent_rows = by_match.get(match_id, [])
        by_agent = {str(r[3]): r for r in agent_rows}
        a1_row = by_agent.get("AGENT_1_SURFACE_ELO")
        a2_row = by_agent.get("AGENT_2_HIERARCHICAL_MARKOV")
        a3_row = by_agent.get("AGENT_3_INTRANSITIVITY")
        manager_row = manager_by_match.get(match_id)
        if not (a1_row and a2_row and a3_row and manager_row):
            skipped["incomplete_agent_matrix"] += 1
            continue
        policy_row = policies.get(int(manager_row[2]))
        if policy_row is None:
            skipped["missing_policy_version"] += 1
            continue
        if target[0] not in bridged_probability:
            skipped["bridged_replay_failed"] += 1
            continue
        outcome_status, winner_side = outcomes.get(match_id, (None, None))
        if outcome_status != "COMPLETED" or winner_side not in (1, 2):
            skipped["not_safely_settled"] += 1
            continue

        policy = ManagerPolicyV1(
            manager_policy_version_id=int(policy_row[0]),
            policy_key=str(policy_row[1]),
            policy_version=str(policy_row[2]),
            three_agent_max_probability_spread=Decimal(policy_row[3]),
            agent3_abstention_max_probability_spread=Decimal(policy_row[4]),
            min_active_agent_probability=Decimal(policy_row[5]),
            allow_agent3_abstention=bool(policy_row[6]),
        )
        original_inputs = [
            _make_prediction(a1_row),
            _make_prediction(a2_row),
            _make_prediction(a3_row),
        ]
        original = evaluate_consensus(
            original_inputs,
            match_id=match_id,
            prediction_run_id=int(target[2]),
            policy=policy,
            as_of=target[3],
        )
        bridged_p1 = float(bridged_probability[target[0]])
        bridged_side = 1 if bridged_p1 >= 0.5 else 2
        bridged_selected = bridged_p1 if bridged_side == 1 else 1.0 - bridged_p1
        counterfactual_inputs = [
            _make_prediction(
                a1_row,
                side=bridged_side,
                probability=Decimal(str(round(bridged_selected, 6))),
                representation=AGENT1_RAW_PROBABILITY_V1,
            ),
            _make_prediction(a2_row),
            _make_prediction(a3_row),
        ]
        counterfactual = evaluate_consensus(
            counterfactual_inputs,
            match_id=match_id,
            prediction_run_id=int(target[2]),
            policy=policy,
            as_of=target[3],
        )
        rows.append(
            {
                "match_id": match_id,
                "prediction_id": target[0],
                "tour": target[14],
                "original_a1_side": (
                    int(a1_row[5]) if a1_row[5] is not None else None
                ),
                "original_a1_probability": (
                    float(a1_row[6]) if a1_row[6] is not None else None
                ),
                "bridged_a1_side": bridged_side,
                "bridged_a1_probability": bridged_selected,
                "a1_side_changed": (
                    int(a1_row[5]) != bridged_side if a1_row[5] is not None else None
                ),
                "a2_status": str(a2_row[4]),
                "a3_status": str(a3_row[4]),
                "original_manager_status": original.manager_status,
                "original_manager_reason": original.reason_code,
                "original_manager_side": original.selected_side,
                "counterfactual_manager_status": counterfactual.manager_status,
                "counterfactual_manager_reason": counterfactual.reason_code,
                "counterfactual_manager_side": counterfactual.selected_side,
                "manager_decision_changed": (
                    original.manager_status,
                    original.selected_side,
                    original.reason_code,
                )
                != (
                    counterfactual.manager_status,
                    counterfactual.selected_side,
                    counterfactual.reason_code,
                ),
                "winner_side": winner_side,
                "original_a1_correct": (
                    int(a1_row[5]) == winner_side if a1_row[5] is not None else None
                ),
                "bridged_a1_correct": bridged_side == winner_side,
                "original_manager_correct": _decision_correct(original, winner_side),
                "counterfactual_manager_correct": _decision_correct(
                    counterfactual, winner_side
                ),
            }
        )

    original_consensus = [
        row for row in rows if row["original_manager_status"] == "CONSENSUS"
    ]
    counterfactual_consensus = [
        row for row in rows if row["counterfactual_manager_status"] == "CONSENSUS"
    ]

    def _accuracy(subset: list[dict[str, Any]], key: str) -> dict[str, Any]:
        graded = [row for row in subset if row[key] is not None]
        wins = sum(1 for row in graded if row[key])
        return {
            "n": len(graded),
            "wins": wins,
            "losses": len(graded) - wins,
            "accuracy": wins / len(graded) if graded else None,
        }

    reason_transitions = Counter(
        f"{row['original_manager_reason']}->{row['counterfactual_manager_reason']}"
        for row in rows
        if row["manager_decision_changed"]
    )
    both_consensus = [
        row
        for row in rows
        if row["original_manager_status"] == "CONSENSUS"
        and row["counterfactual_manager_status"] == "CONSENSUS"
    ]
    return {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "since": SINCE.isoformat(),
        "population": len(rows),
        "skipped": dict(skipped),
        "a1_side_changes": sum(1 for row in rows if row["a1_side_changed"]),
        "manager_decisions_changed": sum(
            1 for row in rows if row["manager_decision_changed"]
        ),
        "reason_transitions": dict(reason_transitions.most_common(20)),
        "consensus": {
            "original": len(original_consensus),
            "counterfactual": len(counterfactual_consensus),
            "both": len(both_consensus),
            "original_only": sum(
                1
                for row in rows
                if row["original_manager_status"] == "CONSENSUS"
                and row["counterfactual_manager_status"] != "CONSENSUS"
            ),
            "counterfactual_only": sum(
                1
                for row in rows
                if row["counterfactual_manager_status"] == "CONSENSUS"
                and row["original_manager_status"] != "CONSENSUS"
            ),
            "same_side_on_overlap": sum(
                1
                for row in both_consensus
                if row["original_manager_side"] == row["counterfactual_manager_side"]
            ),
        },
        "settled_accuracy": {
            "original_manager_consensus": _accuracy(
                original_consensus, "original_manager_correct"
            ),
            "counterfactual_manager_consensus": _accuracy(
                counterfactual_consensus, "counterfactual_manager_correct"
            ),
            "original_a1": _accuracy(rows, "original_a1_correct"),
            "bridged_a1": _accuracy(rows, "bridged_a1_correct"),
        },
        "by_tour": {
            tour: {
                "population": len([row for row in rows if row["tour"] == tour]),
                "manager_decisions_changed": sum(
                    1
                    for row in rows
                    if row["tour"] == tour and row["manager_decision_changed"]
                ),
                "original_manager_consensus": _accuracy(
                    [row for row in original_consensus if row["tour"] == tour],
                    "original_manager_correct",
                ),
                "counterfactual_manager_consensus": _accuracy(
                    [row for row in counterfactual_consensus if row["tour"] == tour],
                    "counterfactual_manager_correct",
                ),
            }
            for tour in ("ATP_MAIN", "CHALLENGER")
        },
        "can_promote": False,
        "production_changed": False,
        "quarantine_note": (
            "run-2548 52-target cohort remains quarantined; its counterfactual Manager "
            "classification lives in a1_target52_manager_counterfactual_20261125.json"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default="docs/research/evidence/a1_identity_manager_interaction_20261125.json",
    )
    args = parser.parse_args(argv)
    with psycopg.connect(
        dsn_from_env(),
        options="-c default_transaction_read_only=on -c statement_timeout=3600000",
    ) as conn:
        report = run(conn)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str)[:3000])
    print("wrote", out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
