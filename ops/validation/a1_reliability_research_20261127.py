"""A1 reliability-intelligence research runner (2026-11-27).

Reads the committed sanitised A1 settled population.  Produces:

* a feature-availability and missingness audit;
* an actual authorized R0/R1 evaluation (fixed rules, no fitting);
* a synthetic-only R2/R3/R4 experiment;
* the per-fixture reliability scores and a manifest.

No model is trained on the historical corpus.  Commercial training rights are
not established, so R2/R3/R4 are synthetic only.  No production write, provider
call, Agent 3 change or Agent 1 threshold change occurs here.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sports_edge_tennis.validation import a1_marginal_research as marginal
from sports_edge_tennis.validation import a1_reliability_shadow as reliability

POPULATION = ROOT / "docs/research/evidence/a1_selective_population_20261126.csv"
OUT = ROOT / "docs/research/evidence"

AUDIT_JSON = OUT / "a1_reliability_feature_audit_20261127.json"
ACTUAL_JSON = OUT / "a1_reliability_actual_20261127.json"
SYNTHETIC_JSON = OUT / "a1_reliability_synthetic_20261127.json"
SCORES_CSV = OUT / "a1_reliability_scores_20261127.csv"
CURVE_CSV = OUT / "a1_reliability_risk_coverage_20261127.csv"
MANIFEST = OUT / "a1_reliability_manifest_20261127.json"
REPORT = ROOT / "docs/research/A1_RELIABILITY_INTELLIGENCE_20261127.md"
TESTS = ROOT / "tests/test_a1_reliability_shadow.py"

TARGET_COVERAGES = (0.40, 0.45, 0.50)
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 20261127


def sha256(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".csv", ".md", ".py", ".txt", ".jsonl"}:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_observations() -> list[dict[str, Any]]:
    with POPULATION.open(encoding="utf-8", newline="") as handle:
        raw = list(csv.DictReader(handle))
    observations = marginal.parse_population(raw)
    if len(observations) != 3223:
        raise SystemExit(f"expected 3,223 settled rows, got {len(observations)}")
    return observations


def strict_pre_match(observations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The reliability layer only consumes forecasts issued before the start."""
    return [
        dict(observation)
        for observation in observations
        if str(observation["as_of"]) < str(observation["match_start_at"])
    ]


def build_features(observation: Mapping[str, Any]) -> reliability.ReliabilityFeatures:
    fav = observation["favourite_side"]
    dog = observation["underdog_side"]
    fav_key, dog_key = f"p{fav}", f"p{dog}"
    effective = observation["effective_corrected"]
    surface = observation["surface_rating_corrected"]
    overall = observation["overall_corrected"]
    matches = observation["matches_corrected"]
    surface_matches = observation["surface_matches_corrected"]
    inactivity = observation["inactivity_corrected"]
    resolution = observation["resolution"]
    fav_res = resolution[fav_key]
    dog_res = resolution[dog_key]
    surface_missing = surface[fav_key] is None or surface[dog_key] is None
    divergence = (
        0.0
        if surface_missing
        else abs(float(overall[fav_key]) - float(surface[fav_key]))
    )
    surface_gap = (
        0.0
        if surface_missing
        else abs(float(surface[fav_key]) - float(surface[dog_key]))
    )
    effective_gap = abs(float(effective[fav_key]) - float(effective[dog_key]))
    recovered = max(
        0.0,
        float(matches[fav_key]) - float(observation["matches_original"][fav_key]),
    )
    identity_method = fav_res["method"]
    unresolved = identity_method in ("AMBIGUOUS", "NO_HISTORICAL_CANDIDATE") or dog_res["method"] in (
        "AMBIGUOUS",
        "NO_HISTORICAL_CANDIDATE",
    )
    return reliability.ReliabilityFeatures(
        prediction_id=int(observation["prediction_id"]),
        match_id=int(observation["match_id"]),
        prediction_as_of=str(observation["as_of"]),
        match_start_at=str(observation["match_start_at"]),
        feature_as_of=str(observation["as_of"]),
        a1_probability_p1=float(observation["p1_original"]),
        a1_selected_side=int(observation["side_original"]),
        effective_rating_gap_abs=effective_gap,
        surface_rating_gap_abs=surface_gap,
        surface_rating_missing=surface_missing,
        rating_divergence_favourite=divergence,
        favourite_overall_matches=int(matches[fav_key]),
        underdog_overall_matches=int(matches[dog_key]),
        min_overall_matches=int(min(matches["p1"], matches["p2"])),
        favourite_surface_matches=int(surface_matches[fav_key]),
        min_surface_matches=int(min(surface_matches["p1"], surface_matches["p2"])),
        inactivity_favourite_days=inactivity[fav_key],
        inactivity_underdog_days=inactivity[dog_key],
        identity_method_favourite=identity_method,
        identity_candidate_count_favourite=int(fav_res["candidate_count"]),
        identity_any_unresolved=unresolved,
        identity_any_bridged=bool(fav_res["bridged"] or dog_res["bridged"]),
        recovered_matches_favourite=int(recovered),
        forecast_probability_delta_abs=abs(
            float(observation["p1_corrected"]) - float(observation["p1_original"])
        ),
        forecast_side_changed=bool(observation["side_changed"]),
        surface_transition_state="UNKNOWN",
        tour=observation["tour"],
        surface=observation["surface"] or "UNKNOWN",
        round=observation["round_code"],
        qualifying=bool(observation["qualifying"]),
        a2_status="MISSING",
    )


def scored_row(observation: Mapping[str, Any], features: reliability.ReliabilityFeatures) -> dict[str, Any]:
    assessment = reliability.score_reliability(features)
    fav = observation["favourite_side"]
    player_id = observation["resolution"][f"p{fav}"]["model_id"]
    return {
        "prediction_id": observation["prediction_id"],
        "match_id": observation["match_id"],
        "date": observation["date"],
        "tour": observation["tour"],
        "surface": observation["surface"],
        "round": observation["round_code"],
        "confidence": observation["confidence_original"],
        "a1_probability_p1": observation["p1_original"],
        "a1_selected_side": observation["side_original"],
        "correct": bool(observation["correct_original"]),
        "risk_points": assessment.risk_points,
        "reliability_score": assessment.reliability_score,
        "reliability_tier": assessment.reliability_tier,
        "selection_score": assessment.selection_score,
        "missing_features": list(assessment.missing_features),
        "player_cluster": f"model:{player_id}" if player_id is not None else f"live:{observation['resolution'][f'p{fav}']['live_id']}",
        "tournament_proxy": "|".join(
            (
                str(observation["date"]),
                observation["tour"],
                observation["competition_tier"],
                observation["surface"] or "UNKNOWN",
            )
        ),
        "market_p1": observation["market_p1"],
        "market_eligible": bool(observation.get("market_eligible")),
    }


def feature_audit(
    observations: Sequence[Mapping[str, Any]],
    features: Sequence[reliability.ReliabilityFeatures],
    *,
    source_rows: int,
    excluded_pre_match: int,
) -> dict[str, Any]:
    a2_present = sum(1 for f in features if f.a2_status == "PRESENT")
    transition = Counter(f.surface_transition_state for f in features)
    inactivity_fav_missing = sum(1 for f in features if f.inactivity_favourite_days is None)
    inactivity_dog_missing = sum(1 for f in features if f.inactivity_underdog_days is None)
    surface_rating_missing = sum(1 for f in features if f.surface_rating_missing)
    identity_methods = Counter(f.identity_method_favourite for f in features)
    bands = Counter(marginal.experience_band(f.favourite_overall_matches) for f in features)
    tournament_context = {
        "surface_missing": sum(1 for f in features if f.surface == "UNKNOWN"),
        "round_unknown": sum(1 for f in features if f.round == "UNKNOWN"),
        "qualifying": sum(1 for f in features if f.qualifying),
    }
    availability = {
        "original_agent1_confidence": len(features),
        "overall_rating_separation": len(features),
        "surface_rating_separation": len(features) - surface_rating_missing,
        "overall_surface_rating_divergence": len(features) - surface_rating_missing,
        "historical_match_counts": len(features),
        "recent_match_counts_90d": 0,
        "surface_specific_experience": len(features),
        "identity_resolution_method": len(features),
        "identity_candidate_count": len(features),
        "historical_completeness_recovered_matches": len(features),
        "inactivity_favourite": len(features) - inactivity_fav_missing,
        "inactivity_underdog": len(features) - inactivity_dog_missing,
        "surface_transitions": 0,
        "forecast_stability_alternative_rating": len(features),
        "agent2_specialist_support": a2_present,
        "tournament_context": len(features),
    }
    return {
        "version": "A1_RELIABILITY_FEATURE_AUDIT_V1",
        "source_settled_rows": source_rows,
        "excluded_at_or_after_start": excluded_pre_match,
        "settled_rows": len(observations),
        "feature_contract": reliability.feature_contract(),
        "availability_counts": availability,
        "availability_share": {
            key: (value / len(features) if features else None)
            for key, value in availability.items()
        },
        "missingness": {
            "surface_rating_missing": surface_rating_missing,
            "inactivity_favourite_missing": inactivity_fav_missing,
            "inactivity_underdog_missing": inactivity_dog_missing,
            "a2_missing": len(features) - a2_present,
            "surface_transition_available_in_export": 0,
            "recent_match_count_available_in_export": 0,
        },
        "favourite_history_bands": dict(bands),
        "identity_methods": dict(identity_methods),
        "surface_transition_states_in_export": dict(transition),
        "tournament_context": tournament_context,
        "genuinely_pit_observable": [
            "original Agent 1 probability and selected side",
            "overall and surface effective ratings at issue",
            "overall, surface and recovered match counts at issue",
            "identity resolution method, candidate count and bridged flag at issue",
            "inactivity since the last visible rating-corpus match",
            "original vs identity-bridged probability delta and side change",
            "tour, surface, round and qualifying context",
        ],
        "not_available_in_the_frozen_export": [
            "recent match counts in a fixed 90-day window",
            "per-fixture surface transition state (available only in the local result-history extract)",
            "Agent 2 specialist support and disagreement",
            "decimal bookmaker prices",
        ],
        "prior_confirmed_findings": {
            "pooled_marginal": "59/101 (58.42%)",
            "marginal_errors_with_favourite_20_plus": "37/42",
            "favourite_0_4_accuracy": "33.3% (99 fixtures)",
            "favourite_no_observable_history_accuracy": "8.3% (36 fixtures)",
            "shrinkage_candidate": "NOT_JUSTIFIED",
            "individual_marginal_factors_surviving_multiplicity": 0,
        },
        "note": (
            "Availability is measured on the frozen 3,223 settled export. A2 and recent "
            "counts are absent from that export; they are not inferred."
        ),
    }


def actual_evaluation(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    results = {}
    for label, key in (("R0_original_confidence", "confidence"), ("R1_reliability_adjusted", "selection_score")):
        ranked = reliability.rank_rows(rows, key)
        points = {}
        for fraction in TARGET_COVERAGES:
            k = reliability.coverage_count(len(ranked), fraction)
            selected = ranked[:k]
            points[f"{fraction:.2f}"] = reliability.selection_metrics(rows, selected)
        results[label] = {
            "ranking": key,
            "points": points,
            "risk_coverage": {
                key_: value
                for key_, value in reliability.risk_coverage(rows, key).items()
                if key_ != "curve"
            },
        }
    baseline = reliability.rank_rows(rows, "confidence")
    candidate = reliability.rank_rows(rows, "selection_score")
    set_differences = {}
    for fraction in TARGET_COVERAGES:
        k = reliability.coverage_count(len(rows), fraction)
        set_differences[f"{fraction:.2f}"] = reliability.set_difference_metrics(
            rows, baseline[:k], candidate[:k]
        )
    cluster = reliability.paired_cluster_cis(
        rows,
        fractions=TARGET_COVERAGES,
        reps=BOOTSTRAP_REPS,
        seed=BOOTSTRAP_SEED,
    )
    error_labels = [0 if row["correct"] else 1 for row in rows]
    auroc_risk = reliability.auroc([row["risk_points"] for row in rows], error_labels)
    auroc_score = reliability.auroc([1.0 - row["reliability_score"] for row in rows], error_labels)
    tiers = reliability.tier_calibration(rows)
    walk_forward = chronological_walk_forward(rows)
    market = market_context(rows)
    return {
        "version": "A1_RELIABILITY_ACTUAL_V1",
        "n": len(rows),
        "target_coverages": list(TARGET_COVERAGES),
        "r0_r1": results,
        "set_differences": set_differences,
        "paired_cluster_cis": cluster,
        "error_detection_auroc": {
            "risk_points": auroc_risk,
            "one_minus_reliability_score": auroc_score,
        },
        "tier_calibration": tiers,
        "chronological_walk_forward": walk_forward,
        "market_context": market,
        "classification": "AUTHORIZED_DEVELOPMENT_REUSED_NOT_INDEPENDENT",
        "note": (
            "R0/R1 are fixed, pre-registered rules evaluated on the previously examined "
            "3,223 settled population. This is not independent prospective evidence. "
            "R1 preserves the original Agent 1 probability and winner; it only ranks."
        ),
    }


def chronological_walk_forward(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (row["date"], row["prediction_id"]))
    dates = sorted({row["date"] for row in ordered})
    if len(dates) < 10:
        return {"status": "INSUFFICIENT_DATES", "dates": len(dates)}
    # ISO week buckets keep the expanding-origin rule simple and deterministic.
    weeks: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in ordered:
        year, week, _ = datetime.fromisoformat(row["date"]).isocalendar()
        weeks[f"{year}-W{week:02d}"].append(row)
    week_keys = sorted(weeks)
    out = {"weeks": {}, "initial_weeks": 4, "status": "DEVELOPMENT_REUSED_WALK_FORWARD"}
    totals = {
        name: {f: {"n": 0, "wins": 0} for f in TARGET_COVERAGES}
        for name in ("R0", "R1")
    }
    for index, week in enumerate(week_keys):
        if index < 4:
            continue
        train = [row for earlier in week_keys[:index] for row in weeks[earlier]]
        test = weeks[week]
        week_out = {}
        for fraction in TARGET_COVERAGES:
            k = reliability.coverage_count(len(train), fraction)
            thresholds = {
                "R0": sorted((row["confidence"] for row in train), reverse=True)[k - 1],
                "R1": sorted((row["selection_score"] for row in train), reverse=True)[k - 1],
            }
            for name, key in (("R0", "confidence"), ("R1", "selection_score")):
                selected = [row for row in test if row[key] >= thresholds[name]]
                wins = sum(1 for row in selected if row["correct"])
                totals[name][fraction]["n"] += len(selected)
                totals[name][fraction]["wins"] += wins
                week_out[f"{name}_{fraction:.2f}"] = {
                    "n": len(selected),
                    "wins": wins,
                    "accuracy": wins / len(selected) if selected else None,
                    "threshold": thresholds[name],
                }
        out["weeks"][week] = week_out
    out["aggregate"] = {
        name: {
            f"{fraction:.2f}": {
                "n": totals[name][fraction]["n"],
                "wins": totals[name][fraction]["wins"],
                "accuracy": (
                    totals[name][fraction]["wins"] / totals[name][fraction]["n"]
                    if totals[name][fraction]["n"]
                    else None
                ),
                "achieved_coverage": (
                    totals[name][fraction]["n"] / sum(len(weeks[w]) for w in week_keys[4:])
                    if sum(len(weeks[w]) for w in week_keys[4:])
                    else None
                ),
            }
            for fraction in TARGET_COVERAGES
        }
        for name in ("R0", "R1")
    }
    return out


def market_context(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row["market_eligible"]]
    if not eligible:
        return {"eligible": 0}
    by_tour = {}
    for tour in ("ATP_MAIN", "CHALLENGER"):
        subset = [row for row in eligible if row["tour"] == tour]
        if not subset:
            by_tour[tour] = {"n": 0}
            continue
        probabilities = [float(row["market_p1"]) for row in subset]
        by_tour[tour] = {
            "n": len(subset),
            "mean_market_probability": sum(probabilities) / len(probabilities),
            "mean_implied_decimal_odds": sum(1.0 / p for p in probabilities) / len(probabilities),
            "note": "1/p is an implied price, not a quoted decimal price",
        }
    return {
        "eligible": len(eligible),
        "rule": "0 <= market_age_hours <= 24 in the frozen export",
        "by_tour": by_tour,
    }


def write_scores_csv(rows: Sequence[Mapping[str, Any]]) -> None:
    fieldnames = [
        "prediction_id", "match_id", "date", "tour", "surface", "round",
        "confidence", "a1_probability_p1", "a1_selected_side", "correct",
        "risk_points", "reliability_score", "reliability_tier", "selection_score",
        "player_cluster", "tournament_proxy", "market_p1", "market_eligible",
    ]
    with SCORES_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item["prediction_id"]):
            writer.writerow({key: row[key] for key in fieldnames})


def write_curve_csv(rows: Sequence[Mapping[str, Any]]) -> None:
    curves = {
        "R0_original_confidence": reliability.risk_coverage(rows, "confidence")["curve"],
        "R1_reliability_adjusted": reliability.risk_coverage(rows, "selection_score")["curve"],
    }
    with CURVE_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ranking", "rank", "coverage", "selective_risk", "generalized_risk"])
        for label, curve in curves.items():
            for point in curve:
                writer.writerow(
                    [
                        label,
                        point["rank"],
                        point["coverage"],
                        point["selective_risk"],
                        point["generalized_risk"],
                    ]
                )


def main() -> None:
    all_observations = load_observations()
    observations = strict_pre_match(all_observations)
    excluded_pre_match = len(all_observations) - len(observations)
    features = [build_features(observation) for observation in observations]
    rows = [scored_row(observation, feature) for observation, feature in zip(observations, features, strict=True)]
    audit = feature_audit(
        observations,
        features,
        source_rows=len(all_observations),
        excluded_pre_match=excluded_pre_match,
    )
    actual = actual_evaluation(rows)
    synthetic = reliability.synthetic_suite()
    for payload in (audit, actual, synthetic):
        payload["can_promote"] = False
        payload["production_changed"] = False
        payload["database_writes"] = False
        payload["research_only"] = True
    write_json(AUDIT_JSON, audit)
    write_json(ACTUAL_JSON, actual)
    write_json(SYNTHETIC_JSON, synthetic)
    write_scores_csv(rows)
    write_curve_csv(rows)
    artifacts = [AUDIT_JSON, ACTUAL_JSON, SYNTHETIC_JSON, SCORES_CSV, CURVE_CSV]
    if REPORT.exists():
        artifacts.append(REPORT)
    if TESTS.exists():
        artifacts.append(TESTS)
    manifest = {
        "version": "A1_RELIABILITY_MANIFEST_V1",
        "generated_at": datetime.now(UTC).isoformat(),
        "base_commit": "559df57c225a3c7d2c0321aa860244f89031327e",
        "integration_reference": "9343a756db496fc0730e773a79ece9c782d91854",
        "branch": "research/a1-identity-bridge-forward-shadow-20261124",
        "can_promote": False,
        "production_changed": False,
        "database_writes": False,
        "provider_calls": False,
        "new_spend": False,
        "historical_model_training": False,
        "agent3_replaced": False,
        "frozen_thresholds": {
            "primary": marginal.PRIMARY_THRESHOLD,
            "secondary": marginal.SECONDARY_THRESHOLD,
        },
        "sources": {
            "population_csv": str(POPULATION.relative_to(ROOT)).replace("\\", "/"),
            "population_csv_sha256": sha256(POPULATION),
        },
        "artifacts": [
            {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in artifacts
        ],
        "code": [
            {
                "path": "src/sports_edge_tennis/validation/a1_reliability_shadow.py",
                "bytes": (SRC / "sports_edge_tennis/validation/a1_reliability_shadow.py").stat().st_size,
                "sha256": sha256(SRC / "sports_edge_tennis/validation/a1_reliability_shadow.py"),
            },
            {
                "path": "ops/validation/a1_reliability_research_20261127.py",
                "bytes": Path(__file__).stat().st_size,
                "sha256": sha256(Path(__file__)),
            },
        ],
        "hash_normalization": "LF for text artifacts (.json/.csv/.md/.py/.txt/.jsonl)",
        "validation": {
            "source_settled_rows": len(all_observations),
            "excluded_at_or_after_start": excluded_pre_match,
            "settled_rows": len(observations),
            "feature_count": len(reliability.FEATURE_CONTRACT),
            "r0_n": len(rows),
            "r1_n": len(rows),
            "a2_available": sum(1 for feature in features if feature.a2_status == "PRESENT"),
            "historical_training_performed": False,
        },
        "note": (
            "Research only. R0/R1 are fixed rules on development-reused data. R2/R3/R4 are "
            "synthetic only because commercial training rights are not established."
        ),
    }
    write_json(MANIFEST, manifest)
    print(
        json.dumps(
            {
                "audit": AUDIT_JSON.name,
                "actual": ACTUAL_JSON.name,
                "synthetic": SYNTHETIC_JSON.name,
                "scores": SCORES_CSV.name,
                "manifest": MANIFEST.name,
                "validation": manifest["validation"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
