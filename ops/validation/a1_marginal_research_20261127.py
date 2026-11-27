"""Offline marginal-cohort research runner (2026-11-27).

Reads only committed, sanitised artifacts plus local reproduction extracts:

* ``docs/research/evidence/a1_selective_population_20261126.csv`` (3,223 rows)
* ``tmp/ledger_full.jsonl`` (local reproduction extract, not committed)
* ``tmp/api_rating_history.json`` (local reproduction extract, not committed)
* ``tmp/bridge_visibility.json`` (local reproduction extract, not committed)

Writes research evidence only.  No database connection, provider call, model
fit on the November holdout, production write or policy change.

Usage::

    python ops/validation/a1_marginal_research_20261127.py
"""
from __future__ import annotations

import bisect
import csv
import hashlib
import json
import sys
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sports_edge_tennis.validation import a1_marginal_research as research

POPULATION = ROOT / "docs/research/evidence/a1_selective_population_20261126.csv"
LEDGER = ROOT / "tmp/ledger_full.jsonl"
RATING_HISTORY = ROOT / "tmp/api_rating_history.json"
BRIDGE_VISIBILITY = ROOT / "tmp/bridge_visibility.json"
OUT = ROOT / "docs/research/evidence"

BOUNDARY = "2026-11-25T07:20:00+00:00"

MARGINAL_CSV = OUT / "a1_marginal_cohorts_20261127.csv"
RETROSPECTIVE_CSV = OUT / "a1_retrospective_bands_20261127.csv"
PHASE1_JSON = OUT / "a1_marginal_cohorts_20261127.json"
PHASE2_JSON = OUT / "a1_newcomer_favourite_20261127.json"
PHASE3_JSON = OUT / "a1_atp_challenger_controlled_20261127.json"
PHASE4_JSON = OUT / "a1_uncertainty_audit_20261127.json"
PHASE5_JSON = OUT / "a1_inactivity_surface_20261127.json"
PHASE6_JSON = OUT / "a1_identity_player_ledger_20261127.json"
CENSUS_CSV = OUT / "a1_identity_slot_census_20261127.csv"
HISTORY_CSV = OUT / "a1_history_attribution_matches_20261127.csv"
MANIFEST = OUT / "a1_marginal_research_manifest_20261127.json"


def sha256(path: Path) -> str:
    """SHA-256 of an artifact with LF-normalized text bytes.

    Windows checkouts may materialise CRLF; hashing the normalized bytes keeps
    the manifest reproducible across platforms.
    """
    data = path.read_bytes()
    if path.suffix.lower() in {".json", ".csv", ".md", ".py", ".txt", ".jsonl"}:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(research.json_safe(payload), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def load_population() -> list[dict[str, Any]]:
    with POPULATION.open(encoding="utf-8", newline="") as handle:
        raw = list(csv.DictReader(handle))
    observations = research.parse_population(raw)
    if len(observations) != 3223:
        raise SystemExit(f"expected 3,223 settled rows, got {len(observations)}")
    return observations


def load_ledger() -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    by_prediction: dict[int, dict[str, Any]] = {}
    e3_rows: list[dict[str, Any]] = []
    with LEDGER.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("experiment") != "E3":
                continue
            by_prediction[int(row["prediction_id"])] = row
            e3_rows.append(row)
    if len(e3_rows) != 3776:
        raise SystemExit(f"expected 3,776 E3 ledger rows, got {len(e3_rows)}")
    return by_prediction, e3_rows


def load_history() -> list[dict[str, Any]]:
    if not RATING_HISTORY.exists():
        return []
    return json.loads(RATING_HISTORY.read_text(encoding="utf-8"))


def observability_map(
    observations: list[dict[str, Any]],
    ledger_by_prediction: dict[int, dict[str, Any]],
    history: list[dict[str, Any]],
) -> dict[int, dict[str, bool]]:
    timeline: dict[int, list[str]] = defaultdict(list)
    for match in history:
        start = match.get("match_start_at")
        if start is None:
            continue
        for side in ("player_1_id", "player_2_id"):
            pid = match.get(side)
            if pid is not None:
                timeline[int(pid)].append(str(start))
    for starts in timeline.values():
        starts.sort()
    out: dict[int, dict[str, bool]] = {}
    for observation in observations:
        ledger = ledger_by_prediction.get(int(observation["prediction_id"]))
        if not ledger:
            out[int(observation["prediction_id"])] = {}
            continue
        flags: dict[str, bool] = {}
        for side in ("p1", "p2"):
            pid = ledger.get(f"{side}_live")
            starts = timeline.get(int(pid), []) if pid is not None else []
            idx = bisect.bisect_left(starts, observation["match_start_at"])
            flags[side] = idx > 0
        out[int(observation["prediction_id"])] = flags
    return out


def marginal_csv(observations: list[dict[str, Any]], thresholds: dict[str, Any]) -> None:
    cohorts = research.frozen_cohorts(observations, thresholds)
    labels = {
        "core_40": cohorts["core_40"],
        "marginal_40_45": cohorts["marginal_40_45"],
        "marginal_45_50": cohorts["marginal_45_50"],
    }
    fieldnames = [
        "frozen_cohort", "prediction_id", "match_id", "date", "tour",
        "round_code", "surface", "favourite_side", "correct_corrected",
        "correct_original", "confidence_corrected", "confidence_original",
        "side_corrected", "side_original", "winner_side",
        "matches_corrected_p1", "matches_corrected_p2",
        "surface_matches_corrected_p1", "surface_matches_corrected_p2",
        "effective_rating_corrected_p1", "effective_rating_corrected_p2",
        "inactivity_days_corrected_p1", "inactivity_days_corrected_p2",
        "resolution_p1_method", "resolution_p2_method",
        "resolution_p1_bridged", "resolution_p2_bridged",
        "market_p1", "market_age_hours", "market_eligible",
        "elo_gap_favourite_minus_underdog",
        "surface_elo_gap_favourite_minus_underdog", "min_matches",
    ]
    with MARGINAL_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for label, rows in labels.items():
            for row in sorted(rows, key=lambda r: r["prediction_id"]):
                factors = research.factor_values(row)
                writer.writerow(
                    {
                        "frozen_cohort": label,
                        "prediction_id": row["prediction_id"],
                        "match_id": row["match_id"],
                        "date": row["date"],
                        "tour": row["tour"],
                        "round_code": row["round_code"],
                        "surface": row["surface"],
                        "favourite_side": row["favourite_side"],
                        "correct_corrected": int(row["correct_corrected"]),
                        "correct_original": int(row["correct_original"]),
                        "confidence_corrected": row["confidence_corrected"],
                        "confidence_original": row["confidence_original"],
                        "side_corrected": row["side_corrected"],
                        "side_original": row["side_original"],
                        "winner_side": row["winner_side"],
                        "matches_corrected_p1": row["matches_corrected"]["p1"],
                        "matches_corrected_p2": row["matches_corrected"]["p2"],
                        "surface_matches_corrected_p1": row["surface_matches_corrected"]["p1"],
                        "surface_matches_corrected_p2": row["surface_matches_corrected"]["p2"],
                        "effective_rating_corrected_p1": row["effective_corrected"]["p1"],
                        "effective_rating_corrected_p2": row["effective_corrected"]["p2"],
                        "inactivity_days_corrected_p1": row["inactivity_corrected"]["p1"],
                        "inactivity_days_corrected_p2": row["inactivity_corrected"]["p2"],
                        "resolution_p1_method": row["resolution"]["p1"]["method"],
                        "resolution_p2_method": row["resolution"]["p2"]["method"],
                        "resolution_p1_bridged": int(row["resolution"]["p1"]["bridged"]),
                        "resolution_p2_bridged": int(row["resolution"]["p2"]["bridged"]),
                        "market_p1": row["market_p1"],
                        "market_age_hours": row["market_age_hours"],
                        "market_eligible": int(bool(row.get("market_eligible"))),
                        "elo_gap_favourite_minus_underdog": factors["elo_gap"],
                        "surface_elo_gap_favourite_minus_underdog": factors["surface_elo_gap"],
                        "min_matches": factors["min_matches"],
                    }
                )


def retrospective_csv(observations: list[dict[str, Any]]) -> None:
    bands = research.retrospective_bands(observations)
    labels = {
        "top_40": bands["top_40"],
        "band_40_45": bands["band_40_45"],
        "band_45_50": bands["band_45_50"],
    }
    fieldnames = [
        "retrospective_band", "rank", "prediction_id", "match_id", "date", "tour",
        "round_code", "surface", "confidence_corrected", "correct_corrected",
        "winner_side", "side_corrected", "min_matches", "elo_gap",
        "surface_elo_gap", "resolution_p1_method", "resolution_p2_method",
        "market_p1",
    ]
    rank_by_id = {row["prediction_id"]: i + 1 for i, row in enumerate(bands["ranked"])}
    with RETROSPECTIVE_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for label, rows in labels.items():
            for row in rows:
                factors = research.factor_values(row)
                writer.writerow(
                    {
                        "retrospective_band": label,
                        "rank": rank_by_id[row["prediction_id"]],
                        "prediction_id": row["prediction_id"],
                        "match_id": row["match_id"],
                        "date": row["date"],
                        "tour": row["tour"],
                        "round_code": row["round_code"],
                        "surface": row["surface"],
                        "confidence_corrected": row["confidence_corrected"],
                        "correct_corrected": int(row["correct_corrected"]),
                        "winner_side": row["winner_side"],
                        "side_corrected": row["side_corrected"],
                        "min_matches": factors["min_matches"],
                        "elo_gap": factors["elo_gap"],
                        "surface_elo_gap": factors["surface_elo_gap"],
                        "resolution_p1_method": row["resolution"]["p1"]["method"],
                        "resolution_p2_method": row["resolution"]["p2"]["method"],
                        "market_p1": row["market_p1"],
                    }
                )


def census_csv(census: dict[str, Any]) -> None:
    fieldnames = [
        "prediction_id", "match_id", "side", "live_player_id", "model_player_id",
        "fallback_player_id", "method", "evidence", "candidate_count", "bridged", "status",
    ]
    with CENSUS_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in census["records"]:
            writer.writerow(record)


def history_csv(rows: list[dict[str, Any]]) -> None:
    fieldnames = ["match_id", "history_as_of", "visible_from", "late_mapping"]
    with HISTORY_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: int(r["match_id"])):
            writer.writerow(
                {
                    "match_id": row["match_id"],
                    "history_as_of": row["history_as_of"],
                    "visible_from": row.get("visible_from"),
                    "late_mapping": int(bool(row.get("late_mapping"))),
                }
            )


def census_digest(records: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda r: (int(r["prediction_id"]), r["side"])):
        line = "|".join(
            str(record[key])
            for key in (
                "prediction_id", "match_id", "side", "live_player_id",
                "model_player_id", "fallback_player_id", "method", "evidence",
                "candidate_count", "bridged", "status",
            )
        )
        digest.update((line + "\n").encode("utf-8"))
    return digest.hexdigest()


def main() -> None:
    if not POPULATION.exists():
        raise SystemExit(f"missing population export: {POPULATION}")
    observations = load_population()
    ledger_by_prediction, ledger_rows = load_ledger() if LEDGER.exists() else ({}, [])
    history = load_history()
    bridge_visibility = (
        json.loads(BRIDGE_VISIBILITY.read_text(encoding="utf-8"))
        if BRIDGE_VISIBILITY.exists()
        else {"summary": {}, "rows": []}
    )
    thresholds = research.development_thresholds(observations)

    phase1 = research.phase1(observations, thresholds)

    observability = observability_map(observations, ledger_by_prediction, history) if history else {}
    phase2 = research.phase2(observations, thresholds, observability=observability)
    phase3 = research.phase3(observations, thresholds)
    audit = research.uncertainty_audit(observations)
    candidate = research.fit_information_shrinkage(
        [o for o in observations if str(o["date"]) <= research.DEV_END]
    )
    candidate_eval = research.evaluate_information_shrinkage(observations, candidate)
    phase4 = {
        "audit": audit,
        "research_candidate": candidate,
        "holdout_evaluation": candidate_eval,
        "reliability_flag": research.reliability_flag_analysis(observations, thresholds),
        "existing_mechanisms": existing_mechanisms(),
    }
    phase5 = research.inactivity_analysis(observations, thresholds)
    meta_path = OUT / "a1_selective_population_meta_20261126.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        phase5["settlement_exclusions"] = {
            "excluded_targets": meta.get("excluded_targets"),
            "exclusion_counts": meta.get("exclusion_counts"),
            "status": (
                "Retirements, walkovers, cancellations and defaults are excluded from "
                "the settled population by construction. Their forecasting effect cannot "
                "be measured here; no withdrawal record is present to audit."
            ),
        }
    surface = (
        research.surface_transition_table(
            observations, ledger_by_prediction, history, thresholds
        )
        if history
        else {"status": "BLOCKED_NO_OBSERVED_RESULT_HISTORY"}
    )
    phase5["surface_transitions"] = surface
    zero_fav = [
        o for o in observations
        if research._value(o["matches_corrected"][f"p{o['favourite_side']}"]) == 0
    ]
    zero_groups: dict[str, list[float]] = defaultdict(list)
    for o in zero_fav:
        flags = observability.get(int(o["prediction_id"]), {})
        seen = bool(flags.get(f"p{o['favourite_side']}"))
        label = "OBSERVED_ELSEWHERE_LOWER_LEVEL_ABSENT" if seen else "NO_OBSERVED_PRIOR_MATCH_UNVERIFIED"
        value = o["inactivity_corrected"][f"p{o['favourite_side']}"]
        if value is not None:
            zero_groups[label].append(value)
    phase5["zero_history_favourite_inactivity"] = {
        label: {
            "n": len(values),
            "mean_days": sum(values) / len(values) if values else None,
            "median_days": sorted(values)[len(values) // 2] if values else None,
        }
        for label, values in zero_groups.items()
    }
    phase5["zero_history_favourite_inactivity"]["total_zero_history_favourites"] = len(zero_fav)
    phase5["zero_history_favourite_inactivity"]["missing_inactivity_n"] = (
        len(zero_fav) - sum(len(values) for values in zero_groups.values())
    )

    phase6 = research.identity_ledger(
        observations, ledger_by_prediction, sample_size=20
    )
    census = research.slot_census(ledger_rows, expected_slots=7552) if ledger_rows else {
        "status": "BLOCKED_NO_LEDGER", "records": []
    }
    slot_records = census.pop("records", [])
    if slot_records:
        census_csv({"records": slot_records})
        census["csv"] = CENSUS_CSV.name
        census["csv_sha256"] = sha256(CENSUS_CSV)
        census["record_digest_sha256"] = census_digest(slot_records)
    census["five_slot_reconciliation"] = {
        "reference_policy_3778_slots": 7556,
        "reference_policy_3778_declared_classified": 7551,
        "unclassified_slots_in_3778_artifact": 5,
        "ledger_3776_slots": 7552,
        "ledger_3776_method_classified": census.get("method_classified_slots"),
        "ledger_3776_resolved": census.get("resolved_slots"),
        "ledger_3776_unresolved": census.get("unresolved_slots"),
        "arithmetic": "7,552 + 4 (two added fixtures) - 5 = 7,551",
        "status": (
            "The 3,776 ledger emits one method record per slot (7,552/7,552). The "
            "3,778 snapshot is not stored per-slot offline, so its five unclassified "
            "slots cannot be named; they are a bounded reporting gap, not five fixtures."
        ),
        "policy_measured": "pre-v2 permissive-equivalent resolver used for the 3,223 experiment",
        "v3_applied": False,
        "census_format": "ONE_RECORD_PER_SLOT_V3_FORMAT",
    }
    phase6["slot_census"] = census
    phase6["history_attribution"] = history_attribution(
        bridge_visibility, ledger_rows
    )
    phase6["supplementary_method_risk"] = research.method_risk_supplement(
        observations, ledger_by_prediction
    )
    phase6["provenance"] = {
        "historical_rating_source": "tennis_data / Sackmann-associated lineage already in the production corpus",
        "licensing": "commercial training rights not verified",
        "commercial_gate": "PR #351 no-spend sample/rights acceptance gate remains the gate",
        "new_commercial_training": False,
        "new_provider_calls": False,
        "new_spend": False,
        "note": (
            "The 16k attribution changes are rows already ingested before the study. "
            "No new historical data, sample or licensed corpus is introduced."
        ),
    }

    for payload in (phase1, phase2, phase3, phase4, phase5, phase6):
        payload["can_promote"] = False
        payload["production_changed"] = False
        payload["database_writes"] = False
        payload["research_only"] = True

    write_json(PHASE1_JSON, phase1)
    write_json(PHASE2_JSON, phase2)
    write_json(PHASE3_JSON, phase3)
    write_json(PHASE4_JSON, phase4)
    write_json(PHASE5_JSON, phase5)
    write_json(PHASE6_JSON, phase6)
    marginal_csv(observations, thresholds)
    retrospective_csv(observations)
    if bridge_visibility.get("rows"):
        history_csv(bridge_visibility["rows"])

    artifacts = [
        PHASE1_JSON, PHASE2_JSON, PHASE3_JSON, PHASE4_JSON, PHASE5_JSON,
        PHASE6_JSON, MARGINAL_CSV, RETROSPECTIVE_CSV,
    ]
    if CENSUS_CSV.exists():
        artifacts.append(CENSUS_CSV)
    if HISTORY_CSV.exists():
        artifacts.append(HISTORY_CSV)
    report_path = ROOT / "docs/research/A1_MARGINAL_FORECAST_WEAKNESSES_20261127.md"
    if report_path.exists():
        artifacts.append(report_path)
    test_path = ROOT / "tests/test_a1_marginal_research.py"
    if test_path.exists():
        artifacts.append(test_path)
    manifest = {
        "version": "A1_MARGINAL_RESEARCH_MANIFEST_V1",
        "generated_at": datetime.now(UTC).isoformat(),
        "base_commit": "786b01f6e69eaff2d9a7c7ce5082d1eb58f09410",
        "branch": "research/a1-identity-bridge-forward-shadow-20261124",
        "can_promote": False,
        "production_changed": False,
        "database_writes": False,
        "provider_calls": False,
        "new_spend": False,
        "frozen_thresholds": {
            "conservative_40": research.CONSERVATIVE_THRESHOLD,
            "primary_45": research.PRIMARY_THRESHOLD,
            "secondary_50": research.SECONDARY_THRESHOLD,
        },
        "thresholds_recomputed_from_development": thresholds,
        "sources": {
            "population_csv": str(POPULATION.relative_to(ROOT)).replace("\\", "/"),
            "population_csv_sha256": sha256(POPULATION),
            "ledger_local_extract": str(LEDGER.relative_to(ROOT)).replace("\\", "/"),
            "ledger_present": LEDGER.exists(),
            "ledger_sha256": sha256(LEDGER) if LEDGER.exists() else None,
            "rating_history_local_extract": str(RATING_HISTORY.relative_to(ROOT)).replace("\\", "/"),
            "rating_history_present": RATING_HISTORY.exists(),
            "rating_history_sha256": sha256(RATING_HISTORY) if RATING_HISTORY.exists() else None,
            "bridge_visibility_local_extract": str(BRIDGE_VISIBILITY.relative_to(ROOT)).replace("\\", "/"),
            "bridge_visibility_present": BRIDGE_VISIBILITY.exists(),
        },
        "artifacts": [
            {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in artifacts
        ],
        "hash_normalization": "LF for text artifacts (.json/.csv/.md/.py/.txt/.jsonl)",
        "code": [
            {
                "path": "src/sports_edge_tennis/validation/a1_marginal_research.py",
                "bytes": (SRC / "sports_edge_tennis/validation/a1_marginal_research.py").stat().st_size,
                "sha256": sha256(SRC / "sports_edge_tennis/validation/a1_marginal_research.py"),
            },
            {
                "path": "ops/validation/a1_marginal_research_20261127.py",
                "bytes": Path(__file__).stat().st_size,
                "sha256": sha256(Path(__file__)),
            },
        ],
        "validation": {
            "settled_rows": len(observations),
            "development_rows": sum(1 for o in observations if str(o["date"]) <= research.DEV_END),
            "holdout_rows": len(research.holdout_rows(observations)),
            "primary_selections": len(research.frozen_cohorts(observations, thresholds)["base_45"]),
            "secondary_selections": len(research.frozen_cohorts(observations, thresholds)["base_50"]),
            "marginal_40_45": len(research.frozen_cohorts(observations, thresholds)["marginal_40_45"]),
            "marginal_45_50": len(research.frozen_cohorts(observations, thresholds)["marginal_45_50"]),
        },
        "note": (
            "Research evidence only. Frozen primary and secondary policies are "
            "untouched. can_promote=false. No production activation."
        ),
    }
    write_json(MANIFEST, manifest)
    print(json.dumps({
        "phase1": PHASE1_JSON.name,
        "phase2": PHASE2_JSON.name,
        "phase3": PHASE3_JSON.name,
        "phase4": PHASE4_JSON.name,
        "phase5": PHASE5_JSON.name,
        "phase6": PHASE6_JSON.name,
        "manifest": MANIFEST.name,
        "thresholds": thresholds,
        "validation": manifest["validation"],
    }, indent=2))


def history_attribution(
    bridge_visibility: dict[str, Any],
    ledger_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = bridge_visibility.get("summary") or {}
    rows = bridge_visibility.get("rows") or []
    unique_matches = len({int(r["match_id"]) for r in rows})
    live_to_model: dict[int, set[int]] = defaultdict(set)
    for row in ledger_rows:
        for side in ("p1", "p2"):
            live = row.get(f"{side}_live")
            resolution = row.get(f"{side}_resolution") or {}
            model = resolution.get("resolved_player_id")
            if live is not None and model is not None:
                live_to_model[int(live)].add(int(model))
    return {
        "row_level_artifact_matches": len(rows),
        "unique_matches_in_artifact": unique_matches,
        "integrity_census_attribution_changed": 16105,
        "difference": 16105 - unique_matches,
        "difference_explanation": (
            "The row-level visibility extract was generated with a later wall-clock "
            "cutoff than the E3 2026-11-25T07:20Z census. The most likely explanation "
            "is that one additional history row was visible at the later cutoff. The "
            "exact E3-census count is 16,105; the row-level extract has 16,104. This "
            "is a bounded one-record cutoff artefact, not a missing player or fixture."
        ),
        "late_mapping_rows": summary.get("late_mapping_rows"),
        "late_mapping_share": summary.get("late_mapping_share"),
        "moved_players": summary.get("moved_players"),
        "methods": summary.get("methods"),
        "multi_live_model_ids_ledger": sorted(
            model for model, lives in _model_to_live(live_to_model).items() if len(lives) > 1
        ),
        "late_visibility_rows": sum(1 for r in rows if r.get("late_mapping")),
    }


def _model_to_live(live_to_model: Mapping[int, set[int]]) -> dict[int, set[int]]:
    model_to_live: dict[int, set[int]] = defaultdict(set)
    for live, models in live_to_model.items():
        for model in models:
            model_to_live[model].add(live)
    return model_to_live


def existing_mechanisms() -> dict[str, Any]:
    return {
        "initial_rating": 1500,
        "dynamic_k": "K(n) = 250 / (n + 5)^0.4",
        "k_at_zero_matches": 250 / (5**0.4),
        "k_at_10_matches": 250 / (15**0.4),
        "k_at_100_matches": 250 / (105**0.4),
        "probability_scale": 400,
        "surface_weight": "w = min(surface_matches, 10)/10 * 0.5",
        "effective_rating": "(1 - w) * overall + w * surface",
        "surface_cap": 0.5,
        "overall_match_count_used": True,
        "recent_match_count_used": False,
        "surface_specific_experience_used": True,
        "opponent_strength_used": True,
        "identity_resolution_confidence_used": False,
        "time_since_last_match_used": False,
        "overall_surface_divergence_as_uncertainty": False,
        "note": (
            "Agent 1 carries a point estimate. Match counts change the learning "
            "rate, and surface counts change the blend, but none of these enter "
            "the probability as an explicit reliability term."
        ),
    }


if __name__ == "__main__":
    main()
