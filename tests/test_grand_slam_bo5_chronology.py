"""Chronological Grand Slam BO5 experiment: semantics, leakage and metrics."""
from __future__ import annotations

import json
import math
from datetime import date

import pytest

from ops.validation import grand_slam_bo5_chronology_20261127 as cli
from sports_edge_tennis.validation.grand_slam_bo5_chronology import (
    COMMERCIAL_AUTHORIZED,
    DataRightsError,
    ExperimentConfig,
    ProbabilitySemanticsError,
    SlamMatch,
    apply_calibration,
    brier,
    dataset_from_payload,
    expected_calibration_error,
    fit_blend_weight,
    fit_logistic_calibration,
    implied_set_probability,
    log_loss,
    run_chronological_experiment,
    synthetic_dataset,
    synthetic_validation,
    theoretical_format_adjustment,
)


def _match(index: int, **overrides) -> SlamMatch:
    base = dict(
        match_id=f"m{index}",
        slam="Australian Open",
        season=2022,
        edition_start=date(2022, 1, 17),
        round_code="R128",
        draw_stage="MAIN_DRAW",
        surface="HARD",
        best_of=5,
        winner_is_player_1=True,
        a1_probability=0.62,
        a2_bo5_probability=0.66,
        set_win_probability=0.58,
        data_rights="SYNTHETIC",
        source="TEST",
    )
    base.update(overrides)
    return SlamMatch(**base)


# ---------------------------------------------------------------------------
# Format semantics
# ---------------------------------------------------------------------------


def test_exact_independent_set_identity_at_sixty_percent():
    assert theoretical_format_adjustment(
        0.60,
        source_semantics="SET_WIN_PROBABILITY",
        source_best_of=3,
        target_best_of=3,
    )[0] == pytest.approx(0.648)
    assert theoretical_format_adjustment(
        0.60,
        source_semantics="SET_WIN_PROBABILITY",
        source_best_of=3,
        target_best_of=5,
    )[0] == pytest.approx(0.68256)


def test_transform_is_symmetric_monotone_and_fixes_one_half():
    for p in (0.05, 0.2, 0.4, 0.5, 0.6, 0.8, 0.95):
        bo5, _ = theoretical_format_adjustment(
            p,
            source_semantics="SET_WIN_PROBABILITY",
            source_best_of=3,
            target_best_of=5,
        )
        reverse, _ = theoretical_format_adjustment(
            1 - p,
            source_semantics="SET_WIN_PROBABILITY",
            source_best_of=3,
            target_best_of=5,
        )
        assert bo5 == pytest.approx(1 - reverse, abs=1e-9)
        if p > 0.5:
            assert bo5 > p
        elif p < 0.5:
            assert bo5 < p
        else:
            assert bo5 == pytest.approx(0.5, abs=1e-9)


def test_match_probability_is_rejected_as_set_probability():
    with pytest.raises(ProbabilitySemanticsError, match="not a set-winning"):
        theoretical_format_adjustment(
            0.65,
            source_semantics="MATCH_WIN_PROBABILITY",
            source_best_of=3,
            target_best_of=5,
        )


def test_explicit_assumption_is_labelled_and_round_trips_the_inverse():
    implied = implied_set_probability(0.65, 3)
    adjusted, label = theoretical_format_adjustment(
        0.65,
        source_semantics="MATCH_WIN_PROBABILITY",
        source_best_of=3,
        target_best_of=5,
        allow_iid_assumption=True,
    )
    assert label == "ASSUMPTION_DEPENDENT"
    assert adjusted > 0.65
    assert adjusted == pytest.approx(0.684959, rel=1e-6)
    assert implied == pytest.approx(0.601390, rel=1e-5)


def test_no_arbitrary_favourite_inflation_across_the_input_range():
    previous = -1.0
    for p in (0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
        value, _ = theoretical_format_adjustment(
            p,
            source_semantics="SET_WIN_PROBABILITY",
            source_best_of=3,
            target_best_of=5,
        )
        assert value >= previous
        previous = value
        # The gain is bounded by the exact identity, not a fitted constant.
        assert value - p <= 0.5


# ---------------------------------------------------------------------------
# Dataset contract
# ---------------------------------------------------------------------------


def test_qualifying_is_best_of_three_and_main_draw_is_best_of_five():
    with pytest.raises(ValueError, match="qualifying is best-of-three"):
        _match(1, draw_stage="QUALIFYING", best_of=5)
    with pytest.raises(ValueError, match="best_of must be 3 or 5"):
        _match(1, best_of=4)


def test_duplicate_match_ids_fail_closed():
    from dataclasses import replace

    base = synthetic_dataset(editions=5, matches_per_edition=4)
    duplicated = list(base)
    duplicated[-1] = replace(
        base[-1], match_id=base[0].match_id
    )
    with pytest.raises(ValueError, match="duplicate"):
        run_chronological_experiment(
            duplicated,
            config=ExperimentConfig(min_training_editions=2),
        )


def test_commercial_rights_gate_rejects_non_commercial_sources():
    base = synthetic_dataset(editions=6, matches_per_edition=5)
    with pytest.raises(DataRightsError, match="non-commercial"):
        run_chronological_experiment(
            base,
            config=ExperimentConfig(
                min_training_editions=1, require_commercial_rights=True
            ),
        )
    authorised = [
        SlamMatch(
            match_id=match.match_id,
            slam=match.slam,
            season=match.season,
            edition_start=match.edition_start,
            round_code=match.round_code,
            draw_stage=match.draw_stage,
            surface=match.surface,
            best_of=match.best_of,
            winner_is_player_1=match.winner_is_player_1,
            a1_probability=match.a1_probability,
            a2_bo5_probability=match.a2_bo5_probability,
            a2_bo3_probability=match.a2_bo3_probability,
            set_win_probability=match.set_win_probability,
            manager_c_probability=match.manager_c_probability,
            rating_difference=match.rating_difference,
            matches_last_365=match.matches_last_365,
            days_since_last_match=match.days_since_last_match,
            data_rights=COMMERCIAL_AUTHORIZED,
            source="LICENSED",
        )
        for match in base
    ]
    result = run_chronological_experiment(
        authorised,
        config=ExperimentConfig(
            min_training_editions=1, require_commercial_rights=True
        ),
    )
    assert result["population"] == len(base)


# ---------------------------------------------------------------------------
# Chronology, calibration and blending
# ---------------------------------------------------------------------------


def test_chronological_folds_train_only_on_strictly_earlier_editions():
    dataset = synthetic_dataset(editions=8, matches_per_edition=12)
    result = run_chronological_experiment(
        dataset,
        config=ExperimentConfig(min_training_editions=3, bootstrap_iterations=20),
    )
    assert result["evaluation_editions"] == 5
    for fold in result["folds"]:
        test_start = date.fromisoformat(fold["test_edition"][0])
        for training in fold["training_editions"]:
            assert date.fromisoformat(training[0]) < test_start


def test_calibration_recovers_a_known_slope():
    dataset = synthetic_dataset(editions=8, matches_per_edition=80)
    probabilities = [match.a1_probability for match in dataset]
    labels = [match.label for match in dataset]
    slope, intercept = fit_logistic_calibration(probabilities, labels)
    assert 1.0 < slope < 2.0  # Agent 1 is under-confident in the generator
    assert abs(intercept) < 0.3
    calibrated = [apply_calibration(p, (slope, intercept)) for p in probabilities]
    assert log_loss(calibrated, labels) < log_loss(probabilities, labels)


def test_blend_weight_is_bounded_and_improves_on_the_better_input():
    a = [0.55, 0.60, 0.65, 0.70, 0.72]
    d = [0.62, 0.68, 0.72, 0.78, 0.80]
    labels = [1, 1, 1, 1, 0]
    weight = fit_blend_weight(a, d, labels)
    assert 0.0 <= weight <= 1.0
    assert weight > 0.0


def test_metrics_have_known_values():
    probabilities = [0.5, 0.5]
    labels = [1, 0]
    assert brier(probabilities, labels) == pytest.approx(0.25)
    assert log_loss(probabilities, labels) == pytest.approx(math.log(2))
    assert expected_calibration_error(probabilities, labels) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Synthetic validation and evidence
# ---------------------------------------------------------------------------


def test_synthetic_validation_passes_all_guards():
    result = synthetic_validation()
    assert result["synthetic_validation"]["all_checks_passed"] is True
    assert result["synthetic_validation"]["is_empirical_evidence"] is False
    overall = result["overall"]
    assert overall["A"]["coverage"] == 1.0
    assert overall["B"]["coverage"] < 1.0
    for arm in ("A", "B", "C", "D", "E", "F"):
        assert math.isfinite(overall[arm]["brier"])
        assert 0.0 <= overall[arm]["accuracy"] <= 1.0


def test_paired_bootstrap_is_reproducible():
    dataset = synthetic_dataset(editions=7, matches_per_edition=20)
    first = run_chronological_experiment(
        dataset,
        config=ExperimentConfig(min_training_editions=3, bootstrap_iterations=50),
    )
    second = run_chronological_experiment(
        dataset,
        config=ExperimentConfig(min_training_editions=3, bootstrap_iterations=50),
    )
    assert first["paired_vs_A"]["C"] == second["paired_vs_A"]["C"]
    interval = first["paired_vs_A"]["C"]["log_loss_delta"]
    assert interval["lower_95"] <= interval["upper_95"]


def test_dataset_payload_round_trip():
    dataset = synthetic_dataset(editions=2, matches_per_edition=3)
    payload = {
        "matches": [
            {
                "match_id": match.match_id,
                "slam": match.slam,
                "season": match.season,
                "edition_start": match.edition_start.isoformat(),
                "round_code": match.round_code,
                "draw_stage": match.draw_stage,
                "surface": match.surface,
                "best_of": match.best_of,
                "winner_is_player_1": match.winner_is_player_1,
                "a1_probability": match.a1_probability,
                "data_rights": match.data_rights,
                "source": match.source,
            }
            for match in dataset
        ]
    }
    loaded = dataset_from_payload(payload)
    assert len(loaded) == len(dataset)
    assert loaded[0].slam == dataset[0].slam


def test_cli_refuses_unauthorised_real_data(tmp_path):
    dataset = synthetic_dataset(editions=5, matches_per_edition=5)
    payload = {
        "version": "GRAND_SLAM_BO5_DATASET_V1",
        "rights": {"status": "UNRESOLVED", "license_reference": None},
        "matches": [
            {
                "match_id": match.match_id,
                "slam": match.slam,
                "season": match.season,
                "edition_start": match.edition_start.isoformat(),
                "round_code": match.round_code,
                "draw_stage": match.draw_stage,
                "surface": match.surface,
                "best_of": match.best_of,
                "winner_is_player_1": match.winner_is_player_1,
                "a1_probability": match.a1_probability,
                "data_rights": "NON_COMMERCIAL",
                "source": "SACKMANN",
            }
            for match in dataset
        ],
    }
    input_path = tmp_path / "dataset.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    out_path = tmp_path / "evidence.json"
    code = cli.main(
        [
            "--input-json",
            str(input_path),
            "--require-commercial-rights",
            "--out-json",
            str(out_path),
        ]
    )
    assert code == 3
    evidence = json.loads(out_path.read_text(encoding="utf-8"))
    assert evidence["mode"] == "REAL_DATA_REFUSED"
    assert evidence["blocker"]["status"] == (
        "BLOCKED_NO_COMMERCIALLY_AUTHORIZED_HISTORICAL_CORPUS"
    )


def test_cli_synthetic_evidence_records_the_real_data_blocker(tmp_path):
    out_path = tmp_path / "synthetic.json"
    code = cli.main(
        [
            "--bootstrap-iterations",
            "40",
            "--out-json",
            str(out_path),
        ]
    )
    assert code == 0
    evidence = json.loads(out_path.read_text(encoding="utf-8"))
    assert evidence["mode"] == "SYNTHETIC_VALIDATION_ONLY"
    assert evidence["real_data_status"] == "BLOCKED"
    assert evidence["provider_calls"] == 0
    assert evidence["database_writes"] == 0
    assert evidence["synthetic_validation"]["all_checks_passed"] is True
