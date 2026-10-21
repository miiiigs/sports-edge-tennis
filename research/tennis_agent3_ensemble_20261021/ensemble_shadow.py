"""Shadow-only Manager impact of Agent 2 temperature calibration.

This module preserves every original Agent 1/2/3 probability and every original
Manager decision. It computes a hypothetical Manager decision after replacing
only Agent 2's selected-side probability with the PR #248 temperature-scaled
probability. Nothing here is imported by production code.

The retrospective comparison is an impact audit, not a holdout. The
pre-registered acceptance rule requires a forward shadow (see
``preregistration.json``).
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Mapping

from research.a2_calibration_shadow.calibrators import CalibrationModel
from research.tennis_agent3_ensemble_20261021.analysis import (
    _ece,
    _metrics,
    _paired_bootstrap,
    _scored,
    _wilson,
    _won,
    load_jsonl,
)
from research.tennis_agent3_ensemble_20261021.policy import (
    AGENT_1,
    AGENT_2,
    AGENT_3,
    AgentVote,
    Decision,
    PolicyConfig,
    policy_from_row,
    simulate_manager,
)

EXPERIMENT_ID = "tennis_agent3_ensemble_20261021"
TEMPERATURE_SLOPE = 0.6439254782294896
TEMPERATURE_SOURCE = "PR #248 / research/a2_calibration_shadow/artifacts/selection.json"


def _vote(
    agent_code: str,
    status: str,
    side: object,
    probability: object,
) -> AgentVote:
    if status == "PREDICTED":
        if side not in (1, 2) or probability is None:
            raise ValueError(f"{agent_code} PREDICTED requires side and probability")
        return AgentVote(agent_code, "PREDICTED", int(side), Decimal(str(probability)))
    return AgentVote(agent_code, "ABSTAINED", None, None)


def _calibrated_probability(model: CalibrationModel, probability: object) -> float:
    return model.predict(float(probability))


def _decision_fields(prefix: str, decision: Decision) -> dict[str, Any]:
    return {
        f"{prefix}_status": decision.manager_status,
        f"{prefix}_side": decision.selected_side,
        f"{prefix}_probability": float(decision.consensus_probability)
        if decision.consensus_probability is not None
        else None,
        f"{prefix}_spread": float(decision.probability_spread)
        if decision.probability_spread is not None
        else None,
        f"{prefix}_reason": decision.reason_code,
        f"{prefix}_active": decision.active_agent_count,
    }


def evaluate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    model = CalibrationModel(
        name="temperature",
        kind="temperature",
        parameters={"slope": TEMPERATURE_SLOPE},
    )
    evaluated: list[dict[str, Any]] = []
    for row in rows:
        policy = policy_from_row(row)
        baseline_votes = {
            AGENT_1: _vote(AGENT_1, row["a1_status"], row["a1_side"], row["a1_probability"]),
            AGENT_2: _vote(AGENT_2, row["a2_status"], row["a2_side"], row["a2_probability"]),
            AGENT_3: _vote(AGENT_3, row["a3_status"], row["a3_side"], row["a3_probability"]),
        }
        baseline = simulate_manager(baseline_votes, policy=policy)
        calibrated_a2 = (
            _calibrated_probability(model, row["a2_probability"])
            if row["a2_status"] == "PREDICTED"
            else None
        )
        candidate_votes = {
            AGENT_1: baseline_votes[AGENT_1],
            AGENT_2: AgentVote(
                AGENT_2,
                "PREDICTED",
                int(row["a2_side"]),
                Decimal(str(calibrated_a2)),
            )
            if calibrated_a2 is not None
            else baseline_votes[AGENT_2],
            AGENT_3: baseline_votes[AGENT_3],
        }
        candidate = simulate_manager(candidate_votes, policy=policy)

        outcome = _won(row, baseline.selected_side)
        candidate_outcome = _won(row, candidate.selected_side)
        if baseline.manager_status != candidate.manager_status:
            if baseline.manager_status == "NO_BET" and candidate.manager_status == "CONSENSUS":
                gate_flip = "NEW_CONSENSUS"
            elif baseline.manager_status == "CONSENSUS" and candidate.manager_status == "NO_BET":
                gate_flip = "LOST_CONSENSUS"
            else:
                gate_flip = "STATUS_CHANGE"
        elif baseline.reason_code != candidate.reason_code:
            gate_flip = "REASON_CHANGED"
        else:
            gate_flip = "NONE"

        evaluated.append(
            {
                "match_id": int(row["match_id"]),
                "prediction_run_id": int(row["prediction_run_id"]),
                "manager_as_of": row["manager_as_of"],
                "surface": row.get("surface") or "<missing>",
                "competition_tier": row.get("competition_tier") or "<missing>",
                "policy_version": row["policy_version"],
                "a1_status": row["a1_status"],
                "a1_side": row["a1_side"],
                "a1_probability": float(row["a1_probability"]) if row["a1_probability"] is not None else None,
                "a2_status": row["a2_status"],
                "a2_side": row["a2_side"],
                "a2_probability_original": float(row["a2_probability"])
                if row["a2_probability"] is not None
                else None,
                "a2_probability_calibrated": calibrated_a2,
                "a3_status": row["a3_status"],
                "a3_side": row["a3_side"],
                "a3_probability": float(row["a3_probability"])
                if row["a3_probability"] is not None
                else None,
                **_decision_fields("baseline", baseline),
                **_decision_fields("candidate", candidate),
                "selection_changed": (
                    baseline.selected_side is not None
                    and candidate.selected_side is not None
                    and baseline.selected_side != candidate.selected_side
                ),
                "gate_flip": gate_flip,
                "spread_delta": (
                    float(candidate.probability_spread - baseline.probability_spread)
                    if candidate.probability_spread is not None
                    and baseline.probability_spread is not None
                    else None
                ),
                "outcome_status": row.get("outcome_status"),
                "winner_side": row.get("winner_side"),
                "baseline_won": outcome,
                "candidate_won": candidate_outcome,
            }
        )
    return evaluated


def _brier(probability: float | None, won: bool | None) -> float | None:
    if probability is None or won is None:
        return None
    return (probability - float(won)) ** 2


def _log_loss(probability: float | None, won: bool | None) -> float | None:
    if probability is None or won is None:
        return None
    clipped = min(max(probability, 1e-12), 1 - 1e-12)
    return -math.log(clipped if won else 1 - clipped)


def _metric_summary(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    selected = [row for row in rows if row[f"{prefix}_won"] is not None]
    return _metrics(
        [row[f"{prefix}_probability"] for row in selected],
        [bool(row[f"{prefix}_won"]) for row in selected],
    )


def _confidence_interval(values: list[float]) -> dict[str, Any]:
    return _paired_bootstrap(values)


def _group_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "<missing>")].append(row)
    result = {}
    for group, members in sorted(groups.items()):
        baseline = _metric_summary(members, "baseline")
        candidate = _metric_summary(members, "candidate")
        result[group] = {
            "n": len(members),
            "baseline_consensus": baseline["n"],
            "candidate_consensus": candidate["n"],
            "baseline_accuracy": baseline["accuracy"],
            "candidate_accuracy": candidate["accuracy"],
            "baseline_brier": baseline["brier"],
            "candidate_brier": candidate["brier"],
            "baseline_log_loss": baseline["log_loss"],
            "candidate_log_loss": candidate["log_loss"],
        }
    return result


def _group_common_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Paired metrics on rows where both policies issue a consensus."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "<missing>")].append(row)
    result = {}
    for group, members in sorted(groups.items()):
        common = [
            row
            for row in members
            if row["baseline_status"] == "CONSENSUS"
            and row["candidate_status"] == "CONSENSUS"
            and row["baseline_won"] is not None
        ]
        flips = Counter(row["gate_flip"] for row in members)
        baseline = _metric_summary(common, "baseline")
        candidate = _metric_summary(common, "candidate")
        deltas = [
            _brier(row["candidate_probability"], row["candidate_won"])
            - _brier(row["baseline_probability"], row["baseline_won"])
            for row in common
        ]
        result[group] = {
            "matches": len(members),
            "common_scored": baseline["n"],
            "baseline_accuracy": baseline["accuracy"],
            "candidate_accuracy": candidate["accuracy"],
            "baseline_brier": baseline["brier"],
            "candidate_brier": candidate["brier"],
            "brier_delta_mean": (sum(deltas) / len(deltas)) if deltas else None,
            "brier_delta_ci95": _paired_bootstrap(deltas)["ci95"] if deltas else None,
            "gate_flips": dict(flips),
        }
    return result


def summarize(evaluated: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_status = Counter(row["baseline_status"] for row in evaluated)
    candidate_status = Counter(row["candidate_status"] for row in evaluated)
    gate_flips = Counter(row["gate_flip"] for row in evaluated)
    baseline_reasons = Counter(row["baseline_reason"] for row in evaluated)
    candidate_reasons = Counter(row["candidate_reason"] for row in evaluated)
    selection_changes = [row for row in evaluated if row["selection_changed"]]

    common = [
        row
        for row in evaluated
        if row["baseline_status"] == "CONSENSUS"
        and row["candidate_status"] == "CONSENSUS"
        and row["baseline_won"] is not None
    ]
    baseline_common = _metric_summary(common, "baseline")
    candidate_common = _metric_summary(common, "candidate")
    brier_deltas = [
        _brier(row["candidate_probability"], row["candidate_won"])
        - _brier(row["baseline_probability"], row["baseline_won"])
        for row in common
    ]
    log_loss_deltas = [
        _log_loss(row["candidate_probability"], row["candidate_won"])
        - _log_loss(row["baseline_probability"], row["baseline_won"])
        for row in common
    ]
    brier_deltas = [value for value in brier_deltas if value is not None]
    log_loss_deltas = [value for value in log_loss_deltas if value is not None]

    new_consensus = [
        row
        for row in evaluated
        if row["gate_flip"] == "NEW_CONSENSUS" and row["candidate_won"] is not None
    ]
    lost_consensus = [
        row
        for row in evaluated
        if row["gate_flip"] == "LOST_CONSENSUS" and row["baseline_won"] is not None
    ]

    a2 = [
        row
        for row in evaluated
        if row["a2_status"] == "PREDICTED" and row["baseline_won"] is not None
    ]
    a2_selected_side_outcome = [
        row for row in a2 if row["a2_side"] in (1, 2)
    ]
    a2_outcomes = [int(row["winner_side"]) == int(row["a2_side"]) for row in a2_selected_side_outcome]
    a2_baseline = _metrics(
        [row["a2_probability_original"] for row in a2_selected_side_outcome], a2_outcomes
    )
    a2_candidate = _metrics(
        [row["a2_probability_calibrated"] for row in a2_selected_side_outcome], a2_outcomes
    )

    high_confidence = {}
    for threshold in (0.8, 0.9):
        high_confidence[str(threshold)] = {
            "baseline": sum(
                1
                for row in evaluated
                if row["a2_probability_original"] is not None
                and row["a2_probability_original"] >= threshold
            ),
            "calibrated": sum(
                1
                for row in evaluated
                if row["a2_probability_calibrated"] is not None
                and row["a2_probability_calibrated"] >= threshold
            ),
        }

    cases_worse = [
        row
        for row in common
        if _brier(row["candidate_probability"], row["candidate_won"])
        > _brier(row["baseline_probability"], row["baseline_won"]) + 1e-12
    ]
    cases_better = [
        row
        for row in common
        if _brier(row["candidate_probability"], row["candidate_won"])
        < _brier(row["baseline_probability"], row["baseline_won"]) - 1e-12
    ]
    spread_deltas = [row["spread_delta"] for row in evaluated if row["spread_delta"] is not None]

    return {
        "population": {
            "matches": len(evaluated),
            "scored_matches": sum(1 for row in evaluated if row["winner_side"] is not None),
            "baseline_consensus": baseline_status.get("CONSENSUS", 0),
            "candidate_consensus": candidate_status.get("CONSENSUS", 0),
            "coverage_delta": baseline_status.get("CONSENSUS", 0)
            - candidate_status.get("CONSENSUS", 0),
        },
        "decisions": {
            "baseline_status": dict(baseline_status),
            "candidate_status": dict(candidate_status),
            "baseline_reasons": dict(baseline_reasons),
            "candidate_reasons": dict(candidate_reasons),
            "gate_flips": dict(gate_flips),
            "selection_changes": len(selection_changes),
            "spread_delta_median": median(spread_deltas) if spread_deltas else None,
            "spread_delta_min": min(spread_deltas) if spread_deltas else None,
            "spread_delta_max": max(spread_deltas) if spread_deltas else None,
        },
        "common_population": {
            "n": len(common),
            "baseline": baseline_common,
            "candidate": candidate_common,
            "brier_delta": _confidence_interval(brier_deltas),
            "log_loss_delta": _confidence_interval(log_loss_deltas),
            "accuracy_delta": (
                (candidate_common["accuracy"] - baseline_common["accuracy"])
                if candidate_common["accuracy"] is not None
                and baseline_common["accuracy"] is not None
                else None
            ),
            "cases_better": len(cases_better),
            "cases_worse": len(cases_worse),
        },
        "new_consensus": {
            "scored": len(new_consensus),
            "wins": sum(1 for row in new_consensus if bool(row["candidate_won"])),
            "accuracy": (
                sum(1 for row in new_consensus if bool(row["candidate_won"])) / len(new_consensus)
                if new_consensus
                else None
            ),
            "accuracy_wilson": (
                _wilson(
                    sum(1 for row in new_consensus if bool(row["candidate_won"])),
                    len(new_consensus),
                )
                if new_consensus
                else None
            ),
        },
        "lost_consensus": {
            "scored": len(lost_consensus),
            "baseline_wins": sum(1 for row in lost_consensus if bool(row["baseline_won"])),
            "baseline_losses": sum(1 for row in lost_consensus if not bool(row["baseline_won"])),
            "baseline_accuracy": (
                sum(1 for row in lost_consensus if bool(row["baseline_won"])) / len(lost_consensus)
                if lost_consensus
                else None
            ),
            "baseline_accuracy_wilson": (
                _wilson(
                    sum(1 for row in lost_consensus if bool(row["baseline_won"])),
                    len(lost_consensus),
                )
                if lost_consensus
                else None
            ),
        },
        "agent2_calibration": {
            "n": len(a2_selected_side_outcome),
            "baseline": a2_baseline,
            "calibrated": a2_candidate,
            "brier_delta": (
                (a2_candidate["brier"] - a2_baseline["brier"])
                if a2_candidate["brier"] is not None and a2_baseline["brier"] is not None
                else None
            ),
            "log_loss_delta": (
                (a2_candidate["log_loss"] - a2_baseline["log_loss"])
                if a2_candidate["log_loss"] is not None and a2_baseline["log_loss"] is not None
                else None
            ),
            "ece_delta": (
                (a2_candidate["ece"] - a2_baseline["ece"])
                if a2_candidate["ece"] is not None and a2_baseline["ece"] is not None
                else None
            ),
            "high_confidence_display_coverage": high_confidence,
        },
        "by_surface": _group_metrics(evaluated, "surface"),
        "by_tier": _group_metrics(evaluated, "competition_tier"),
        "by_surface_common": _group_common_metrics(evaluated, "surface"),
        "by_tier_common": _group_common_metrics(evaluated, "competition_tier"),
        "worsening_cases": [
            {
                "match_id": row["match_id"],
                "baseline_probability": row["baseline_probability"],
                "candidate_probability": row["candidate_probability"],
                "won": row["baseline_won"],
                "brier_delta": _brier(row["candidate_probability"], row["candidate_won"])
                - _brier(row["baseline_probability"], row["baseline_won"]),
            }
            for row in sorted(
                cases_worse,
                key=lambda item: _brier(item["candidate_probability"], item["candidate_won"])
                - _brier(item["baseline_probability"], item["baseline_won"]),
                reverse=True,
            )[:10]
        ],
    }


PREREGISTRATION = {
    "experiment_id": EXPERIMENT_ID,
    "protocol_version": "tennis_agent3_ensemble_shadow_prereg_v1",
    "status": "retrospective impact audit; forward shadow required",
    "question": (
        "Does preserving the issued Manager policy while replacing only Agent 2's "
        "selected-side probability with the PR #248 temperature calibration improve "
        "decision quality without silently changing selections or coverage?"
    ),
    "baseline": "Issued A1/A2/A3 probabilities and issued Manager decision.",
    "candidate": (
        "Same A1/A3 probabilities and same Manager policy; only A2 selected-side "
        "probability is temperature-scaled with slope 0.6439254782294896."
    ),
    "primary_metrics": [
        "selected-side change count (must be 0)",
        "Manager gate flips by direction and reason",
        "consensus coverage retention",
        "paired Brier and log-loss delta on the common scored population",
        "accuracy on retained and newly qualified picks",
        "A2 Brier/log-loss/ECE and high-confidence display coverage",
    ],
    "guardrails": {
        "production_model_activation": "prohibited",
        "manager_threshold_changes": "prohibited",
        "a1_a2_weighting_changes": "prohibited",
        "betting_logic_or_stake_changes": "prohibited",
        "future_data": (
            "Policy decisions use only predictions and policy rows with as_of <= "
            "the row's manager_as_of; outcomes are joined only for retrospective scoring."
        ),
    },
    "acceptance_for_forward_shadow": {
        "selection_changes": 0,
        "common_population_brier_delta_ci_upper": 0.0,
        "common_population_log_loss_delta_ci_upper": 0.0,
        "new_consensus_accuracy_floor": 0.55,
        "lost_consensus_winners": "reported per 100, not hidden",
        "by_surface_or_tier_degradation": (
            "no subgroup with n>=30 may show a paired Brier delta upper CI above 0.0"
        ),
        "coverage": "net coverage change reported; no minimum coverage gain is assumed",
        "forward_window": "at least 8 weeks and 50 scored Manager decisions",
    },
    "rejection_triggers": [
        "any selected-side change without an explicit new policy",
        "paired Brier or log-loss delta upper 95% CI above zero",
        "materially worse accuracy in a subgroup with n>=30",
        "new-consensus accuracy below the pre-registered floor",
    ],
}


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4%}"


def _num(value: float | None, digits: int = 6) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}"


def write_tables(summary: Mapping[str, Any], path: Path) -> None:
    lines = [
        "# Ensemble shadow tables",
        "",
        "## Decisions",
        "",
        "| Metric | Baseline | Candidate |",
        "| --- | ---: | ---: |",
        f"| Manager consensus | {summary['population']['baseline_consensus']:,} | {summary['population']['candidate_consensus']:,} |",
        f"| Scored matches | {summary['population']['scored_matches']:,} | {summary['population']['scored_matches']:,} |",
        "",
        "## Gate flips",
        "",
        "| Flip | Matches |",
        "| --- | ---: |",
    ]
    for key, value in summary["decisions"]["gate_flips"].items():
        lines.append(f"| {key} | {value:,} |")
    lines.extend(
        [
            "",
            "## Common scored population",
            "",
            "| Metric | Baseline | Candidate | Delta | 95% CI |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    common = summary["common_population"]
    for name in ("brier", "log_loss", "ece"):
        baseline = common["baseline"][name]
        candidate = common["candidate"][name]
        if baseline is None or candidate is None:
            continue
        lines.append(f"| {name} | {baseline:.6f} | {candidate:.6f} | {candidate - baseline:+.6f} | n/a |")
    brier_ci = common["brier_delta"]["ci95"]
    log_ci = common["log_loss_delta"]["ci95"]
    if brier_ci:
        lines.append(
            f"| Brier paired delta | | | {common['brier_delta']['mean']:+.6f} | "
            f"[{brier_ci[0]:+.6f}, {brier_ci[1]:+.6f}] |"
        )
    else:
        lines.append("| Brier paired delta | | | n/a | n/a |")
    if log_ci:
        lines.append(
            f"| Log-loss paired delta | | | {common['log_loss_delta']['mean']:+.6f} | "
            f"[{log_ci[0]:+.6f}, {log_ci[1]:+.6f}] |"
        )
    else:
        lines.append("| Log-loss paired delta | | | n/a | n/a |")
    lines.extend(
        [
            "",
            "## Agent 2 calibration",
            "",
            "| Metric | Baseline | Calibrated |",
            "| --- | ---: | ---: |",
            f"| Brier | {summary['agent2_calibration']['baseline']['brier']:.6f} | {summary['agent2_calibration']['calibrated']['brier']:.6f} |",
            f"| Log loss | {summary['agent2_calibration']['baseline']['log_loss']:.6f} | {summary['agent2_calibration']['calibrated']['log_loss']:.6f} |",
            f"| ECE | {summary['agent2_calibration']['baseline']['ece']:.6f} | {summary['agent2_calibration']['calibrated']['ece']:.6f} |",
            "",
            "## Coverage churn",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| New consensus scored / wins / accuracy | {summary['new_consensus']['scored']} / {summary['new_consensus']['wins']} / {_pct(summary['new_consensus']['accuracy'])} |",
            f"| Lost consensus scored / baseline wins / accuracy | {summary['lost_consensus']['scored']} / {summary['lost_consensus']['baseline_wins']} / {_pct(summary['lost_consensus']['baseline_accuracy'])} |",
            f"| Selection changes | {summary['decisions']['selection_changes']} |",
            "",
            "## Paired common population by surface",
            "",
            "| Surface | Common scored | Baseline accuracy | Candidate accuracy | Brier delta | 95% CI | Lost | New |",
            "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for group, values in summary["by_surface_common"].items():
        ci = values["brier_delta_ci95"]
        ci_text = f"[{ci[0]:+.6f}, {ci[1]:+.6f}]" if ci else "n/a"
        flips = values["gate_flips"]
        lines.append(
            f"| {group} | {values['common_scored']} | "
            f"{_pct(values['baseline_accuracy'])} | {_pct(values['candidate_accuracy'])} | "
            f"{_num(values['brier_delta_mean'])} | {ci_text} | "
            f"{flips.get('LOST_CONSENSUS', 0)} | {flips.get('NEW_CONSENSUS', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Paired common population by tier",
            "",
            "| Tier | Common scored | Baseline accuracy | Candidate accuracy | Brier delta | 95% CI | Lost | New |",
            "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for group, values in summary["by_tier_common"].items():
        ci = values["brier_delta_ci95"]
        ci_text = f"[{ci[0]:+.6f}, {ci[1]:+.6f}]" if ci else "n/a"
        flips = values["gate_flips"]
        lines.append(
            f"| {group} | {values['common_scored']} | "
            f"{_pct(values['baseline_accuracy'])} | {_pct(values['candidate_accuracy'])} | "
            f"{_num(values['brier_delta_mean'])} | {ci_text} | "
            f"{flips.get('LOST_CONSENSUS', 0)} | {flips.get('NEW_CONSENSUS', 0)} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract-dir", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    args = parser.parse_args()

    rows = load_jsonl(args.extract_dir / "ensemble_rows.jsonl")
    evaluated = evaluate_rows(rows)
    summary = summarize(evaluated)
    summary["experiment_id"] = EXPERIMENT_ID
    summary["generated_utc"] = datetime.now(timezone.utc).isoformat()
    summary["temperature_slope"] = TEMPERATURE_SLOPE
    summary["temperature_source"] = TEMPERATURE_SOURCE
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    (args.artifacts_dir / "ensemble_shadow.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    (args.artifacts_dir / "preregistration.json").write_text(
        json.dumps(PREREGISTRATION, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_tables(summary, args.artifacts_dir / "ensemble_tables.md")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
