from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

from research.a2_calibration_shadow import calibrators, harness, metrics


def _deterministic_samples(true_slope: float = 0.7) -> list[tuple[float, int]]:
    samples: list[tuple[float, int]] = []
    for index in range(21):
        confidence = 0.5 + index * 0.025
        true_probability = calibrators.sigmoid(true_slope * calibrators.logit(confidence))
        wins = round(true_probability * 1000)
        samples.extend([(confidence, 1)] * wins)
        samples.extend([(confidence, 0)] * (1000 - wins))
    return samples


def _synthetic_records(
    *,
    per_season: int = 500,
    seed: int = 123,
) -> list[dict]:
    rng = np.random.default_rng(seed)
    records: list[dict] = []
    match_id = 1
    for season in range(2019, 2026):
        split = "validation" if season <= 2022 else "test"
        for _ in range(per_season):
            confidence = 0.5 + 0.5 * float(rng.random())
            true_probability = calibrators.sigmoid(0.65 * calibrators.logit(confidence))
            outcome = int(rng.random() < true_probability)
            records.append(
                {
                    "match_id": match_id,
                    "split": split,
                    "season_year": season,
                    "predicted": True,
                    "probability": confidence,
                    "selection_side": 1,
                    "winner_side": 1 if outcome else 2,
                    "y": outcome,
                    "surface": "HARD",
                    "tour_scope": "CHALLENGER",
                    "competition_tier": "CHALLENGER",
                    "history_bucket": "50_plus",
                    "inactivity_bucket": "active_lt_180d",
                    "qualifying_bucket": "main_draw",
                    "cohort": f"{season}-06-01T00:00:00+00:00",
                    "match_date": f"{season}-06-01",
                }
            )
            match_id += 1
    return records


def test_temperature_recovers_known_slope() -> None:
    model = calibrators.fit_temperature(_deterministic_samples(0.7))
    slope = float(model.parameters["slope"])
    assert 0.65 <= slope <= 0.75
    grid = [0.5 + index * 0.01 for index in range(51)]
    values = [model.predict(value) for value in grid]
    assert all(0.5 <= value <= 1.0 for value in values)
    assert all(right >= left for left, right in pairwise(values))


def test_platt_stays_on_or_above_half() -> None:
    model = calibrators.fit_platt(_deterministic_samples(0.7))
    assert float(model.parameters["intercept"]) >= 0.0
    assert float(model.parameters["slope"]) > 0.0
    for confidence in (0.5, 0.6, 0.75, 0.9, 1.0):
        assert model.predict(confidence) >= 0.5


def test_isotonic_is_monotone_and_respects_minimum_bin() -> None:
    samples = _deterministic_samples(0.7)
    model = calibrators.fit_isotonic(samples, min_bin=100)
    assert len(model.parameters["values"]) <= len(samples) // 100 + 2
    grid = [0.5 + index * 0.01 for index in range(51)]
    values = [model.predict(value) for value in grid]
    assert all(0.5 <= value <= 1.0 for value in values)
    assert all(right >= left for left, right in pairwise(values))


def test_piecewise_linear_is_monotone() -> None:
    model = calibrators.fit_piecewise_linear(_deterministic_samples(0.7))
    grid = [0.5 + index * 0.01 for index in range(51)]
    values = [model.predict(value) for value in grid]
    assert all(0.5 <= value <= 1.0 for value in values)
    assert all(right >= left for left, right in pairwise(values))


def test_apply_symmetrically_preserves_complement_and_side() -> None:
    model = calibrators.fit_temperature(_deterministic_samples(0.7))
    favourite = calibrators.apply_symmetrically(model, 0.8)
    underdog = calibrators.apply_symmetrically(model, 0.2)
    assert favourite >= 0.5
    assert underdog <= 0.5
    assert favourite + underdog == pytest.approx(1.0)


def test_known_metrics_match_baseline_convention() -> None:
    result = metrics.point_metrics([0.7, 0.8], [1, 0])
    assert result["n"] == 2
    assert result["accuracy"] == pytest.approx(0.5)
    assert result["brier"] == pytest.approx(0.365)
    assert result["ece"] == pytest.approx(0.55)


def test_paired_bootstrap_zero_for_identical_probabilities() -> None:
    result = metrics.paired_bootstrap(
        [0.7, 0.8, 0.6],
        [0.7, 0.8, 0.6],
        [1, 0, 1],
        reps=50,
        seed=7,
    )
    for key in ("brier", "log_loss", "ece", "accuracy"):
        assert result["delta"][key] == pytest.approx(0.0)
        assert result["delta_ci"][key] == pytest.approx([0.0, 0.0])


def test_threshold_effects_find_equivalent_threshold() -> None:
    effects = metrics.threshold_effects(
        [0.95, 0.85, 0.75, 0.65],
        [0.80, 0.72, 0.64, 0.58],
        thresholds=(0.9,),
    )
    assert effects[0]["baseline_count"] == 1
    assert effects[0]["calibrated_count"] == 0
    assert effects[0]["calibrated_threshold_for_baseline_count"] == pytest.approx(0.80)


def test_selection_does_not_read_test_outcomes() -> None:
    records = _synthetic_records(per_season=600)
    first = harness.run_selection(records, reps=10, seed=7)
    flipped = [dict(record) for record in records]
    for record in flipped:
        if record["split"] == "test":
            record["y"] = 1 - int(record["y"])
            record["winner_side"] = 1 if record["y"] else 2
    second = harness.run_selection(flipped, reps=10, seed=7)

    assert first["selected_method"] == second["selected_method"]
    for name in calibrators.CANDIDATE_NAMES:
        assert (
            first["candidates"][name]["pooled"]["calibrated"]["brier"]
            == second["candidates"][name]["pooled"]["calibrated"]["brier"]
        )


def test_end_to_end_synthetic_run_preserves_accuracy_and_coverage() -> None:
    records = _synthetic_records(per_season=600)
    selection = harness.run_selection(records, reps=10, seed=7)
    analysis = harness.run_test(records, selection, reps=10, seed=7)

    assert analysis["selected_method"] == selection["selected_method"]
    assert analysis["baseline_test"]["accuracy"] == pytest.approx(
        analysis["calibrated_test"]["accuracy"]
    )
    assert analysis["paired_test"]["delta"]["accuracy"] == pytest.approx(0.0)
    assert analysis["ranking"]["selection_side_changes"] == 0
    assert analysis["leakage_checks"]["test_rows_in_fit"] == 0
    assert analysis["leakage_checks"]["monotone_mapping_check"]["nondecreasing"] is True
    assert analysis["coverage"]["raw_prediction_coverage_changed"] is False
    assert analysis["calibrated_test"]["brier"] <= analysis["baseline_test"]["brier"]


def test_load_records_rejects_duplicate_match_id(tmp_path: Path) -> None:
    record = _synthetic_records(per_season=1)[0]
    path = tmp_path / "records.jsonl"
    path.write_text(
        json.dumps(record) + "\n" + json.dumps(record) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate match_id"):
        harness.load_records(path)
