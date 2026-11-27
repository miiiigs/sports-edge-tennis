"""Run the frozen Agent 1 selective-performance experiment from the committed CSV.

Read-only research.  No model fitting, no database access, no threshold tuning on
the evaluation population.  Emits the full evidence JSON, the ranked population
and the complete risk-coverage curve.

Example::

    python ops/validation/a1_selective_performance_experiment_20261126.py
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sports_edge_tennis.validation.a1_selective_performance import (
    COVERAGE_POINTS,
    _summarise_scores,
    confidence,
    coverage_count,
    evaluate_coverage,
    evaluate_population,
    four_way,
    load_population_csv,
    rank_rows,
    risk_coverage_curve,
    wilson_interval,
)

EVIDENCE = ROOT / "docs" / "research" / "evidence"
POPULATION = EVIDENCE / "a1_selective_population_20261126.csv"
CENSUS = EVIDENCE / "a1_target_census_20261126.csv"
POPULATION_META = EVIDENCE / "a1_selective_population_meta_20261126.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def market_benchmark(rows: list[dict], age_limit_hours: float = 24.0) -> dict:
    eligible = [
        row
        for row in rows
        if row.get("market_p1") is not None
        and row.get("market_age_hours") is not None
        and row["market_age_hours"] <= age_limit_hours
    ]
    result: dict = {
        "age_limit_hours": age_limit_hours,
        "available_rows": sum(1 for row in rows if row.get("market_p1") is not None),
        "eligible_rows": len(eligible),
        "coverage_of_settled": len(eligible) / len(rows),
    }
    if len(eligible) < 20:
        result["status"] = "INSUFFICIENT"
        return result
    result["full_population"] = {
        "market": _summarise_scores([(float(row["market_p1"]), int(row["y"])) for row in eligible]),
        "original": _summarise_scores([(float(row["p1_original"]), int(row["y"])) for row in eligible]),
        "corrected": _summarise_scores([(float(row["p1_corrected"]), int(row["y"])) for row in eligible]),
    }
    curves = {}
    for label, key in (
        ("market", "market_p1"),
        ("original", "p1_original"),
        ("corrected", "p1_corrected"),
    ):
        ranked = sorted(
            eligible,
            key=lambda row: (-confidence(float(row[key])), int(row["prediction_id"])),
        )
        points = {}
        for fraction in COVERAGE_POINTS:
            k = coverage_count(len(ranked), fraction)
            selected = ranked[:k]
            block = _summarise_scores([(float(row[key]), int(row["y"])) for row in selected])
            wins = int(block["wins"])
            points[f"{fraction:.2f}"] = {
                "selection_count": k,
                "wins": wins,
                "accuracy": block["accuracy"],
                "wilson95": wilson_interval(wins, k),
                "brier": block["brier"],
                "log_loss": block["log_loss"],
                "mean_confidence": block["mean_confidence"],
            }
        curves[label] = points
    result["coverage_curves"] = curves
    result["booking_note"] = (
        "The benchmark uses only a pre-issue quote that existed at or before the "
        "original prediction as_of. It is not a closing-line or execution result."
    )
    return result


def ranked_population(rows: list[dict], out_path: Path) -> dict:
    rank_original = {
        int(row["prediction_id"]): index + 1
        for index, row in enumerate(rank_rows(rows, "p1_original"))
    }
    rank_corrected = {
        int(row["prediction_id"]): index + 1
        for index, row in enumerate(rank_rows(rows, "p1_corrected"))
    }
    columns = [
        "prediction_id",
        "match_id",
        "date",
        "tour",
        "round_code",
        "rank_original",
        "rank_corrected",
        "confidence_original",
        "confidence_corrected",
        "p1_original",
        "p1_corrected",
        "side_original",
        "side_corrected",
        "winner_side",
        "won_original",
        "won_corrected",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: rank_original[int(item["prediction_id"])]):
            writer.writerow(
                {
                    "prediction_id": row["prediction_id"],
                    "match_id": row["match_id"],
                    "date": row["date"],
                    "tour": row["tour"],
                    "round_code": row.get("round_code"),
                    "rank_original": rank_original[int(row["prediction_id"])],
                    "rank_corrected": rank_corrected[int(row["prediction_id"])],
                    "confidence_original": row["confidence_original"],
                    "confidence_corrected": row["confidence_corrected"],
                    "p1_original": row["p1_original"],
                    "p1_corrected": row["p1_corrected"],
                    "side_original": row["side_original"],
                    "side_corrected": row["side_corrected"],
                    "winner_side": row["winner_side"],
                    "won_original": row["correct_original"],
                    "won_corrected": row["correct_corrected"],
                }
            )
    return {
        "path": out_path.name,
        "sha256": sha256(out_path),
        "rows": len(rows),
    }


def risk_curve_csv(rows: list[dict], out_path: Path) -> dict:
    curves = {
        "original": risk_coverage_curve(rows, "p1_original"),
        "corrected": risk_coverage_curve(rows, "p1_corrected"),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["version", "rank", "coverage", "selective_risk", "generalized_risk", "confidence"]
        )
        for version, points in curves.items():
            for point in points:
                writer.writerow(
                    [
                        version,
                        int(point["rank"]),
                        point["coverage"],
                        point["selective_risk"],
                        point["generalized_risk"],
                        point["confidence"],
                    ]
                )
    return {"path": out_path.name, "sha256": sha256(out_path), "rows": sum(len(c) for c in curves.values())}


def population_reconciliation(population_meta: dict, census_path: Path) -> dict:
    with census_path.open(newline="", encoding="utf-8") as handle:
        census = list(csv.DictReader(handle))
    reasons: dict[str, int] = {}
    for row in census:
        reason = row["exclusion_reason"]
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "population_3776_targets": len(census),
        "population_3223_safely_completed_at_cutoff": sum(1 for row in census if row["settled"] == "1"),
        "population_3271_later_settlement_snapshot": 3271,
        "population_3284_later_settlement_snapshot": 3284,
        "population_3778_later_target_universe": 3778,
        "exclusion_counts": dict(sorted(reasons.items())),
        "explicit_rules": {
            "3776": "latest non-experiment COMPLETED Agent 1 prediction per match, as_of >= 2026-09-10 and <= 2026-11-25T07:20Z",
            "3223": "3776 with latest result COMPLETED and winner_side in (1,2) visible at the 2026-11-25T07:20Z freeze",
            "3271": "same 3776 target universe; later settlement snapshot (last result observation 2026-11-25T08:13Z) used by the Manager-interaction run",
            "3284": "same 3776 target universe; later settlement snapshot (last result observation 2026-11-26T04:03Z)",
            "3778": "same rule without the 07:20Z upper cutoff; two additional targets issued between 07:20Z and 11:00Z",
        },
        "reconciliation_note": (
            "These are four settlement snapshots and one expanded target universe, not four "
            "independent eligibility definitions. The CSV census contains the exact 3,223 settled "
            "ids and every exclusion reason. Later snapshots add settled fixtures; they do not "
            "add model skill."
        ),
        "census_sha256": sha256(census_path),
        "population_meta_sha256": hashlib.sha256(
            json.dumps(population_meta, sort_keys=True).encode()
        ).hexdigest(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", default=str(POPULATION))
    parser.add_argument("--census", default=str(CENSUS))
    parser.add_argument("--population-meta", default=str(POPULATION_META))
    parser.add_argument(
        "--out", default=str(EVIDENCE / "a1_selective_performance_20261126.json")
    )
    parser.add_argument("--top-k", type=int, default=800)
    parser.add_argument("--development-end", default="2026-10-31")
    parser.add_argument("--no-bootstrap", action="store_true")
    args = parser.parse_args(argv)

    rows = load_population_csv(args.population)
    population_meta = json.loads(Path(args.population_meta).read_text(encoding="utf-8"))
    if len(rows) != 3223:
        raise SystemExit(f"unexpected settled population {len(rows)}")

    report = evaluate_population(
        rows,
        development_end=args.development_end,
        top_k=args.top_k,
        bootstrap=not args.no_bootstrap,
    )
    report["provenance_block"] = {
        "population_csv": {
            "path": Path(args.population).name,
            "sha256": sha256(Path(args.population)),
            "rows": len(rows),
        },
        "census_csv": {
            "path": Path(args.census).name,
            "sha256": sha256(Path(args.census)),
            "rows": population_meta["e3_targets"],
        },
        "source_ledger_sha256": population_meta["source_ledger_sha256"],
        "exporter_version": population_meta["version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": "agent1_surface_elo_v1",
        "feature_version": "agent1_surface_elo_inputs_v1",
        "config_version": "agent1_surface_elo_config_v1",
        "evaluation_cutoff": "2026-11-25T07:20:00Z",
    }
    report["market_benchmark"] = market_benchmark(rows)
    strict_rows = [
        row
        for row in rows
        if str(row["as_of"]) < str(row["match_start_at"])
    ]
    report["strict_prematch_sensitivity"] = {
        "rule": "as_of strictly before match_start_at",
        "population": len(rows),
        "excluded": len(rows) - len(strict_rows),
        "excluded_prediction_ids": sorted(
            int(row["prediction_id"])
            for row in rows
            if str(row["as_of"]) >= str(row["match_start_at"])
        ),
        "excluded_detail": [
            {
                "prediction_id": int(row["prediction_id"]),
                "match_id": int(row["match_id"]),
                "as_of": row["as_of"],
                "match_start_at": row["match_start_at"],
                "seconds_after_start": (
                    datetime.fromisoformat(str(row["as_of"]))
                    - datetime.fromisoformat(str(row["match_start_at"]))
                ).total_seconds(),
            }
            for row in rows
            if str(row["as_of"]) >= str(row["match_start_at"])
        ],
        "original": evaluate_coverage(
            strict_rows,
            "p1_original",
            points=(0.40, 0.45, 0.50, 1.00),
            bootstrap=False,
        ),
        "corrected": evaluate_coverage(
            strict_rows,
            "p1_corrected",
            points=(0.40, 0.45, 0.50, 1.00),
            bootstrap=False,
        ),
        "four_way_top800": four_way(strict_rows, k=args.top_k),
    }
    report["population_reconciliation"] = population_reconciliation(
        population_meta, Path(args.census)
    )
    report["ranked_population"] = ranked_population(
        rows, EVIDENCE / "a1_selective_ranked_populations_20261126.csv"
    )
    report["risk_coverage_curve_csv"] = risk_curve_csv(
        rows, EVIDENCE / "a1_selective_risk_coverage_20261126.csv"
    )

    out = Path(args.out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "population": report["population"],
        "validation": report["validation"],
        "four_way": report["four_way_top800"]["wins"],
        "cohorts": {
            "shared": report["cohort_comparison_top800"]["shared"]["n"],
            "removed": report["cohort_comparison_top800"]["removed"]["n"],
            "added": report["cohort_comparison_top800"]["added"]["n"],
        },
        "risk_coverage": report["risk_coverage"],
        "market": {
            "eligible": report["market_benchmark"]["eligible_rows"],
            "full": report["market_benchmark"].get("full_population"),
        },
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
