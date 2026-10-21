"""Offline analysis for the Tennis model-error forensics cohort.

Reads the frozen JSONL produced by ``extract_live_picks.py`` and writes
deterministic aggregate metrics, breakdowns and case-study tables.  No database
connection, no model execution and no production writes.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

BOOTSTRAP_SEED = 20261021
BOOTSTRAP_REPS = 2000
MIN_MANAGER_PROBABILITY = 0.60
HIGH_CONFIDENCE = 0.80


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("_manifest"):
                manifest = record
            else:
                rows.append(record)
    return rows, manifest


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def band(probability: float) -> str:
    if probability >= 0.90:
        return "90+"
    if probability >= 0.80:
        return "80-89"
    if probability >= 0.70:
        return "70-79"
    if probability >= 0.60:
        return "60-69"
    return "<60"


def scored_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("scored")]


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def normal_two_sided_p(observed: int, n: int, expected_p: float) -> float | None:
    if n == 0 or expected_p in (0.0, 1.0):
        return None
    variance = n * expected_p * (1 - expected_p)
    if variance <= 0:
        return None
    z = (observed - n * expected_p) / math.sqrt(variance)
    return math.erfc(abs(z) / math.sqrt(2))


def brier_score(rows: Iterable[dict[str, Any]]) -> float | None:
    rows = list(rows)
    if not rows:
        return None
    return sum((as_float(r["consensus_probability"]) - (1.0 if r["won"] else 0.0)) ** 2 for r in rows) / len(rows)


def log_loss(rows: Iterable[dict[str, Any]], epsilon: float = 1e-12) -> float | None:
    rows = list(rows)
    if not rows:
        return None
    total = 0.0
    for row in rows:
        p = min(1 - epsilon, max(epsilon, as_float(row["consensus_probability"])))
        y = 1.0 if row["won"] else 0.0
        total -= y * math.log(p) + (1 - y) * math.log(1 - p)
    return total / len(rows)


def ece(rows: Iterable[dict[str, Any]], start: float = 0.50, width: float = 0.05, bins: int = 10) -> float | None:
    rows = list(rows)
    if not rows:
        return None
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(bins)]
    for row in rows:
        p = as_float(row["consensus_probability"])
        index = min(bins - 1, max(0, int((p - start) / width)))
        buckets[index].append(row)
    total = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        mean_p = sum(as_float(r["consensus_probability"]) for r in bucket) / len(bucket)
        actual = sum(1 for r in bucket if r["won"]) / len(bucket)
        total += (len(bucket) / len(rows)) * abs(actual - mean_p)
    return total


def bootstrap_ci(
    rows: list[dict[str, Any]],
    metric,
    *,
    seed: int = BOOTSTRAP_SEED,
    reps: int = BOOTSTRAP_REPS,
) -> tuple[float, float] | None:
    if not rows:
        return None
    rng = random.Random(seed)
    values: list[float] = []
    n = len(rows)
    for _ in range(reps):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        value = metric(sample)
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    return (values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))])


def summarize(rows: list[dict[str, Any]], *, bootstrap: bool = True) -> dict[str, Any]:
    scored = scored_rows(rows)
    n = len(scored)
    wins = sum(1 for r in scored if r["won"])
    mean_p = sum(as_float(r["consensus_probability"]) for r in scored) / n if n else None
    actual = wins / n if n else None
    out: dict[str, Any] = {
        "eligible": len(rows),
        "scored": n,
        "wins": wins,
        "losses": n - wins,
        "void_or_cancelled": sum(1 for r in rows if r.get("void")),
        "pending": sum(1 for r in rows if r.get("outcome_status") is None),
        "mean_probability": mean_p,
        "actual_win_rate": actual,
        "calibration_gap": (actual - mean_p) if actual is not None and mean_p is not None else None,
        "accuracy_ci95": wilson_interval(wins, n) if n else None,
        "calibration_p_value": normal_two_sided_p(wins, n, mean_p) if n and mean_p else None,
        "brier": brier_score(scored),
        "log_loss": log_loss(scored),
        "ece_050_100": ece(scored),
        "ece_060_100": ece(scored, start=0.60, width=0.04, bins=10),
    }
    if bootstrap and n:
        out["brier_ci95"] = bootstrap_ci(scored, brier_score)
        out["log_loss_ci95"] = bootstrap_ci(scored, log_loss)
        out["ece_ci95"] = bootstrap_ci(scored, ece)
    return out


def group_summary(
    rows: list[dict[str, Any]], key_fn
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row))].append(row)
    result = []
    for key in sorted(groups):
        summary = summarize(groups[key], bootstrap=False)
        summary["group"] = key
        result.append(summary)
    return result


def history_bucket(count: int | None) -> str:
    if count is None:
        return "unknown"
    if count < 5:
        return "<5"
    if count < 10:
        return "5-9"
    if count < 20:
        return "10-19"
    if count < 50:
        return "20-49"
    return "50+"


def inactivity_bucket(days: int | None) -> str:
    if days is None:
        return "unknown"
    if days <= 7:
        return "0-7"
    if days <= 30:
        return "8-30"
    if days <= 90:
        return "31-90"
    if days <= 180:
        return "91-180"
    return ">180"


def spread_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.02:
        return "<0.02"
    if value < 0.04:
        return "0.02-0.04"
    if value < 0.06:
        return "0.04-0.06"
    return ">=0.06"


def market_edge_bucket(value: float | None) -> str:
    if value is None:
        return "no_pre_pick_sharp"
    if value < 0:
        return "model_below_sharp"
    if value < 0.05:
        return "0-5pp"
    if value < 0.10:
        return "5-10pp"
    if value < 0.20:
        return "10-20pp"
    return "20pp+"


def edge_bucket(value: float | None) -> str:
    # decision edge_pct is stored as a fraction (0.1158 = 11.58 percentage points).
    if value is None:
        return "unknown"
    if value < 0:
        return "negative"
    if value < 0.02:
        return "0-2pp"
    if value < 0.05:
        return "2-5pp"
    if value < 0.10:
        return "5-10pp"
    return "10pp+"


def market_agreement(row: dict[str, Any]) -> str:
    market = row.get("market") or {}
    sharp_fav = as_int(market.get("sharp_favourite_side"))
    side = as_int(row.get("selected_side"))
    if sharp_fav is None:
        return "no_pre_pick_sharp"
    return "model_picked_market_favourite" if sharp_fav == side else "model_picked_market_underdog"


def cause_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    history = (row.get("history") or {}).get("picked") or {}
    opponent = (row.get("history") or {}).get("opponent") or {}
    market = row.get("market") or {}
    dq = row.get("data_quality") or {}
    agents = row.get("agents") or {}

    if int(row.get("abstaining_agent_count") or 0) >= 1:
        flags.append("agent3_abstained")
    if (agents.get("AGENT_3_INTRANSITIVITY") or {}).get("status") == "ABSTAINED":
        flags.append("agent3_abstained")
    if as_int(history.get("prior_matches")) is not None and int(history["prior_matches"]) < 10:
        flags.append("picked_thin_history")
    if as_int(history.get("prior_stat_matches")) is not None and int(history["prior_stat_matches"]) < 5:
        flags.append("picked_thin_stat_history")
    if as_int(history.get("days_since_last_match")) is not None and int(history["days_since_last_match"]) > 60:
        flags.append("picked_long_inactivity")
    if as_int(history.get("prior_surface_matches")) is not None and int(history["prior_surface_matches"]) < 3:
        flags.append("picked_thin_surface_history")
    if as_int(opponent.get("prior_matches")) is not None and int(opponent["prior_matches"]) == 0:
        flags.append("opponent_no_visible_history")
    elif as_int(opponent.get("prior_matches")) is not None and int(opponent["prior_matches"]) < 5:
        flags.append("opponent_thin_history")
    if as_int(opponent.get("prior_10_wins")) is not None and int(opponent["prior_10_wins"]) >= 7:
        flags.append("opponent_strong_recent_form")
    if (
        as_float(opponent.get("prior_surface_win_rate")) is not None
        and float(opponent["prior_surface_win_rate"]) >= 0.70
        and int(opponent.get("prior_surface_matches") or 0) >= 5
    ):
        flags.append("opponent_strong_surface_form")
    if (
        as_float(history.get("prior_surface_win_rate")) is not None
        and float(history["prior_surface_win_rate"]) < 0.40
        and int(history.get("prior_surface_matches") or 0) >= 3
    ):
        flags.append("picked_weak_surface_record")
    if as_float(market.get("model_minus_sharp")) is not None and float(market["model_minus_sharp"]) >= 0.10:
        flags.append("model_much_above_sharp")
    if market_agreement(row) == "model_picked_market_underdog":
        flags.append("model_picked_market_underdog")
    if market_agreement(row) == "no_pre_pick_sharp":
        flags.append("no_pre_pick_sharp")
    if dq.get("surface_missing"):
        flags.append("surface_missing")
    if dq.get("name_flag_mismatch"):
        flags.append("qualification_identity_mismatch")
    if dq.get("result_states_disagree"):
        flags.append("result_observations_disagree")
    return sorted(set(flags))


def case_studies(rows: list[dict[str, Any]], *, minimum: float = HIGH_CONFIDENCE) -> list[dict[str, Any]]:
    cases = [
        row
        for row in scored_rows(rows)
        if as_float(row["consensus_probability"]) >= minimum and not row["won"]
    ]
    cases.sort(key=lambda r: (-float(r["consensus_probability"]), r["match_start_at"] or ""))
    out = []
    for row in cases:
        agents = row.get("agents") or {}
        out.append(
            {
                "match_id": row["match_id"],
                "match_date": row.get("match_start_at") or row.get("edition_start_date"),
                "tournament": row.get("tournament"),
                "surface": row.get("surface"),
                "tier": row.get("tier"),
                "best_of_sets": row.get("best_of_sets"),
                "round_code": row.get("round_code"),
                "qualifying_flag": row.get("qualifying_flag"),
                "picked_player": row.get("picked_player_name"),
                "opponent": row.get("opponent_player_name"),
                "predicted_probability": as_float(row["consensus_probability"]),
                "active_agent_count": row.get("active_agent_count"),
                "probability_spread": as_float(row.get("probability_spread")),
                "picked_at": row.get("picked_at"),
                "prediction_finalized_at": row.get("prediction_finalized_at"),
                "match_start_at": row.get("match_start_at"),
                "result": {
                    "outcome_status": row.get("outcome_status"),
                    "winner_side": row.get("winner_side"),
                    "scoreline": row.get("scoreline_text"),
                    "actual_winner": row.get("opponent_player_name"),
                    "result_observed_at": row.get("result_as_of"),
                    "result_source_system": row.get("result_source_system"),
                    "observation_count": (row.get("data_quality") or {}).get("result_observation_count"),
                    "observations_disagree": (row.get("data_quality") or {}).get("result_states_disagree"),
                },
                "agents": {
                    code: {
                        "status": values.get("status"),
                        "selection_side": values.get("selection_side"),
                        "win_probability": values.get("win_probability"),
                        "abstain_reason_code": values.get("abstain_reason_code"),
                        "model_version": values.get("model_version"),
                    }
                    for code, values in agents.items()
                },
                "history": row.get("history"),
                "market": row.get("market"),
                "data_quality": row.get("data_quality"),
                "cause_flags": cause_flags(row),
            }
        )
    return out


def write_markdown_table(handle, headers: list[str], rows: list[list[Any]]) -> None:
    handle.write("| " + " | ".join(headers) + " |\n")
    handle.write("| " + " | ".join("---" for _ in headers) + " |\n")
    for row in rows:
        handle.write("| " + " | ".join("" if value is None else str(value) for value in row) + " |\n")


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    if isinstance(value, tuple):
        return "(" + ", ".join(fmt(v, digits) for v in value) + ")"
    return str(value)


def build_report(
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    manager = [r for r in rows if r["population"] == "manager_consensus"]
    manager_eligible = [r for r in manager if as_float(r["consensus_probability"]) >= MIN_MANAGER_PROBABILITY]
    tracking = [r for r in rows if r["population"] == "wide_spread_tracking"]
    value_bets = [r for r in rows if r["population"] == "value_bet"]
    vetoed = [r for r in rows if r["population"] == "vetoed"]

    band_table = group_summary(manager_eligible, lambda r: band(as_float(r["consensus_probability"])))
    # Preserve band order.
    order = {"60-69": 0, "70-79": 1, "80-89": 2, "90+": 3}
    band_table.sort(key=lambda r: order.get(r["group"], 99))

    breakdowns = {
        "surface": group_summary(manager_eligible, lambda r: r.get("surface") or "MISSING"),
        "tier": group_summary(manager_eligible, lambda r: r.get("tier") or "MISSING"),
        "tournament": group_summary(manager_eligible, lambda r: r.get("tournament") or "UNKNOWN"),
        "history_depth": group_summary(
            manager_eligible,
            lambda r: history_bucket(as_int(((r.get("history") or {}).get("picked") or {}).get("prior_matches"))),
        ),
        "stat_history_depth": group_summary(
            manager_eligible,
            lambda r: history_bucket(as_int(((r.get("history") or {}).get("picked") or {}).get("prior_stat_matches"))),
        ),
        "inactivity": group_summary(
            manager_eligible,
            lambda r: inactivity_bucket(as_int(((r.get("history") or {}).get("picked") or {}).get("days_since_last_match"))),
        ),
        "agent_probability_spread": group_summary(
            manager_eligible, lambda r: spread_bucket(as_float(r.get("probability_spread")))
        ),
        "market_agreement": group_summary(manager_eligible, market_agreement),
        "model_minus_sharp": group_summary(
            manager_eligible, lambda r: market_edge_bucket(as_float((r.get("market") or {}).get("model_minus_sharp")))
        ),
        "value_bet": group_summary(
            manager_eligible, lambda r: "value_bet" if (r.get("value_bet") or {}).get("is_value_bet") else "model_only"
        ),
        "qualification_identity": group_summary(
            manager_eligible,
            lambda r: "name_flag_mismatch"
            if (r.get("data_quality") or {}).get("name_flag_mismatch")
            else "coherent",
        ),
        "result_quality": group_summary(
            manager_eligible,
            lambda r: "disagree"
            if (r.get("data_quality") or {}).get("result_states_disagree")
            else "agree_or_single",
        ),
    }

    high = [r for r in manager_eligible if as_float(r["consensus_probability"]) >= HIGH_CONFIDENCE]
    high_summary = summarize(high)
    high_cases = case_studies(manager_eligible)
    value_bets_published = [r for r in value_bets if r.get("published_at")]
    value_bets_unpublished = [r for r in value_bets if not r.get("published_at")]
    high_case_flags: Counter[str] = Counter()
    for case in high_cases:
        high_case_flags.update(case["cause_flags"])
    population_flags: Counter[str] = Counter()
    for row in manager_eligible:
        population_flags.update(cause_flags(row))

    analysis = {
        "dataset": manifest,
        "definitions": {
            "manager_probability_floor": MIN_MANAGER_PROBABILITY,
            "high_confidence_floor": HIGH_CONFIDENCE,
            "primary_population": "first production Manager CONSENSUS per match, probability >= 0.60",
            "scoring": "COMPLETED results with winner_side 1/2 only; RETIRED/WALKOVER/CANCELLED excluded",
            "ece": "10 bins from 0.50 to 1.00 (0.05 width); 0.60-1.00 sensitivity also reported",
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_reps": BOOTSTRAP_REPS,
        },
        "population_counts": {
            "manager_rows_all_probabilities": len(manager),
            "manager_eligible_ge60": len(manager_eligible),
            "manager_scored_ge60": len(scored_rows(manager_eligible)),
            "manager_high_confidence_ge80": len(high),
            "wide_spread_tracking": len(tracking),
            "value_bets": len(value_bets),
            "value_bets_published": len(value_bets_published),
            "value_bets_pre_publication_boundary": len(value_bets_unpublished),
            "vetoed": len(vetoed),
        },
        "manager_all_bands": summarize(manager),
        "manager_eligible": summarize(manager_eligible),
        "manager_bands": band_table,
        "high_confidence": high_summary,
        "breakdowns": breakdowns,
        "case_studies": high_cases,
        "high_confidence_loss_cause_flags": dict(high_case_flags.most_common()),
        "manager_population_cause_flags": dict(population_flags.most_common()),
        "tracking_summary": summarize(tracking),
        "tracking_bands": group_summary(tracking, lambda r: band(as_float(r["consensus_probability"]))),
        "value_bets_summary": summarize(value_bets),
        "value_bets_bands": group_summary(value_bets, lambda r: band(as_float(r["consensus_probability"]))),
        "value_bets_edge": group_summary(value_bets, lambda r: edge_bucket(as_float(r.get("edge_pct")))),
        "value_bets_published_summary": summarize(value_bets_published),
        "value_bets_unpublished_summary": summarize(value_bets_unpublished),
        "vetoed_summary": summarize(vetoed),
        "leakage_checks": {
            "manager_rows": len(manager),
            "result_observed_at_or_before_pick": sum(
                1
                for r in manager
                if r.get("result_as_of") and r.get("picked_at") and r["result_as_of"] <= r["picked_at"]
            ),
            "picked_at_or_after_match_start": sum(
                1
                for r in manager
                if r.get("match_start_at") and r.get("picked_at") and r["picked_at"] >= r["match_start_at"]
            ),
            "finalized_at_or_after_match_start": sum(
                1
                for r in manager
                if r.get("match_start_at")
                and r.get("prediction_finalized_at")
                and r["prediction_finalized_at"] >= r["match_start_at"]
            ),
            "missing_match_start": sum(1 for r in manager if not r.get("match_start_at")),
            "result_states_disagree": sum(
                1 for r in manager if (r.get("data_quality") or {}).get("result_states_disagree")
            ),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, sort_keys=True), encoding="utf-8"
    )

    # Human-readable tables.
    with (output_dir / "tables.md").open("w", encoding="utf-8") as handle:
        handle.write("# Tennis model-error forensics - generated tables\n\n")
        handle.write(f"Dataset SHA-256: `{manifest.get('row_sha256')}`\n\n")
        handle.write("## Manager Model Picks (unique first evaluation per match, >= 60%)\n\n")
        rows_out = []
        for item in band_table:
            rows_out.append(
                [
                    item["group"],
                    item["eligible"],
                    item["scored"],
                    item["wins"],
                    item["losses"],
                    fmt(item["mean_probability"]),
                    fmt(item["actual_win_rate"]),
                    fmt(item["calibration_gap"]),
                    fmt(item["accuracy_ci95"]),
                    fmt(item["brier"]),
                    fmt(item["log_loss"]),
                    fmt(item["ece_050_100"]),
                    fmt(item["calibration_p_value"]),
                ]
            )
        write_markdown_table(
            handle,
            [
                "Band",
                "Eligible",
                "Scored",
                "Wins",
                "Losses",
                "Mean p",
                "Actual",
                "Gap",
                "Accuracy 95% CI",
                "Brier",
                "Log loss",
                "ECE",
                "p vs mean p",
            ],
            rows_out,
        )
        overall = analysis["manager_eligible"]
        high_item = analysis["high_confidence"]
        handle.write("\n**Overall**: ")
        handle.write(
            f"eligible={overall['eligible']}, scored={overall['scored']}, "
            f"actual={fmt(overall['actual_win_rate'])}, mean p={fmt(overall['mean_probability'])}, "
            f"gap={fmt(overall['calibration_gap'])}, Brier={fmt(overall['brier'])}, "
            f"log loss={fmt(overall['log_loss'])}, ECE={fmt(overall['ece_050_100'])}.\n"
        )
        handle.write(
            f"\n**High confidence (>= 80%)**: eligible={high_item['eligible']}, "
            f"scored={high_item['scored']}, wins={high_item['wins']}, "
            f"actual={fmt(high_item['actual_win_rate'])}, mean p={fmt(high_item['mean_probability'])}, "
            f"gap={fmt(high_item['calibration_gap'])}, accuracy 95% CI={fmt(high_item['accuracy_ci95'])}, "
            f"Brier={fmt(high_item['brier'])}, log loss={fmt(high_item['log_loss'])}, "
            f"ECE={fmt(high_item['ece_050_100'])}, p={fmt(high_item['calibration_p_value'])}.\n"
        )

        handle.write("\n## Population separation\n\n")
        population_rows = [
            ["Manager Model Picks (unique first evaluation, >=60%)", analysis["population_counts"]["manager_eligible_ge60"], "primary forward population"],
            ["Manager unique first evaluations (all probabilities)", analysis["population_counts"]["manager_rows_all_probabilities"], "includes <60% predictions; 4,825 raw hourly rows are not independent"],
            ["A1/A2 wide-spread tracking", analysis["population_counts"]["wide_spread_tracking"], "research only; never a Value Bet"],
            ["Actual Value Bets", analysis["population_counts"]["value_bets"], "first BET per match; overlaps Manager picks"],
            ["  of which published before match", analysis["population_counts"]["value_bets_published"], "published_at present"],
            ["  of which pre-publication-boundary ledger", analysis["population_counts"]["value_bets_pre_publication_boundary"], "not published at the time"],
            ["Market Veto-blocked", analysis["population_counts"]["vetoed"], "separate from Value Bets"],
            ["Historical Agent 2 replay (PR #239)", 36512, "separate offline replay; 40,040 eligible matches"],
        ]
        write_markdown_table(handle, ["Population", "Rows", "Note"], population_rows)
        leakage = analysis["leakage_checks"]
        handle.write(
            f"\n**Leakage checks**: result observed at/before pick={leakage['result_observed_at_or_before_pick']}; "
            f"picked at/after start={leakage['picked_at_or_after_match_start']}; "
            f"finalized at/after start={leakage['finalized_at_or_after_match_start']}; "
            f"missing start={leakage['missing_match_start']}; "
            f"disagreeing result observations={leakage['result_states_disagree']}.\n"
        )

        for name, table in breakdowns.items():
            handle.write(f"\n## Breakdown: {name}\n\n")
            rows_out = [
                [
                    item["group"],
                    item["eligible"],
                    item["scored"],
                    item["wins"],
                    item["mean_probability"],
                    item["actual_win_rate"],
                    item["calibration_gap"],
                    item["brier"],
                    item["ece_050_100"],
                    fmt(item["calibration_p_value"]),
                ]
                for item in table
            ]
            write_markdown_table(
                handle,
                ["Group", "Eligible", "Scored", "Wins", "Mean p", "Actual", "Gap", "Brier", "ECE", "p"],
                rows_out,
            )

        handle.write("\n## A1/A2 wide-spread tracking (research only, >= 60%)\n\n")
        rows_out = [
            [
                item["group"],
                item["eligible"],
                item["scored"],
                item["wins"],
                item["mean_probability"],
                item["actual_win_rate"],
                item["calibration_gap"],
                item["brier"],
                item["ece_050_100"],
            ]
            for item in analysis["tracking_bands"]
        ]
        write_markdown_table(
            handle,
            ["Band", "Eligible", "Scored", "Wins", "Mean p", "Actual", "Gap", "Brier", "ECE"],
            rows_out,
        )

        handle.write("\n## Actual Value Bets (first BET per match, all probabilities)\n\n")
        rows_out = [
            [
                item["group"],
                item["eligible"],
                item["scored"],
                item["wins"],
                item["mean_probability"],
                item["actual_win_rate"],
                item["calibration_gap"],
                item["brier"],
                item["ece_050_100"],
            ]
            for item in analysis["value_bets_bands"]
        ]
        write_markdown_table(
            handle,
            ["Band", "Eligible", "Scored", "Wins", "Mean p", "Actual", "Gap", "Brier", "ECE"],
            rows_out,
        )
        handle.write("\n## Actual Value Bets by claimed edge\n\n")
        rows_out = [
            [
                item["group"],
                item["eligible"],
                item["scored"],
                item["wins"],
                item["mean_probability"],
                item["actual_win_rate"],
                item["calibration_gap"],
                item["brier"],
                fmt(item["calibration_p_value"]),
            ]
            for item in analysis["value_bets_edge"]
        ]
        write_markdown_table(
            handle,
            ["Claimed edge", "Eligible", "Scored", "Wins", "Mean p", "Actual", "Gap", "Brier", "p"],
            rows_out,
        )
        for label, key in (
            ("Value Bets", "value_bets_summary"),
            ("Published Value Bets", "value_bets_published_summary"),
            ("Pre-publication-boundary Value Bets", "value_bets_unpublished_summary"),
            ("Veto-blocked", "vetoed_summary"),
        ):
            item = analysis[key]
            handle.write(
                f"\n**{label}**: eligible={item['eligible']}, scored={item['scored']}, "
                f"wins={item['wins']}, actual={fmt(item['actual_win_rate'])}, "
                f"mean p={fmt(item['mean_probability'])}, gap={fmt(item['calibration_gap'])}, "
                f"p={fmt(item['calibration_p_value'])}.\n"
            )

    # Case-study evidence table.
    with (output_dir / "case_studies.md").open("w", encoding="utf-8") as handle:
        handle.write("# High-confidence incorrect Manager Model Picks\n\n")
        handle.write(
            "All rows are unique first production Manager CONSENSUS evaluations per match, "
            "probability >= 80%, settled COMPLETED losses.\n\n"
        )
        handle.write("## Evidence-flag frequency\n\n")
        write_markdown_table(
            handle,
            ["Flag", "High-confidence losses", "All Manager picks >=60%"],
            [
                [
                    flag,
                    count,
                    analysis["manager_population_cause_flags"].get(flag, 0),
                ]
                for flag, count in analysis["high_confidence_loss_cause_flags"].items()
            ],
        )
        handle.write("\nFlags are descriptive, not causal. A flag is only a cause when the "
                     "case narrative supplies evidence beyond the loss itself.\n\n")
        for index, case in enumerate(high_cases, start=1):
            handle.write(f"## {index}. Match {case['match_id']} - {case['picked_player']} vs {case['opponent']}\n\n")
            handle.write(f"- Date/start: {case['match_start_at']}\n")
            handle.write(f"- Tournament: {case['tournament']} ({case['tier']}, {case['surface']}, best of {case['best_of_sets']}, {case['round_code']})\n")
            handle.write(f"- Picked: {case['picked_player']} at {fmt(case['predicted_probability'])}\n")
            handle.write(f"- Actual winner: {case['result']['actual_winner']} ({case['result']['scoreline']})\n")
            handle.write(f"- Result source: {case['result']['result_source_system']}; observations={case['result']['observation_count']}; disagree={case['result']['observations_disagree']}\n")
            handle.write(f"- Picked at: {case['picked_at']}; finalized: {case['prediction_finalized_at']}\n")
            handle.write(f"- Active agents: {case['active_agent_count']}; probability spread: {fmt(case['probability_spread'])}\n")
            for code, agent in case["agents"].items():
                handle.write(
                    f"  - {code}: {agent['status']} side={agent['selection_side']} "
                    f"p={fmt(agent['win_probability'])} abstain={agent['abstain_reason_code']}\n"
                )
            handle.write(f"- Picked history: {json.dumps(case['history']['picked'], sort_keys=True)}\n")
            handle.write(f"- Opponent history: {json.dumps(case['history']['opponent'], sort_keys=True)}\n")
            handle.write(f"- Market: {json.dumps(case['market'], sort_keys=True)}\n")
            handle.write(f"- Data quality: {json.dumps(case['data_quality'], sort_keys=True)}\n")
            handle.write(f"- Evidence flags: {', '.join(case['cause_flags']) or 'none'}\n\n")

    return analysis


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    rows, manifest = load_jsonl(args.dataset)
    analysis = build_report(rows, manifest, args.output_dir)
    print(json.dumps(analysis["population_counts"], indent=2))
    print(json.dumps(analysis["manager_eligible"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
