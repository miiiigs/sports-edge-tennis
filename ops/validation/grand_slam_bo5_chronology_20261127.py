"""Chronological Grand Slam best-of-five experiment.

Default mode runs the synthetic validation harness. A real run requires an
explicit, commercially authorised dataset; the CLI refuses to score any record
whose rights status is not COMMERCIAL_AUTHORIZED.

No provider call, database write or purchase is made.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime

from sports_edge_tennis.validation.grand_slam_bo5_chronology import (
    COMMERCIAL_AUTHORIZED,
    ExperimentConfig,
    dataset_from_payload,
    run_chronological_experiment,
    synthetic_validation,
)

VERSION = "GRAND_SLAM_BO5_CLI_20261127_V1"

EXTRACT_TEMPLATE = {
    "version": "GRAND_SLAM_BO5_DATASET_V1",
    "rights": {
        "status": "UNRESOLVED",
        "license_reference": None,
        "authorised_by": None,
    },
    "matches": [
        {
            "match_id": "string",
            "slam": "Australian Open|Roland Garros|Wimbledon|US Open",
            "season": 2022,
            "edition_start": "2022-01-17",
            "round_code": "R128|R64|R32|R16|QF|SF|F|Q1|Q2|Q3",
            "draw_stage": "MAIN_DRAW|QUALIFYING",
            "surface": "HARD|CLAY|GRASS",
            "best_of": 5,
            "winner_is_player_1": True,
            "a1_probability": 0.64,
            "a2_bo5_probability": 0.68,
            "a2_bo3_probability": None,
            "set_win_probability": 0.60,
            "manager_c_probability": 0.66,
            "rating_difference": 120.0,
            "matches_last_365": 45,
            "days_since_last_match": 21,
            "data_rights": "COMMERCIAL_AUTHORIZED",
            "source": "licensed-vendor-name",
        }
    ],
}

REAL_DATA_BLOCKER = {
    "status": "BLOCKED_NO_COMMERCIALLY_AUTHORIZED_HISTORICAL_CORPUS",
    "reasons": [
        "The largest stored historical corpus is Sackmann-derived (115,678 rows "
        "reported by the PR #351 procurement audit) under CC BY-NC-SA, which is "
        "non-commercial and requires a separate commercial licence.",
        "No signed commercial licence, vendor export or authorised official "
        "historical feed is present in the repository.",
        "The 2016-2026 Australian Open rows are retrospective history, not "
        "original pre-match forecasts; the original-forecast cohort is zero.",
        "This session has no authorised read-only production database access and "
        "makes no provider call.",
    ],
    "what_would_unblock": [
        "A commercially licensed Grand Slam match corpus with pre-match "
        "probabilities or the inputs needed to recompute them.",
        "An authorised read-only extract of the historical match and serve/return "
        "tables under a commercial data licence.",
        "An owner-approved provider probe coordinated with PR #351.",
    ],
}


def _load_dataset(path: str) -> tuple[list, dict]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    rights = payload.get("rights") or {}
    matches = dataset_from_payload(payload)
    return matches, rights


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", help="commercially authorised dataset")
    parser.add_argument("--require-commercial-rights", action="store_true")
    parser.add_argument("--min-training-editions", type=int, default=4)
    parser.add_argument("--bootstrap-iterations", type=int, default=1000)
    parser.add_argument("--bootstrap-seed", type=int, default=20261127)
    parser.add_argument("--extract-template", action="store_true")
    parser.add_argument(
        "--out-json",
        default="docs/research/evidence/grand_slam_bo5_chronology_20261127.json",
    )
    args = parser.parse_args(argv)

    if args.extract_template:
        print(json.dumps(EXTRACT_TEMPLATE, indent=2, sort_keys=True))
        return 0

    if args.input_json:
        matches, rights = _load_dataset(args.input_json)
        if args.require_commercial_rights:
            declared = str(rights.get("status") or "UNRESOLVED")
            if declared != COMMERCIAL_AUTHORIZED:
                payload = {
                    "version": VERSION,
                    "mode": "REAL_DATA_REFUSED",
                    "declared_rights": declared,
                    "blocker": REAL_DATA_BLOCKER,
                    "provider_calls": 0,
                    "database_writes": 0,
                }
                _emit(payload, args.out_json)
                return 3
        result = run_chronological_experiment(
            matches,
            config=ExperimentConfig(
                min_training_editions=args.min_training_editions,
                bootstrap_iterations=args.bootstrap_iterations,
                bootstrap_seed=args.bootstrap_seed,
                require_commercial_rights=args.require_commercial_rights,
            ),
        )
        payload = {
            "version": VERSION,
            "mode": "REAL_DATA",
            "report_as_of_utc": datetime.now(UTC).isoformat(),
            "rights": rights,
            **result,
        }
        _emit(payload, args.out_json)
        return 0

    result = synthetic_validation(seed=args.bootstrap_seed)
    payload = {
        "version": VERSION,
        "mode": "SYNTHETIC_VALIDATION_ONLY",
        "report_as_of_utc": datetime.now(UTC).isoformat(),
        "real_data_status": "BLOCKED",
        "real_data_blocker": REAL_DATA_BLOCKER,
        "provider_calls": 0,
        "database_writes": 0,
        "provider_spend": 0,
        **result,
    }
    _emit(payload, args.out_json)
    return 0 if payload["synthetic_validation"]["all_checks_passed"] else 1


def _emit(payload: dict, out_json: str) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if out_json:
        directory = os.path.dirname(os.path.abspath(out_json))
        os.makedirs(directory, exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
