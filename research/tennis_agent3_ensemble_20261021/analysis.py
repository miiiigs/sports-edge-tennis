"""Offline Agent 3 root-cause and incremental-contribution analysis.

Inputs are the read-only JSONL extracts produced by ``extract.py``. The analysis
is deterministic and never writes to the database.
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
from typing import Any, Iterable, Mapping

from research.tennis_agent3_ensemble_20261021.policy import (
    AGENT_1,
    AGENT_2,
    AGENT_3,
    AgentVote,
    PolicyConfig,
    policy_from_row,
    simulate_manager,
)

EXPERIMENT_ID = "tennis_agent3_ensemble_20261021"
SURFACES = ("HARD", "CLAY", "GRASS", "CARPET", "<missing>")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _scored(row: Mapping[str, Any]) -> bool:
    return row.get("outcome_status") == "COMPLETED" and row.get("winner_side") in (1, 2)


def _won(row: Mapping[str, Any], side: int | None) -> bool | None:
    if not _scored(row) or side not in (1, 2):
        return None
    return int(row["winner_side"]) == int(side)


def _metrics(probabilities: Iterable[float], outcomes: Iterable[bool]) -> dict[str, Any]:
    pairs = [(float(p), bool(y)) for p, y in zip(probabilities, outcomes)]
    if not pairs:
        return {"n": 0, "accuracy": None, "brier": None, "log_loss": None, "ece": None}
    eps = 1e-12
    # These helpers receive selected-side probabilities (>= 0.5), so the
    # selected side is always the one with probability >= 0.5 and accuracy is
    # simply the observed outcome rate.
    correct = sum(1 for _, outcome in pairs if outcome)
    brier = sum((probability - float(outcome)) ** 2 for probability, outcome in pairs) / len(pairs)
    log_loss = (
        -sum(
            math.log(max(probability if outcome else 1.0 - probability, eps))
            for probability, outcome in pairs
        )
        / len(pairs)
    )
    ece = _ece(pairs)
    return {
        "n": len(pairs),
        "accuracy": correct / len(pairs),
        "brier": brier,
        "log_loss": log_loss,
        "ece": ece,
    }


def _ece(pairs: list[tuple[float, bool]], bins: int = 10) -> float:
    total = len(pairs)
    error = 0.0
    for index in range(bins):
        lower = 0.5 + index * (0.5 / bins)
        upper = 0.5 + (index + 1) * (0.5 / bins)
        members = [
            (probability, outcome)
            for probability, outcome in pairs
            if lower <= probability < upper or (index == bins - 1 and probability == upper)
        ]
        if not members:
            continue
        mean_probability = sum(probability for probability, _ in members) / len(members)
        observed = sum(1 for _, outcome in members if outcome) / len(members)
        error += len(members) / total * abs(mean_probability - observed)
    return error


def _accuracy(wins: int, scored: int) -> float | None:
    return wins / scored if scored else None


def _wilson(wins: int, n: int, z: float = 1.959963984540054) -> list[float] | None:
    if n == 0:
        return None
    p = wins / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0.0, centre - half), min(1.0, centre + half)]


def _paired_bootstrap(
    deltas: list[float], *, seed: int = 20261021, reps: int = 2000
) -> dict[str, Any]:
    import random

    if not deltas:
        return {"n": 0, "mean": None, "ci95": None}
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        sample = [deltas[rng.randrange(len(deltas))] for _ in deltas]
        means.append(sum(sample) / len(sample))
    means.sort()
    return {
        "n": len(deltas),
        "mean": sum(deltas) / len(deltas),
        "ci95": [means[int(0.025 * reps)], means[int(0.975 * reps) - 1]],
    }


def _first_by_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first: dict[int, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (item["match_id"], item["as_of"], item["prediction_id"])):
        first.setdefault(int(row["match_id"]), row)
    return list(first.values())


def _surface(row: Mapping[str, Any]) -> str:
    return str(row.get("surface") or "<missing>")


def _tier(row: Mapping[str, Any]) -> str:
    return str(row.get("competition_tier") or "<missing>")


def _a3_evaluation_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = _first_by_match(rows)
    predicted = [row for row in first if row["a3_status"] == "PREDICTED"]
    return {
        "evaluation_rows": len(rows),
        "unique_matches": len(first),
        "unique_predicted_matches": len(predicted),
        "unique_coverage_rate": len(predicted) / len(first) if first else None,
        "first_as_of": min((row["as_of"] for row in rows), default=None),
        "last_as_of": max((row["as_of"] for row in rows), default=None),
        "evaluation_level": {
            f"{status}|{reason}": count
            for (status, reason), count in Counter(
                (
                    row["a3_status"],
                    row["a3_abstain_reason"] or "<null>",
                )
                for row in rows
            ).items()
        },
        "unique_first_status_reason": {
            f"{status}|{reason}": count
            for (status, reason), count in Counter(
                (row["a3_status"], row["a3_abstain_reason"] or "<null>") for row in first
            ).items()
        },
        "post_0017_vs_pre": dict(
            Counter(
                "post_0017" if row["a3_abstain_reason"] else "pre_or_missing_0017"
                for row in rows
                if row["a3_status"] == "ABSTAINED"
            )
        ),
        "by_surface_first_evaluation": _by_surface_first(first),
        "by_tier_first_evaluation": _by_dimension_first(first, "competition_tier"),
        "by_season_first_evaluation": _by_dimension_first(first, "season_year"),
        "top_tournaments": _top_tournaments(first, limit=15),
    }


def _by_dimension_first(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"predicted": 0, "abstained": 0})
    for row in rows:
        value = row.get(key)
        label = "<missing>" if value is None else str(value)
        grouped[label]["predicted" if row["a3_status"] == "PREDICTED" else "abstained"] += 1
    return dict(sorted(grouped.items()))


def _top_tournaments(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"predicted": 0, "abstained": 0})
    for row in rows:
        label = str(row.get("tournament_name") or "<missing>")
        grouped[label]["predicted" if row["a3_status"] == "PREDICTED" else "abstained"] += 1
    ranked = sorted(
        grouped.items(),
        key=lambda item: (item[1]["abstained"] + item[1]["predicted"], item[0]),
        reverse=True,
    )[:limit]
    return [
        {
            "tournament": name,
            "predicted": counts["predicted"],
            "abstained": counts["abstained"],
            "coverage": counts["predicted"] / (counts["predicted"] + counts["abstained"]),
        }
        for name, counts in ranked
    ]


def _by_surface_first(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"predicted": 0, "abstained": 0})
    for row in rows:
        key = _surface(row)
        grouped[key]["predicted" if row["a3_status"] == "PREDICTED" else "abstained"] += 1
    return dict(sorted(grouped.items()))


def _diagnostics_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    status_gate = Counter(
        (row["final_status"], row["gate_failed"] or "<none>") for row in rows
    )
    identity = Counter(row["identity_status"] for row in rows)
    support_path = Counter(row["support_path"] or "<null>" for row in rows)
    direct_raw = Counter(int(row["direct_raw_support"] or 0) for row in rows)
    eligible_shared = Counter(int(row["eligible_shared_opponents"] or 0) for row in rows)
    surface = Counter(_surface(row) for row in rows)

    direct_pass = 0
    direct_plus_network = 0
    direct_only = 0
    network_only_pass = 0
    network_normal = 0
    cohorts = Counter()
    network_only_not_predicted = 0
    for row in rows:
        raw = int(row["direct_raw_support"] or 0)
        effective = Decimal(str(row["direct_effective_support"] or "0"))
        direct_gate = raw >= 2 and effective >= Decimal("2.5")
        eligible = int(row["eligible_shared_opponents"] or 0)
        q_count = Decimal(str(row["q_count"] or "0"))
        net_only_gate = eligible >= 8 and q_count >= Decimal("16")
        if direct_gate:
            direct_pass += 1
            if eligible >= 5:
                network_normal += 1
            if row["support_path"] == "DIRECT_PLUS_NETWORK":
                direct_plus_network += 1
            elif row["support_path"] == "DIRECT_ONLY":
                direct_only += 1
        if net_only_gate:
            network_only_pass += 1
            if not direct_gate and row["final_status"] != "PREDICTED":
                network_only_not_predicted += 1
        if row["final_status"] == "ABSTAINED" and row["gate_failed"] == "INSUFFICIENT_MATCHUP_SUPPORT":
            if raw == 0:
                cohorts["direct_raw=0"] += 1
            elif raw == 1:
                cohorts["direct_raw=1"] += 1
            elif effective < Decimal("2.5"):
                cohorts["direct_raw>=2_effective<2.5"] += 1
            elif eligible < 5:
                cohorts["direct_pass_network_eligible<5"] += 1
            else:
                cohorts["other"] += 1

    return {
        "n": len(rows),
        "unique_matches": len({int(row["match_id"]) for row in rows}),
        "unique_predicted_matches": len(
            {int(row["match_id"]) for row in rows if row["final_status"] == "PREDICTED"}
        ),
        "status_gate": {f"{status}|{gate}": count for (status, gate), count in status_gate.items()},
        "identity": dict(identity),
        "support_path": dict(support_path),
        "direct_raw_histogram": dict(sorted(direct_raw.items())),
        "eligible_shared_opponents_histogram": dict(sorted(eligible_shared.items())),
        "surface": dict(surface),
        "gate_decomposition": {
            "direct_raw_zero": direct_raw.get(0, 0),
            "direct_raw_one": direct_raw.get(1, 0),
            "direct_raw_ge2_effective_lt_2_5": sum(
                1
                for row in rows
                if int(row["direct_raw_support"] or 0) >= 2
                and Decimal(str(row["direct_effective_support"] or "0")) < Decimal("2.5")
            ),
            "direct_gate_pass": direct_pass,
            "network_normal_support": network_normal,
            "direct_plus_network": direct_plus_network,
            "direct_only": direct_only,
            "network_eligible_zero": eligible_shared.get(0, 0),
            "network_eligible_1_4": sum(
                count for value, count in eligible_shared.items() if 1 <= value <= 4
            ),
            "network_eligible_5_7": sum(
                count for value, count in eligible_shared.items() if 5 <= value <= 7
            ),
            "network_eligible_ge8": sum(
                count for value, count in eligible_shared.items() if value >= 8
            ),
            "network_only_gate_pass": network_only_pass,
            "network_only_gate_pass_but_not_predicted": network_only_not_predicted,
        },
        "insufficient_cohorts": dict(cohorts),
        "direct_three_plus_required": {
            "explanation": (
                "The raw gate requires >=2 prior H2H matches while the effective gate "
                "requires >=2.5 decay-weighted observations. Two observations can never "
                "reach 2.5 because each temporal weight is <=1, so the raw=2 boundary is "
                "not actionable: a pair needs at least three retained observations."
            ),
            "direct_raw_eq2": direct_raw.get(2, 0),
            "direct_raw_ge3": sum(count for value, count in direct_raw.items() if value >= 3),
        },
    }


def _a3_contribution(
    first_evaluations: list[dict[str, Any]],
    ensemble_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    predicted = [row for row in first_evaluations if row["a3_status"] == "PREDICTED"]
    scored = [row for row in predicted if _scored(row)]
    wins = sum(1 for row in scored if _won(row, int(row["a3_side"])))
    side_counts = Counter(int(row["a3_side"]) for row in predicted if row["a3_side"] in (1, 2))

    agent_pairs = []
    for row in predicted:
        if row["a1_status"] == "PREDICTED" and row["a1_probability"] is not None:
            agent_pairs.append(
                (
                    "A1",
                    float(row["a1_probability"]),
                    _won(row, int(row["a1_side"])),
                )
            )
        if row["a2_status"] == "PREDICTED" and row["a2_probability"] is not None:
            agent_pairs.append(
                (
                    "A2",
                    float(row["a2_probability"]),
                    _won(row, int(row["a2_side"])),
                )
            )
    agent_metrics = {}
    for code in ("A1", "A2", "A3"):
        selected = [(probability, outcome) for name, probability, outcome in agent_pairs if name == code]
        if code == "A3":
            selected = [(float(row["a3_probability"]), _won(row, int(row["a3_side"]))) for row in scored]
        selected = [(p, y) for p, y in selected if y is not None]
        agent_metrics[code] = _metrics([p for p, _ in selected], [y for _, y in selected])

    three_active = [
        row
        for row in ensemble_rows
        if row["a1_status"] == "PREDICTED"
        and row["a2_status"] == "PREDICTED"
        and row["a3_status"] == "PREDICTED"
    ]
    three_reasons = Counter(row["manager_reason_code"] for row in three_active)
    three_spreads = [
        Decimal(str(row["manager_probability_spread"]))
        for row in three_active
        if row.get("manager_probability_spread") is not None
    ]
    a3_matches_a1 = sum(
        1 for row in three_active if int(row["a3_side"]) == int(row["a1_side"])
    )
    a3_matches_a2 = sum(
        1 for row in three_active if int(row["a3_side"]) == int(row["a2_side"])
    )

    agreement = Counter()
    for row in predicted:
        a3_side = row["a3_side"]
        a1_side = row["a1_side"] if row["a1_status"] == "PREDICTED" else None
        a2_side = row["a2_side"] if row["a2_status"] == "PREDICTED" else None
        if a1_side is not None and int(a1_side) == int(a3_side):
            agreement["a3_agrees_a1"] += 1
        if a2_side is not None and int(a2_side) == int(a3_side):
            agreement["a3_agrees_a2"] += 1
        if a1_side is not None and a2_side is not None and int(a1_side) == int(a2_side):
            agreement["a1_a2_agree"] += 1
            if int(a1_side) == int(a3_side):
                agreement["all_three_agree"] += 1
            else:
                agreement["a3_dissents"] += 1

    counterfactuals = {}
    for threshold in (Decimal("0.06"), Decimal("0.08")):
        picks = []
        for row in three_active:
            policy = policy_from_row(row)
            shadow = PolicyConfig(
                policy_version=policy.policy_version,
                allow_agent3_abstention=True,
                three_agent_max_probability_spread=policy.three_agent_max_probability_spread,
                agent3_abstention_max_probability_spread=threshold,
                min_active_agent_probability=policy.min_active_agent_probability,
            )
            votes = {
                AGENT_1: AgentVote(AGENT_1, "PREDICTED", int(row["a1_side"]), _decimal(row["a1_probability"])),
                AGENT_2: AgentVote(AGENT_2, "PREDICTED", int(row["a2_side"]), _decimal(row["a2_probability"])),
                AGENT_3: AgentVote(AGENT_3, "ABSTAINED", None, None),
            }
            decision = simulate_manager(votes, policy=shadow)
            if decision.manager_status == "CONSENSUS":
                outcome = _won(row, decision.selected_side)
                if outcome is not None:
                    picks.append(outcome)
        counterfactuals[str(threshold)] = {
            "scored_picks": len(picks),
            "wins": sum(1 for outcome in picks if outcome),
            "accuracy": _accuracy(sum(1 for outcome in picks if outcome), len(picks)),
        }

    dissent_rows = [
        row
        for row in three_active
        if int(row["a1_side"]) == int(row["a2_side"]) and int(row["a1_side"]) != int(row["a3_side"])
    ]
    dissent_scored = [row for row in dissent_rows if _scored(row)]
    dissent_a1a2_wins = sum(
        1 for row in dissent_scored if _won(row, int(row["a1_side"]))
    )

    return {
        "unique_predicted_matches": len(predicted),
        "scored_matches": len(scored),
        "wins": wins,
        "accuracy": _accuracy(wins, len(scored)),
        "accuracy_wilson": _wilson(wins, len(scored)),
        "brier": agent_metrics["A3"]["brier"],
        "log_loss": agent_metrics["A3"]["log_loss"],
        "side_distribution": {str(side): count for side, count in sorted(side_counts.items())},
        "agent_metrics_on_same_matches": agent_metrics,
        "agreement": dict(agreement),
        "three_active": {
            "manager_rows": len(three_active),
            "reason_codes": dict(three_reasons),
            "all_no_bet": all(row["manager_status"] == "NO_BET" for row in three_active),
            "spread_min": float(min(three_spreads)) if three_spreads else None,
            "spread_median": float(median(three_spreads)) if three_spreads else None,
            "spread_max": float(max(three_spreads)) if three_spreads else None,
            "rows_spread_le_0_08": sum(1 for value in three_spreads if value <= Decimal("0.08")),
            "a3_side_equals_a1": a3_matches_a1,
            "a3_side_equals_a1_rate": a3_matches_a1 / len(three_active) if three_active else None,
            "a3_side_equals_a2": a3_matches_a2,
            "a3_dissent_rows": len(dissent_rows),
            "a3_dissent_scored": len(dissent_scored),
            "a3_dissent_a1a2_still_won": dissent_a1a2_wins,
            "counterfactual_two_agent": counterfactuals,
        },
    }


def _ensemble_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = Counter(row["manager_reason_code"] for row in rows)
    status = Counter(row["manager_status"] for row in rows)
    active = Counter(int(row["active_agent_count"]) for row in rows)
    scored = [row for row in rows if _scored(row)]
    return {
        "matches": len(rows),
        "status": dict(status),
        "active_agent_count": dict(sorted(active.items())),
        "reason_codes": dict(reasons),
        "scored_matches": len(scored),
    }


def analyze(
    *,
    a3_evaluations: list[dict[str, Any]],
    a3_diagnostics: list[dict[str, Any]],
    ensemble_rows: list[dict[str, Any]],
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    first = _first_by_match(a3_evaluations)
    return {
        "experiment_id": EXPERIMENT_ID,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
        "coverage": _a3_evaluation_summary(a3_evaluations),
        "abstentions": {
            "diagnostics": _diagnostics_summary(a3_diagnostics),
        },
        "contribution": _a3_contribution(first, ensemble_rows),
        "manager_population": _ensemble_coverage(ensemble_rows),
    }


def write_tables(analysis: Mapping[str, Any], path: Path) -> None:
    coverage = analysis["coverage"]
    diag = analysis["abstentions"]["diagnostics"]
    contrib = analysis["contribution"]
    lines = [
        "# Agent 3 evidence tables",
        "",
        "## Coverage",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| A3 evaluation rows | {coverage['evaluation_rows']:,} |",
        f"| Unique matches evaluated | {coverage['unique_matches']:,} |",
        f"| Unique matches predicted | {coverage['unique_predicted_matches']:,} |",
        f"| Unique-match coverage | {coverage['unique_coverage_rate']:.4%} |",
        "",
        "## Diagnostics gate decomposition",
        "",
        "| Gate/cohort | Evaluations |",
        "| --- | ---: |",
    ]
    for key, value in diag["gate_decomposition"].items():
        lines.append(f"| {key} | {value:,} |")
    lines.extend(
        [
            "",
            "## Insufficient-support cohorts",
            "",
            "| Cohort | Evaluations |",
            "| --- | ---: |",
        ]
    )
    for key, value in diag["insufficient_cohorts"].items():
        lines.append(f"| {key} | {value:,} |")
    lines.extend(
        [
            "",
            "## Agent contribution",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Unique A3 predicted matches | {contrib['unique_predicted_matches']} |",
            f"| Scored A3 predicted matches | {contrib['scored_matches']} |",
            f"| A3 wins | {contrib['wins']} |",
            f"| A3 accuracy | {contrib['accuracy']:.4%} |" if contrib["accuracy"] is not None else "| A3 accuracy | n/a |",
            f"| A3 Brier | {contrib['brier']:.6f} |" if contrib["brier"] is not None else "| A3 Brier | n/a |",
            f"| A3 log loss | {contrib['log_loss']:.6f} |" if contrib["log_loss"] is not None else "| A3 log loss | n/a |",
            f"| A3 side = A1 side (3-active rows) | {contrib['three_active']['a3_side_equals_a1']}/{contrib['three_active']['manager_rows']} |",
            f"| 3-active Manager rows | {contrib['three_active']['manager_rows']} |",
            f"| 3-active rows passing 0.08 spread | {contrib['three_active']['rows_spread_le_0_08']} |",
            "",
            "## Two-agent counterfactual on 3-active rows",
            "",
            "| A3-abstention spread gate | Scored picks | Wins | Accuracy |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for threshold, values in contrib["three_active"]["counterfactual_two_agent"].items():
        accuracy = values["accuracy"]
        lines.append(
            f"| {threshold} | {values['scored_picks']} | {values['wins']} | "
            f"{accuracy:.4%} |" if accuracy is not None else f"| {threshold} | {values['scored_picks']} | {values['wins']} | n/a |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract-dir", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    args = parser.parse_args()

    manifest_path = args.extract_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    analysis = analyze(
        a3_evaluations=load_jsonl(args.extract_dir / "a3_evaluations.jsonl"),
        a3_diagnostics=load_jsonl(args.extract_dir / "a3_diagnostics.jsonl"),
        ensemble_rows=load_jsonl(args.extract_dir / "ensemble_rows.jsonl"),
        manifest=manifest,
    )
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    (args.artifacts_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    write_tables(analysis, args.artifacts_dir / "tables.md")
    print(json.dumps(analysis, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
