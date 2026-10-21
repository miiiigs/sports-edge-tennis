"""Reproducible Agent 2 probability-calibration shadow experiment.

The harness consumes the frozen Agent 2 prediction records produced by the
approved form-lookback research (PR #239, head ``eec5919``).  It never retrains
Agent 2, never changes a production model, and never writes to a database.

Protocol
--------
1. Fit every candidate calibrator on 2019-2021 predictions and score 2022.
2. Repeat on 2019-2020 and score 2021.
3. Select the simplest candidate within the pre-registered log-loss tolerance
   of the best pooled validation result.
4. Refit the selected calibrator on 2019-2022 and evaluate the frozen
   2023-2025 test population once.

The test outcomes are not read by the selection step.  ``--mode select``
writes ``selection.json`` before any test evaluation exists.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.a2_calibration_shadow.calibrators import (
    CANDIDATE_NAMES,
    COMPLEXITY_RANK,
    CalibrationModel,
    fit_calibrator,
    logit,
    sigmoid,
)
from research.a2_calibration_shadow.metrics import (
    bootstrap_intervals,
    cox_calibration,
    high_confidence_summary,
    paired_bootstrap,
    point_metrics,
    threshold_effects,
)

EXPERIMENT_ID = "a2_calibration_shadow_20261021"
PROTOCOL_VERSION = "a2_calibration_shadow_protocol_v1"
REFERENCE_PR_HEAD = "eec591921367cd81736e9feba1b52011657f1512"
STARTING_MAIN_SHA = "b6b7e506687172a0b08441bb006013d131a19fcd"
DEFAULT_SEED = 20261021
DEFAULT_REPS = 1000
SUBGROUP_REPS = 500
SELECTION_LOG_LOSS_TOLERANCE = 0.0002

VALIDATION_FOLDS: tuple[dict[str, int | str], ...] = (
    {
        "name": "2021",
        "train_start": 2019,
        "train_end": 2020,
        "eval_year": 2021,
    },
    {
        "name": "2022",
        "train_start": 2019,
        "train_end": 2021,
        "eval_year": 2022,
    },
)

EXPECTED_BASELINE_TEST = {
    "predicted": 36512,
    "accuracy": 0.6040479842243646,
    "brier": 0.23535950080287593,
    "log_loss": 0.6654477796981432,
    "ece": 0.029270838902278697,
}

REQUIRED_RECORD_FIELDS = (
    "match_id",
    "split",
    "season_year",
    "predicted",
    "probability",
    "selection_side",
    "winner_side",
    "y",
    "surface",
    "tour_scope",
    "competition_tier",
    "history_bucket",
    "inactivity_bucket",
    "qualifying_bucket",
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    seen: set[int] = set()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if "_manifest" in raw:
                continue
            missing = [field for field in REQUIRED_RECORD_FIELDS if field not in raw]
            if missing:
                raise ValueError(f"record on line {line_number} is missing {missing}")
            match_id = int(raw["match_id"])
            if match_id in seen:
                raise ValueError(f"duplicate match_id {match_id} in {path}")
            seen.add(match_id)
            if raw["split"] not in ("validation", "test"):
                raise ValueError(f"unexpected split {raw['split']!r}")
            season = int(raw["season_year"])
            if raw["split"] == "validation" and not (2019 <= season <= 2022):
                raise ValueError(f"validation season {season} outside 2019-2022")
            if raw["split"] == "test" and not (2023 <= season <= 2025):
                raise ValueError(f"test season {season} outside 2023-2025")
            if bool(raw["predicted"]):
                probability = float(raw["probability"])
                if not (0.5 <= probability <= 1.0):
                    raise ValueError(
                        f"match {match_id} has selected-side probability {probability}"
                    )
                if raw["selection_side"] not in (1, 2):
                    raise ValueError(f"match {match_id} has invalid selection_side")
                if raw["winner_side"] not in (1, 2):
                    raise ValueError(f"match {match_id} has invalid winner_side")
                expected_outcome = int(raw["selection_side"] == raw["winner_side"])
                if int(raw["y"]) != expected_outcome:
                    raise ValueError(f"match {match_id} outcome disagrees with side")
            else:
                if raw["probability"] is not None or raw["y"] is not None:
                    raise ValueError(f"abstained match {match_id} carries a probability")
            records.append(raw)
    return records


def _predicted(records: Iterable[dict]) -> list[dict]:
    return [record for record in records if bool(record["predicted"])]


def _season_rows(records: Iterable[dict], start: int, end: int) -> list[dict]:
    return [
        record
        for record in records
        if start <= int(record["season_year"]) <= end
    ]


def _xy(records: Sequence[dict]) -> tuple[list[float], list[int]]:
    return (
        [float(record["probability"]) for record in records],
        [int(record["y"]) for record in records],
    )


def _population_sha256(records: Sequence[dict]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: int(item["match_id"])):
        digest.update(
            _canonical(
                {
                    "match_id": int(record["match_id"]),
                    "split": record["split"],
                    "season_year": int(record["season_year"]),
                    "probability": record["probability"],
                    "selection_side": record["selection_side"],
                    "winner_side": record["winner_side"],
                    "y": record["y"],
                    "surface": record["surface"],
                    "competition_tier": record["competition_tier"],
                }
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _population_summary(records: Sequence[dict]) -> dict:
    if not records:
        return {"n": 0}
    seasons = Counter(int(record["season_year"]) for record in records)
    cohorts = [record["cohort"] for record in records if record.get("cohort")]
    match_dates = [record["match_date"] for record in records if record.get("match_date")]
    return {
        "n": len(records),
        "seasons": dict(sorted(seasons.items())),
        "first_cohort": min(cohorts) if cohorts else None,
        "last_cohort": max(cohorts) if cohorts else None,
        "first_match_date": min(match_dates) if match_dates else None,
        "last_match_date": max(match_dates) if match_dates else None,
        "population_sha256": _population_sha256(records),
    }


def _evaluate_candidate(
    name: str,
    train_records: Sequence[dict],
    eval_records: Sequence[dict],
) -> dict:
    train_probabilities, train_outcomes = _xy(train_records)
    samples = list(zip(train_probabilities, train_outcomes))
    model = fit_calibrator(name, samples)
    eval_probabilities, eval_outcomes = _xy(eval_records)
    calibrated = [model.predict(probability) for probability in eval_probabilities]
    baseline_metrics = point_metrics(eval_probabilities, eval_outcomes)
    calibrated_metrics = point_metrics(calibrated, eval_outcomes)
    return {
        "model": model,
        "baseline": baseline_metrics,
        "calibrated": calibrated_metrics,
        "delta": {
            "brier": calibrated_metrics["brier"] - baseline_metrics["brier"],
            "log_loss": calibrated_metrics["log_loss"] - baseline_metrics["log_loss"],
            "ece": calibrated_metrics["ece"] - baseline_metrics["ece"],
            "accuracy": calibrated_metrics["accuracy"] - baseline_metrics["accuracy"],
        },
        "calibrated_probabilities": calibrated,
    }


def run_selection(
    records: Sequence[dict],
    *,
    reps: int = DEFAULT_REPS,
    seed: int = DEFAULT_SEED,
) -> dict:
    predicted = _predicted(records)
    fold_rows: list[tuple[dict, list[dict], list[dict]]] = []
    for fold in VALIDATION_FOLDS:
        train = _season_rows(
            predicted,
            int(fold["train_start"]),
            int(fold["train_end"]),
        )
        evaluation = _season_rows(predicted, int(fold["eval_year"]), int(fold["eval_year"]))
        if not train or not evaluation:
            raise ValueError(f"validation fold {fold['name']} is empty")
        fold_rows.append((fold, train, evaluation))

    results: dict[str, dict] = {}
    for name in CANDIDATE_NAMES:
        fold_results: list[dict] = []
        pooled_baseline: list[float] = []
        pooled_outcomes: list[int] = []
        pooled_calibrated: list[float] = []
        for fold, train, evaluation in fold_rows:
            evaluated = _evaluate_candidate(name, train, evaluation)
            eval_probabilities, eval_outcomes = _xy(evaluation)
            pooled_baseline.extend(eval_probabilities)
            pooled_outcomes.extend(eval_outcomes)
            pooled_calibrated.extend(evaluated["calibrated_probabilities"])
            fold_results.append(
                {
                    "fold": fold["name"],
                    "train_years": [int(fold["train_start"]), int(fold["train_end"])],
                    "train_n": len(train),
                    "eval_n": len(evaluation),
                    "model": evaluated["model"].to_dict(),
                    "baseline": evaluated["baseline"],
                    "calibrated": evaluated["calibrated"],
                    "delta": evaluated["delta"],
                }
            )
        pooled_baseline_metrics = point_metrics(pooled_baseline, pooled_outcomes)
        pooled_calibrated_metrics = point_metrics(pooled_calibrated, pooled_outcomes)
        results[name] = {
            "complexity_rank": COMPLEXITY_RANK[name],
            "folds": fold_results,
            "pooled": {
                "n": len(pooled_outcomes),
                "baseline": pooled_baseline_metrics,
                "calibrated": pooled_calibrated_metrics,
                "delta": {
                    "brier": pooled_calibrated_metrics["brier"]
                    - pooled_baseline_metrics["brier"],
                    "log_loss": pooled_calibrated_metrics["log_loss"]
                    - pooled_baseline_metrics["log_loss"],
                    "ece": pooled_calibrated_metrics["ece"]
                    - pooled_baseline_metrics["ece"],
                    "accuracy": pooled_calibrated_metrics["accuracy"]
                    - pooled_baseline_metrics["accuracy"],
                },
                "paired": paired_bootstrap(
                    pooled_baseline,
                    pooled_calibrated,
                    pooled_outcomes,
                    reps=min(reps, 500),
                    seed=seed,
                ),
            },
        }

    identity = results["identity"]["pooled"]["calibrated"]
    eligible = [name for name in CANDIDATE_NAMES if name != "identity"]
    best_name = min(
        eligible,
        key=lambda name: results[name]["pooled"]["calibrated"]["log_loss"],
    )
    best_log_loss = results[best_name]["pooled"]["calibrated"]["log_loss"]
    near_best = [
        name
        for name in eligible
        if results[name]["pooled"]["calibrated"]["log_loss"]
        <= best_log_loss + SELECTION_LOG_LOSS_TOLERANCE
    ]
    selected = min(near_best, key=lambda name: COMPLEXITY_RANK[name])

    improvement_over_identity = identity["log_loss"] - results[selected]["pooled"]["calibrated"]["log_loss"]
    brier_improves = results[selected]["pooled"]["calibrated"]["brier"] < identity["brier"]
    ece_improves = results[selected]["pooled"]["calibrated"]["ece"] < identity["ece"]
    if improvement_over_identity <= 1e-6 or not brier_improves or not ece_improves:
        selected = "identity"

    refit_rows = _season_rows(predicted, 2019, 2022)
    refit_probabilities, refit_outcomes = _xy(refit_rows)
    refit_model = fit_calibrator(
        selected,
        list(zip(refit_probabilities, refit_outcomes)),
    )

    return {
        "protocol_version": PROTOCOL_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "created_utc": _utc_now(),
        "test_outcomes_used": False,
        "selection_rule": {
            "fit_window": "2019-2021 for the 2022 fold; 2019-2020 for the 2021 fold",
            "validation_window": "2021 and 2022 seasons",
            "primary_metric": "pooled log loss",
            "log_loss_tolerance": SELECTION_LOG_LOSS_TOLERANCE,
            "tie_break": "lowest complexity rank",
            "complexity_rank": COMPLEXITY_RANK,
            "requires_brier_improvement": True,
            "requires_ece_improvement": True,
        },
        "folds": [
            {
                "name": fold["name"],
                "train_years": [int(fold["train_start"]), int(fold["train_end"])],
                "eval_year": int(fold["eval_year"]),
                "train_n": len(train),
                "eval_n": len(evaluation),
            }
            for fold, train, evaluation in fold_rows
        ],
        "candidates": results,
        "best_validation_log_loss_candidate": best_name,
        "within_tolerance": near_best,
        "selected_method": selected,
        "selected_method_improvement_over_identity_log_loss": improvement_over_identity,
        "refit_window": "2019-2022",
        "refit_n": len(refit_rows),
        "refit_model": refit_model.to_dict(),
        "baseline_validation_population": _population_summary(
            _season_rows(predicted, 2019, 2022)
        ),
    }


def _subgroup_analysis(
    rows: Sequence[dict],
    baseline: Sequence[float],
    calibrated: Sequence[float],
    *,
    key: str,
    reps: int,
    seed: int,
) -> dict:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(rows):
        grouped[str(record[key])].append(index)
    result: dict[str, dict] = {}
    for value, indices in sorted(grouped.items()):
        base_p = [baseline[index] for index in indices]
        cal_p = [calibrated[index] for index in indices]
        outcomes = [int(rows[index]["y"]) for index in indices]
        base_metrics = point_metrics(base_p, outcomes)
        cal_metrics = point_metrics(cal_p, outcomes)
        paired = paired_bootstrap(
            base_p,
            cal_p,
            outcomes,
            reps=reps,
            seed=seed,
        )
        result[value] = {
            "n": len(indices),
            "small_sample": len(indices) < 200,
            "baseline": base_metrics,
            "calibrated": cal_metrics,
            "paired": paired,
        }
    return result


def _ranking_impact(
    baseline: Sequence[float],
    calibrated: Sequence[float],
) -> dict:
    base = np.asarray(baseline, dtype=float)
    cal = np.asarray(calibrated, dtype=float)
    order = np.argsort(base, kind="stable")
    sorted_cal = cal[order]
    inversions = int((np.diff(sorted_cal) < -1e-12).sum())
    base_ranks = _average_ranks(base)
    cal_ranks = _average_ranks(cal)
    correlation = float(np.corrcoef(base_ranks, cal_ranks)[0, 1])
    return {
        "pairwise_inversions": inversions,
        "spearman_rank_correlation": correlation,
        "unique_baseline_values": len(np.unique(base)),
        "unique_calibrated_values": len(np.unique(cal)),
        "selection_side_changes": 0,
    }


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="stable")
    ranks = np.empty(values.size, dtype=float)
    sorted_values = values[order]
    start = 0
    while start < values.size:
        end = start + 1
        while end < values.size and sorted_values[end] == sorted_values[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0 + 1.0
        start = end
    return ranks


def _manager_impact(
    baseline: Sequence[float],
    calibrated: Sequence[float],
) -> dict:
    base = np.asarray(baseline, dtype=float)
    cal = np.asarray(calibrated, dtype=float)
    delta = cal - base
    return {
        "a2_probability_floor": 0.5,
        "calibrated_below_floor": int((cal < 0.5).sum()),
        "mean_probability_change": float(delta.mean()),
        "min_probability_change": float(delta.min()),
        "max_probability_change": float(delta.max()),
        "p95_absolute_change": float(np.percentile(np.abs(delta), 95)),
        "max_absolute_change": float(np.abs(delta).max()),
        "consensus_mean_shift_if_a1_a3_unchanged": float(delta.mean() / 3.0),
        "spread_change_bound_if_a1_a3_unchanged": float(np.abs(delta).max()),
        "evaluable": False,
        "note": (
            "The frozen A2 record has no A1 or A3 prediction. The three-agent "
            "probability spread and consensus mean cannot be reconstructed; "
            "only the A2 component and a worst-case spread bound are reported."
        ),
    }


def _high_confidence_losses(
    baseline: Sequence[float],
    calibrated: Sequence[float],
    outcomes: Sequence[int],
) -> dict:
    base = np.asarray(baseline, dtype=float)
    cal = np.asarray(calibrated, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    summary = high_confidence_summary(base, cal, y)
    surprise_mask = (base >= 0.9) & (y == 0)
    surprise_count = int(surprise_mask.sum())
    surprise = {
        "n": surprise_count,
        "share_of_0_90_band": (
            surprise_count / int((base >= 0.9).sum())
            if int((base >= 0.9).sum())
            else None
        ),
        "mean_baseline_probability": float(base[surprise_mask].mean())
        if surprise_count
        else None,
        "mean_calibrated_probability": float(cal[surprise_mask].mean())
        if surprise_count
        else None,
    }
    loss_mask = y == 0
    return {
        "bands": summary,
        "surprise_losses_at_0_90": surprise,
        "all_losses": {
            "n": int(loss_mask.sum()),
            "mean_baseline_probability": float(base[loss_mask].mean()),
            "mean_calibrated_probability": float(cal[loss_mask].mean()),
        },
        "interpretation": (
            "Calibration changes the probability assigned to a loss; it does "
            "not change which matches are losses. Directional accuracy is "
            "therefore identical by construction."
        ),
    }


def _conditional_temperature_analysis(
    train: Sequence[dict],
    test: Sequence[dict],
    baseline: Sequence[float],
    global_calibrated: Sequence[float],
    outcomes: Sequence[int],
    *,
    reps: int,
    seed: int,
) -> dict:
    """Exploratory group-specific temperature scaling, fitted on 2019-2022.

    This is not part of the pre-registered selection.  It tests whether a
    global layer is sufficient or whether surface/tier-specific layers merit
    separate research.  Groups with fewer than 500 training rows fall back to
    the global slope to avoid overfitting small cells.
    """

    global_slope = float(
        fit_calibrator(
            "temperature",
            list(zip(*_xy(train))),
        ).parameters["slope"]
    )
    result: dict[str, dict] = {}
    for key in ("competition_tier", "surface", "tour_scope"):
        groups = sorted({str(record[key]) for record in test})
        slopes: dict[str, float] = {}
        for group in groups:
            group_train = [record for record in train if str(record[key]) == group]
            if len(group_train) >= 500:
                model = fit_calibrator(
                    "temperature",
                    list(zip(*_xy(group_train))),
                )
                slopes[group] = float(model.parameters["slope"])
            else:
                slopes[group] = global_slope

        conditional_probabilities = [
            _temperature_predict(probability, slopes[str(record[key])])
            for record, probability in zip(test, baseline)
        ]
        conditional_metrics = point_metrics(conditional_probabilities, outcomes)
        per_group: dict[str, dict] = {}
        for group in groups:
            indices = [
                index for index, record in enumerate(test) if str(record[key]) == group
            ]
            base_p = [baseline[index] for index in indices]
            global_p = [global_calibrated[index] for index in indices]
            conditional_p = [conditional_probabilities[index] for index in indices]
            group_outcomes = [outcomes[index] for index in indices]
            per_group[group] = {
                "n": len(indices),
                "slope": slopes[group],
                "baseline": point_metrics(base_p, group_outcomes),
                "global": point_metrics(global_p, group_outcomes),
                "conditional": point_metrics(conditional_p, group_outcomes),
                "conditional_minus_global": paired_bootstrap(
                    global_p,
                    conditional_p,
                    group_outcomes,
                    reps=reps,
                    seed=seed,
                ),
            }
        result[key] = {
            "global_slope": global_slope,
            "slopes": slopes,
            "overall": {
                "baseline": point_metrics(baseline, outcomes),
                "global": point_metrics(global_calibrated, outcomes),
                "conditional": conditional_metrics,
                "conditional_minus_global": paired_bootstrap(
                    global_calibrated,
                    conditional_probabilities,
                    outcomes,
                    reps=reps,
                    seed=seed,
                ),
            },
            "groups": per_group,
        }
    return result


def _temperature_predict(probability: float, slope: float) -> float:
    return sigmoid(slope * logit(float(probability)))


def _calibration_curves(
    baseline: Sequence[float],
    calibrated: Sequence[float],
    outcomes: Sequence[int],
) -> dict:
    base_metrics = point_metrics(baseline, outcomes)
    cal_metrics = point_metrics(calibrated, outcomes)
    return {
        "baseline": base_metrics["reliability"],
        "selected": cal_metrics["reliability"],
        "baseline_cumulative": base_metrics["cumulative"],
        "selected_cumulative": cal_metrics["cumulative"],
    }


def run_test(
    records: Sequence[dict],
    selection: dict,
    *,
    reps: int = DEFAULT_REPS,
    seed: int = DEFAULT_SEED,
) -> dict:
    if selection.get("test_outcomes_used") is not False:
        raise ValueError("selection file does not certify that test outcomes were unused")
    selected_name = str(selection["selected_method"])

    predicted = _predicted(records)
    train = _season_rows(predicted, 2019, 2022)
    test = _season_rows(predicted, 2023, 2025)
    validation_2022 = _season_rows(predicted, 2022, 2022)
    train_2019_2021 = _season_rows(predicted, 2019, 2021)

    train_ids = {int(record["match_id"]) for record in train}
    test_ids = {int(record["match_id"]) for record in test}
    if train_ids & test_ids:
        raise ValueError("fit and test populations overlap")

    train_probabilities, train_outcomes = _xy(train)
    test_probabilities, test_outcomes = _xy(test)
    selected_model = fit_calibrator(
        selected_name,
        list(zip(train_probabilities, train_outcomes)),
    )
    calibrated_test = [
        selected_model.predict(probability) for probability in test_probabilities
    ]
    baseline_metrics = point_metrics(test_probabilities, test_outcomes)
    calibrated_metrics = point_metrics(calibrated_test, test_outcomes)

    baseline_matches_report = _baseline_matches_pr239(baseline_metrics)

    paired = paired_bootstrap(
        test_probabilities,
        calibrated_test,
        test_outcomes,
        reps=reps,
        seed=seed,
    )
    baseline_intervals = bootstrap_intervals(
        test_probabilities,
        test_outcomes,
        reps=reps,
        seed=seed,
    )
    calibrated_intervals = bootstrap_intervals(
        calibrated_test,
        test_outcomes,
        reps=reps,
        seed=seed,
    )

    by_season: dict[str, dict] = {}
    for season in (2023, 2024, 2025):
        indices = [
            index
            for index, record in enumerate(test)
            if int(record["season_year"]) == season
        ]
        base_p = [test_probabilities[index] for index in indices]
        cal_p = [calibrated_test[index] for index in indices]
        outcomes = [test_outcomes[index] for index in indices]
        by_season[str(season)] = {
            "n": len(indices),
            "baseline": point_metrics(base_p, outcomes),
            "calibrated": point_metrics(cal_p, outcomes),
            "paired": paired_bootstrap(
                base_p,
                cal_p,
                outcomes,
                reps=reps,
                seed=seed,
            ),
        }

    subgroups: dict[str, dict] = {}
    for key in (
        "surface",
        "competition_tier",
        "tour_scope",
        "season_year",
        "history_bucket",
        "inactivity_bucket",
        "qualifying_bucket",
    ):
        subgroups[key] = _subgroup_analysis(
            test,
            test_probabilities,
            calibrated_test,
            key=key,
            reps=SUBGROUP_REPS,
            seed=seed,
        )

    validation_2022_model = fit_calibrator(
        selected_name,
        list(zip(*_xy(train_2019_2021))),
    )
    validation_2022_probabilities, validation_2022_outcomes = _xy(validation_2022)
    validation_2022_calibrated = [
        validation_2022_model.predict(probability)
        for probability in validation_2022_probabilities
    ]

    stability_model = fit_calibrator(
        selected_name,
        list(zip(*_xy(train_2019_2021))),
    )
    stability_calibrated = [
        stability_model.predict(probability) for probability in test_probabilities
    ]

    candidate_reps = min(reps, 500)
    candidate_test: dict[str, dict] = {}
    for name in CANDIDATE_NAMES:
        evaluated = _evaluate_candidate(name, train, test)
        if name == selected_name:
            candidate_paired = paired
        else:
            candidate_paired = paired_bootstrap(
                test_probabilities,
                evaluated["calibrated_probabilities"],
                test_outcomes,
                reps=candidate_reps,
                seed=seed,
            )
        candidate_test[name] = {
            "model": evaluated["model"].to_dict(),
            "baseline": evaluated["baseline"],
            "calibrated": evaluated["calibrated"],
            "delta": evaluated["delta"],
            "paired": candidate_paired,
            "paired_reps": reps if name == selected_name else candidate_reps,
        }

    conditional_exploratory = _conditional_temperature_analysis(
        train,
        test,
        test_probabilities,
        calibrated_test,
        test_outcomes,
        reps=min(reps, 500),
        seed=seed,
    )

    leakage = {
        "fit_years": [2019, 2022],
        "test_years": [2023, 2025],
        "fit_test_year_overlap": False,
        "test_rows_in_fit": 0,
        "test_outcomes_used_for_selection": False,
        "selection_file_certifies_unused_test": selection.get("test_outcomes_used") is False,
        "duplicate_match_ids": 0,
        "baseline_matches_pr239": baseline_matches_report,
        "monotone_mapping_check": _monotone_check(selected_model),
        "selection_side_changes": 0,
        "records_sha256": _records_sha256(predicted),
        "test_population_sha256": _population_sha256(test),
    }

    analysis = {
        "experiment_id": EXPERIMENT_ID,
        "protocol_version": PROTOCOL_VERSION,
        "created_utc": _utc_now(),
        "selected_method": selected_name,
        "selected_model": selected_model.to_dict(),
        "selected_model_refit_n": len(train),
        "populations": {
            "fit_2019_2022": _population_summary(train),
            "validation_2022": _population_summary(validation_2022),
            "test_2023_2025": _population_summary(test),
            "test_selected": _population_summary(test),
            "test_eligible": _population_summary(
                [record for record in records if record["split"] == "test"]
            ),
        },
        "baseline_test": baseline_metrics,
        "calibrated_test": calibrated_metrics,
        "paired_test": paired,
        "baseline_intervals": baseline_intervals,
        "calibrated_intervals": calibrated_intervals,
        "by_season": by_season,
        "subgroups": subgroups,
        "threshold_effects": threshold_effects(
            test_probabilities,
            calibrated_test,
        ),
        "high_confidence": _high_confidence_losses(
            test_probabilities,
            calibrated_test,
            test_outcomes,
        ),
        "cox_calibration": {
            "baseline": cox_calibration(test_probabilities, test_outcomes),
            "calibrated": cox_calibration(calibrated_test, test_outcomes),
        },
        "ranking": _ranking_impact(test_probabilities, calibrated_test),
        "manager": _manager_impact(test_probabilities, calibrated_test),
        "coverage": {
            "eligible_test_matches": len(
                [record for record in records if record["split"] == "test"]
            ),
            "predicted_test_matches": len(test),
            "abstained_test_matches": len(
                [record for record in records if record["split"] == "test"]
            )
            - len(test),
            "raw_prediction_coverage": len(test)
            / len([record for record in records if record["split"] == "test"]),
            "raw_prediction_coverage_changed": False,
            "threshold_display_coverage": threshold_effects(
                test_probabilities,
                calibrated_test,
            ),
        },
        "validation_2022": {
            "n": len(validation_2022),
            "model_fit_years": [2019, 2021],
            "model": validation_2022_model.to_dict(),
            "baseline": point_metrics(
                validation_2022_probabilities,
                validation_2022_outcomes,
            ),
            "calibrated": point_metrics(
                validation_2022_calibrated,
                validation_2022_outcomes,
            ),
            "paired": paired_bootstrap(
                validation_2022_probabilities,
                validation_2022_calibrated,
                validation_2022_outcomes,
                reps=reps,
                seed=seed,
            ),
        },
        "stability_fit_2019_2021": {
            "model": stability_model.to_dict(),
            "test_baseline": baseline_metrics,
            "test_calibrated": point_metrics(
                stability_calibrated,
                test_outcomes,
            ),
            "paired": paired_bootstrap(
                test_probabilities,
                stability_calibrated,
                test_outcomes,
                reps=reps,
                seed=seed,
            ),
        },
        "candidate_test": candidate_test,
        "exploratory_conditional": conditional_exploratory,
        "calibration_curves": {
            "validation_2022": _calibration_curves(
                validation_2022_probabilities,
                validation_2022_calibrated,
                validation_2022_outcomes,
            ),
            "test": _calibration_curves(
                test_probabilities,
                calibrated_test,
                test_outcomes,
            ),
        },
        "leakage_checks": leakage,
    }
    return analysis


def _baseline_matches_pr239(metrics: dict) -> dict:
    checks: dict[str, dict] = {}
    metric_keys = {
        "predicted": "n",
        "accuracy": "accuracy",
        "brier": "brier",
        "log_loss": "log_loss",
        "ece": "ece",
    }
    for expected_key, metric_key in metric_keys.items():
        actual = metrics[metric_key]
        expected = EXPECTED_BASELINE_TEST[expected_key]
        tolerance = 0.0 if expected_key == "predicted" else 1e-6
        checks[expected_key] = {
            "actual": actual,
            "expected": expected,
            "absolute_difference": abs(float(actual) - float(expected)),
            "tolerance": tolerance,
            "pass": abs(float(actual) - float(expected)) <= tolerance,
        }
    return {
        "pass": all(item["pass"] for item in checks.values()),
        "checks": checks,
    }


def _monotone_check(model: CalibrationModel) -> dict:
    grid = [0.5 + index * 0.001 for index in range(501)]
    values = [model.predict(confidence) for confidence in grid]
    nondecreasing = all(
        right >= left - 1e-12 for left, right in pairwise(values)
    )
    within_bounds = all(0.5 <= value <= 1.0 for value in values)
    return {
        "grid_points": len(grid),
        "nondecreasing": nondecreasing,
        "within_0_5_to_1": within_bounds,
        "minimum": min(values),
        "maximum": max(values),
    }


def _records_sha256(records: Sequence[dict]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: int(item["match_id"])):
        digest.update(_canonical(record).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _format(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _format_ci(interval: Sequence[float] | None, digits: int = 4) -> str:
    if interval is None:
        return "n/a"
    return f"{interval[0]:.{digits}f} to {interval[1]:.{digits}f}"


def write_tables(analysis: dict, selection: dict, path: Path) -> None:
    lines: list[str] = []
    lines.append("# Agent 2 Calibration Shadow - Generated Tables")
    lines.append("")
    lines.append(f"- Experiment: `{analysis['experiment_id']}`")
    lines.append(f"- Selected calibrator: `{analysis['selected_method']}`")
    lines.append(
        f"- Selection protocol: {selection['protocol_version']} "
        f"(test outcomes used: {selection['test_outcomes_used']})"
    )
    lines.append("")

    lines.append("## Populations")
    lines.append("")
    lines.append("| Population | n | Seasons | First cohort | Last cohort |")
    lines.append("| --- | ---: | --- | --- | --- |")
    for name, summary in analysis["populations"].items():
        lines.append(
            f"| {name} | {summary.get('n', 0)} | {summary.get('seasons', {})} | "
            f"{summary.get('first_cohort')} | {summary.get('last_cohort')} |"
        )
    lines.append("")

    lines.append("## Validation candidate selection")
    lines.append("")
    lines.append("| Candidate | Rank | Pooled n | Brier | Log loss | ECE | Accuracy | Delta log loss vs identity |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    identity_ll = selection["candidates"]["identity"]["pooled"]["calibrated"]["log_loss"]
    for name in CANDIDATE_NAMES:
        pooled = selection["candidates"][name]["pooled"]
        metrics = pooled["calibrated"]
        lines.append(
            f"| {name} | {selection['candidates'][name]['complexity_rank']} | "
            f"{pooled['n']} | {_format(metrics['brier'])} | "
            f"{_format(metrics['log_loss'])} | {_format(metrics['ece'])} | "
            f"{_format(metrics['accuracy'])} | "
            f"{_format(metrics['log_loss'] - identity_ll, 6)} |"
        )
    lines.append("")
    if selection["selected_method"] == "identity":
        lines.append(
            "Selected `identity` because no calibration candidate improved "
            "pooled validation log loss, Brier and ECE together."
        )
    else:
        lines.append(
            f"Selected `{selection['selected_method']}` because it was within "
            f"{selection['selection_rule']['log_loss_tolerance']} pooled log loss of the "
            f"best candidate (`{selection['best_validation_log_loss_candidate']}`) and "
            "has the lowest complexity rank among tied candidates."
        )
    lines.append("")

    lines.append("## Test: baseline versus selected calibrator")
    lines.append("")
    lines.append("| Metric | Baseline | Baseline 95% CI | Selected | Selected 95% CI | Paired delta | Paired 95% CI |")
    lines.append("| --- | ---: | --- | ---: | --- | ---: | --- |")
    for metric in ("accuracy", "brier", "log_loss", "ece"):
        base = analysis["baseline_test"][metric]
        cal = analysis["calibrated_test"][metric]
        base_ci = analysis["baseline_intervals"].get(metric)
        cal_ci = analysis["calibrated_intervals"].get(metric)
        delta = analysis["paired_test"]["delta"][metric]
        delta_ci = analysis["paired_test"]["delta_ci"][metric]
        lines.append(
            f"| {metric} | {_format(base)} | {_format_ci(base_ci)} | "
            f"{_format(cal)} | {_format_ci(cal_ci)} | {_format(delta, 6)} | "
            f"{_format_ci(delta_ci, 6)} |"
        )
    lines.append("")
    lines.append(
        "A negative paired delta for Brier, log loss or ECE is an improvement. "
        "Accuracy is unchanged because the calibrator is monotone and constrained "
        "to keep the selected side."
    )
    lines.append("")

    lines.append("## Test: reliability by cumulative confidence band")
    lines.append("")
    lines.append("| Method | Band | n | Mean predicted | Observed | Gap | 95% Wilson |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
    for method_key, label in (("baseline_test", "baseline"), ("calibrated_test", "selected")):
        for band in analysis[method_key]["cumulative"]:
            lines.append(
                f"| {label} | >= {band['threshold']:.2f} | {band['n']} | "
                f"{_format(band['mean_probability'])} | {_format(band['observed'])} | "
                f"{_format(band['gap'])} | {_format_ci(band['wilson'])} |"
            )
    lines.append("")

    lines.append("## Test: reliability by 5-point bin")
    lines.append("")
    lines.append("| Method | Bin | n | Mean predicted | Observed | Gap |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
    for method_key, label in (("baseline_test", "baseline"), ("calibrated_test", "selected")):
        for bucket in analysis[method_key]["reliability"]:
            if not bucket["n"]:
                continue
            lines.append(
                f"| {label} | {bucket['lower']:.2f}-{bucket['upper']:.2f} | "
                f"{bucket['n']} | {_format(bucket['mean_probability'])} | "
                f"{_format(bucket['observed'])} | {_format(bucket['gap'])} |"
            )
    lines.append("")

    lines.append("## Test: threshold crossings")
    lines.append("")
    lines.append("| Threshold | Baseline n | Selected n | Delta | Baseline share | Selected share | Selected threshold preserving baseline n |")
    lines.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for effect in analysis["threshold_effects"]:
        lines.append(
            f"| {effect['threshold']:.2f} | {effect['baseline_count']} | "
            f"{effect['calibrated_count']} | {effect['delta_count']} | "
            f"{_format(effect['baseline_share'])} | "
            f"{_format(effect['calibrated_share'])} | "
            f"{_format(effect['calibrated_threshold_for_baseline_count'])} |"
        )
    lines.append("")

    lines.append("## Test: high-confidence bands")
    lines.append("")
    lines.append("| Band | n | Wins | Losses | Baseline mean | Selected mean | Observed | Baseline gap | Selected gap |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for band in analysis["high_confidence"]["bands"]:
        if not band.get("n"):
            continue
        lines.append(
            f"| >= {band['threshold']:.2f} | {band['n']} | {band['wins']} | "
            f"{band['losses']} | {_format(band['baseline_mean'])} | "
            f"{_format(band['calibrated_mean'])} | {_format(band['observed'])} | "
            f"{_format(band['baseline_gap'])} | {_format(band['calibrated_gap'])} |"
        )
    lines.append("")

    lines.append("## Test: subgroup Brier and ECE")
    lines.append("")
    lines.append("| Dimension | Group | n | Baseline Brier | Selected Brier | Delta Brier | Baseline ECE | Selected ECE | Delta ECE | Small sample |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for dimension, groups in analysis["subgroups"].items():
        for group, item in groups.items():
            lines.append(
                f"| {dimension} | {group} | {item['n']} | "
                f"{_format(item['baseline']['brier'])} | "
                f"{_format(item['calibrated']['brier'])} | "
                f"{_format(item['paired']['delta']['brier'], 6)} | "
                f"{_format(item['baseline']['ece'])} | "
                f"{_format(item['calibrated']['ece'])} | "
                f"{_format(item['paired']['delta']['ece'], 6)} | "
                f"{_format(item['small_sample'])} |"
            )
    lines.append("")

    lines.append("## Test: product and ranking impact")
    lines.append("")
    ranking = analysis["ranking"]
    manager = analysis["manager"]
    coverage = analysis["coverage"]
    lines.append(f"- Predicted winners changed: `{ranking['selection_side_changes']}`")
    lines.append(f"- Pairwise ranking inversions: `{ranking['pairwise_inversions']}`")
    lines.append(
        f"- Spearman rank correlation (raw versus calibrated): "
        f"`{_format(ranking['spearman_rank_correlation'], 6)}`"
    )
    lines.append(
        f"- A2 calibrated probabilities below the Manager 0.5 floor: "
        f"`{manager['calibrated_below_floor']}`"
    )
    lines.append(
        f"- Mean A2 probability change: `{_format(manager['mean_probability_change'])}`; "
        f"max absolute change: `{_format(manager['max_absolute_change'])}`"
    )
    lines.append(
        f"- Consensus mean shift if A1/A3 are unchanged: "
        f"`{_format(manager['consensus_mean_shift_if_a1_a3_unchanged'])}`"
    )
    lines.append(
        f"- Three-agent spread change bound if A1/A3 are unchanged: "
        f"`{_format(manager['spread_change_bound_if_a1_a3_unchanged'])}`"
    )
    lines.append(
        f"- Raw prediction coverage: `{_format(coverage['raw_prediction_coverage'])}` "
        f"({coverage['predicted_test_matches']}/{coverage['eligible_test_matches']}); "
        "unchanged by calibration."
    )
    lines.append("")

    lines.append("## Leakage checks")
    lines.append("")
    for key, value in analysis["leakage_checks"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")

    lines.append("## Exploratory test comparison of all candidates")
    lines.append("")
    lines.append("| Candidate | Brier | Log loss | ECE | Delta Brier vs baseline | Delta log loss vs baseline | Delta ECE vs baseline |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for name in CANDIDATE_NAMES:
        item = analysis["candidate_test"][name]
        metrics = item["calibrated"]
        lines.append(
            f"| {name} | {_format(metrics['brier'])} | {_format(metrics['log_loss'])} | "
            f"{_format(metrics['ece'])} | {_format(item['delta']['brier'], 6)} | "
            f"{_format(item['delta']['log_loss'], 6)} | {_format(item['delta']['ece'], 6)} |"
        )
    lines.append("")
    lines.append(
        "This table is exploratory. The primary comparison is the pre-registered "
        "selected candidate in the baseline-versus-selected table above."
    )
    lines.append("")

    lines.append("## Exploratory conditional temperature scaling")
    lines.append("")
    lines.append(
        "Group-specific temperature slopes fitted on 2019-2022, with a global-slope "
        "fallback for groups with fewer than 500 training rows. This is not part of "
        "the pre-registered selection."
    )
    lines.append("")
    lines.append("| Dimension | Group | n | Slope | Baseline Brier | Global Brier | Conditional Brier | Baseline ECE | Global ECE | Conditional ECE | Conditional minus global Brier (95% CI) |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for dimension, block in analysis["exploratory_conditional"].items():
        for group, item in block["groups"].items():
            ci = item["conditional_minus_global"]["delta_ci"]["brier"]
            lines.append(
                f"| {dimension} | {group} | {item['n']} | {_format(item['slope'], 3)} | "
                f"{_format(item['baseline']['brier'])} | {_format(item['global']['brier'])} | "
                f"{_format(item['conditional']['brier'])} | "
                f"{_format(item['baseline']['ece'])} | {_format(item['global']['ece'])} | "
                f"{_format(item['conditional']['ece'])} | {_format_ci(ci, 6)} |"
            )
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_reliability_csv(analysis: dict, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["dataset", "method", "bin_lower", "bin_upper", "n", "mean_predicted", "observed", "gap"]
        )
        for dataset, key in (("validation_2022", "validation_2022"), ("test", "test")):
            curves = analysis["calibration_curves"][key]
            for method in ("baseline", "selected"):
                for bucket in curves[method]:
                    writer.writerow(
                        [
                            dataset,
                            method,
                            bucket["lower"],
                            bucket["upper"],
                            bucket["n"],
                            bucket["mean_probability"],
                            bucket["observed"],
                            bucket["gap"],
                        ]
                    )


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_frozen_manifest(path: Path | None) -> dict | None:
    if path is None:
        return None
    if not path.exists():
        return {"path": str(path), "exists": False}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if "_manifest" in raw:
                return {
                    "path": str(path),
                    "exists": True,
                    "manifest": raw["_manifest"],
                }
    return {"path": str(path), "exists": True, "manifest": None}


def _write_run_manifest(
    *,
    artifacts_dir: Path,
    records_path: Path,
    records_sha256: str,
    frozen_manifest: dict | None,
    selection: dict,
    analysis: dict,
    reps: int,
    seed: int,
    command: str,
) -> dict:
    artifacts: dict[str, str] = {}
    for path in sorted(artifacts_dir.glob("*")):
        if path.is_file() and path.name != "run_manifest.json":
            artifacts[path.name] = _sha256_file(path)
    manifest = {
        "experiment_id": EXPERIMENT_ID,
        "protocol_version": PROTOCOL_VERSION,
        "created_utc": _utc_now(),
        "branch": "research/tennis-agent2-calibration-shadow-20261021",
        "starting_main_sha": STARTING_MAIN_SHA,
        "reference_pr_head": REFERENCE_PR_HEAD,
        "code_sha": _git_sha(),
        "command": command,
        "records_path": str(records_path),
        "records_sha256": records_sha256,
        "frozen_dataset": frozen_manifest,
        "bootstrap_reps": reps,
        "subgroup_bootstrap_reps": SUBGROUP_REPS,
        "bootstrap_seed": seed,
        "selection_log_loss_tolerance": SELECTION_LOG_LOSS_TOLERANCE,
        "selected_method": selection["selected_method"],
        "test_outcomes_used_for_selection": False,
        "production_changed": False,
        "database_writes": False,
        "artifacts": artifacts,
        "metrics": {
            "baseline_test": {
                key: analysis["baseline_test"][key]
                for key in ("accuracy", "brier", "log_loss", "ece")
            },
            "calibrated_test": {
                key: analysis["calibrated_test"][key]
                for key in ("accuracy", "brier", "log_loss", "ece")
            },
            "paired_delta": analysis["paired_test"]["delta"],
            "paired_delta_ci": analysis["paired_test"]["delta_ci"],
        },
    }
    write_json(artifacts_dir / "run_manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--output-dir", default=Path("tmp/a2_calibration_shadow"), type=Path)
    parser.add_argument(
        "--artifacts-dir",
        default=Path("research/a2_calibration_shadow/artifacts"),
        type=Path,
    )
    parser.add_argument("--frozen-dataset", default=None, type=Path)
    parser.add_argument("--mode", choices=("select", "test", "all"), default="all")
    parser.add_argument("--reps", default=DEFAULT_REPS, type=int)
    parser.add_argument("--seed", default=DEFAULT_SEED, type=int)
    args = parser.parse_args(argv)

    started = time.time()
    records = load_records(args.records)
    predicted = _predicted(records)
    records_sha256 = _records_sha256(predicted)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)

    selection_path = args.output_dir / "selection.json"
    if args.mode in ("select", "all"):
        selection = run_selection(records, reps=args.reps, seed=args.seed)
        write_json(selection_path, selection)
        write_json(args.artifacts_dir / "selection.json", selection)
        print(f"wrote {selection_path}")
        print(f"selected method: {selection['selected_method']}")
    else:
        if not selection_path.exists():
            raise SystemExit(
                f"HOLD: {selection_path} is missing. Run --mode select before --mode test."
            )
        selection = json.loads(selection_path.read_text(encoding="utf-8"))

    if args.mode in ("test", "all"):
        analysis = run_test(records, selection, reps=args.reps, seed=args.seed)
        if not analysis["leakage_checks"]["baseline_matches_pr239"]["pass"]:
            raise SystemExit(
                "HOLD: baseline records do not reproduce the approved PR #239 metrics"
            )
        frozen_manifest = _load_frozen_manifest(args.frozen_dataset)
        analysis["frozen_dataset"] = frozen_manifest
        analysis["run"] = {
            "records_path": str(args.records),
            "records_sha256": records_sha256,
            "frozen_dataset": frozen_manifest,
            "bootstrap_reps": args.reps,
            "subgroup_bootstrap_reps": SUBGROUP_REPS,
            "bootstrap_seed": args.seed,
            "production_changed": False,
            "database_writes": False,
        }
        write_json(args.artifacts_dir / "analysis.json", analysis)
        write_json(args.artifacts_dir / "candidates_validation.json", selection["candidates"])
        write_json(args.artifacts_dir / "candidates_test.json", analysis["candidate_test"])
        write_json(args.artifacts_dir / "calibration_curves.json", analysis["calibration_curves"])
        write_json(
            args.artifacts_dir / "frozen_manifest.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "starting_main_sha": STARTING_MAIN_SHA,
                "reference_pr_head": REFERENCE_PR_HEAD,
                "records_path": str(args.records),
                "records_sha256": records_sha256,
                "frozen_dataset": frozen_manifest,
                "populations": analysis["populations"],
                "baseline_test": analysis["baseline_test"],
                "baseline_matches_pr239": analysis["leakage_checks"][
                    "baseline_matches_pr239"
                ],
            },
        )
        write_reliability_csv(analysis, args.artifacts_dir / "reliability_test.csv")
        write_tables(analysis, selection, args.artifacts_dir / "tables.md")
        _write_run_manifest(
            artifacts_dir=args.artifacts_dir,
            records_path=args.records,
            records_sha256=records_sha256,
            frozen_manifest=frozen_manifest,
            selection=selection,
            analysis=analysis,
            reps=args.reps,
            seed=args.seed,
            command=" ".join([sys.executable, *sys.argv]),
        )
        print(f"wrote {args.artifacts_dir}")
        print(
            "test baseline: "
            f"brier={analysis['baseline_test']['brier']:.6f} "
            f"log_loss={analysis['baseline_test']['log_loss']:.6f} "
            f"ece={analysis['baseline_test']['ece']:.6f}"
        )
        print(
            "test calibrated: "
            f"brier={analysis['calibrated_test']['brier']:.6f} "
            f"log_loss={analysis['calibrated_test']['log_loss']:.6f} "
            f"ece={analysis['calibrated_test']['ece']:.6f}"
        )
        print(
            "paired delta: "
            f"brier={analysis['paired_test']['delta']['brier']:.6f} "
            f"log_loss={analysis['paired_test']['delta']['log_loss']:.6f} "
            f"ece={analysis['paired_test']['delta']['ece']:.6f}"
        )
    print(f"elapsed_seconds={time.time() - started:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
