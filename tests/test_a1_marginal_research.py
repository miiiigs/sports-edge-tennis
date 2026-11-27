from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from sports_edge_tennis.validation import a1_marginal_research as research

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "research" / "evidence"
POPULATION = EVIDENCE / "a1_selective_population_20261126.csv"


@pytest.fixture(scope="module")
def observations():
    with POPULATION.open(encoding="utf-8", newline="") as handle:
        return research.parse_population(list(csv.DictReader(handle)))


@pytest.fixture(scope="module")
def thresholds(observations):
    return research.development_thresholds(observations)


def test_wilson_and_newcombe_are_sane():
    low, high = research.wilson_interval(50, 100)
    assert abs((low + high) / 2 - 0.5) < 1e-12
    assert research.wilson_interval(0, 0) is None
    identical = research.newcombe_difference(50, 100, 50, 100)
    assert identical["difference"] == 0.0
    assert identical["ci95"][0] < 0 < identical["ci95"][1]


def test_paired_difference_uses_discordant_pairs():
    result = research.paired_proportion_difference(
        [True, True, False, False], [True, False, True, False]
    )
    assert result["difference"] == 0.0
    assert result["discordant_model_only"] == 1
    assert result["discordant_market_only"] == 1
    assert result["mcnemar_p"] == 1.0


def test_fisher_exact_edges():
    assert research.fisher_exact_two_sided(0, 10, 10, 0) < 1e-4
    assert research.fisher_exact_two_sided(5, 5, 5, 5) == pytest.approx(1.0)
    assert research.fisher_exact_two_sided(0, 0, 0, 0) is None


def test_benjamini_hochberg_preserves_none():
    adjusted = research.benjamini_hochberg([0.01, 0.04, 0.03, None])
    assert adjusted[0] == pytest.approx(0.03)
    assert adjusted[2] == pytest.approx(0.04)
    assert adjusted[3] is None


def test_calibration_slope_requires_enough_rows():
    assert research.calibration_slope([0.6] * 10, [1] * 10)["status"] == "INSUFFICIENT"
    slope = research.calibration_slope(
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9] * 5,
        [0, 0, 0, 1, 1, 1, 1, 1, 1] * 5,
    )
    assert slope["status"] in {"OK", "SINGULAR", "NON_FINITE"}


def test_parse_population_keeps_both_histories():
    raw = {
        "prediction_id": "1",
        "match_id": "2",
        "date": "2026-11-01",
        "as_of": "2026-11-01T00:00:00+00:00",
        "match_start_at": "2026-11-01T02:00:00+00:00",
        "tour": "CHALLENGER",
        "competition_tier": "CHALLENGER",
        "round_code": "round_of_16",
        "surface": "HARD",
        "qualifying_flag": "False",
        "season_year": "2026",
        "p1_original": "0.6",
        "p1_corrected": "0.4",
        "winner_side": "2",
        "overall_matches_original_p1": "5",
        "overall_matches_original_p2": "50",
        "overall_matches_corrected_p1": "45",
        "overall_matches_corrected_p2": "50",
        "surface_matches_corrected_p1": "4",
        "surface_matches_corrected_p2": "20",
        "effective_rating_corrected_p1": "1700",
        "effective_rating_corrected_p2": "1500",
        "resolution_p1_live": "10",
        "resolution_p1_model": "11",
        "resolution_p1_method": "ALIAS",
        "resolution_p1_bridged": "True",
    }
    parsed = research.parse_population([raw])[0]
    assert parsed["round_code"] == "R16"
    # p1_original=0.6 selects side 1; winner is 2, so the original is wrong.
    assert parsed["correct_original"] is False
    assert parsed["correct_corrected"] is True
    assert parsed["matches_corrected"]["p1"] == 45.0
    assert parsed["resolution"]["p1"]["bridged"] is True
    assert research.experience_band(45) == "20-49"
    assert research.surface_experience_band(4) == "1-4"


def test_development_thresholds_are_exact(thresholds):
    assert thresholds["development_rows"] == 2198
    assert thresholds["40"]["development_selections"] == 879
    assert thresholds["40"]["threshold"] == pytest.approx(0.683286)
    assert thresholds["45"]["development_selections"] == 989
    assert thresholds["45"]["threshold"] == pytest.approx(0.668060)
    assert thresholds["50"]["development_selections"] == 1099
    assert thresholds["50"]["threshold"] == pytest.approx(0.648708)


def test_frozen_cohorts_reproduce_mission_counts(observations, thresholds):
    cohorts = research.frozen_cohorts(observations, thresholds)
    assert len(cohorts["holdout"]) == 1025
    assert len(cohorts["core_40"]) == 375
    assert len(cohorts["marginal_40_45"]) == 45
    assert sum(1 for r in cohorts["marginal_40_45"] if r["correct_corrected"]) == 27
    assert len(cohorts["base_45"]) == 420
    assert sum(1 for r in cohorts["base_45"] if r["correct_corrected"]) == 321
    assert len(cohorts["marginal_45_50"]) == 56
    assert sum(1 for r in cohorts["marginal_45_50"] if r["correct_corrected"]) == 32
    assert len(cohorts["base_50"]) == 476
    assert sum(1 for r in cohorts["base_50"] if r["correct_corrected"]) == 353


def test_retrospective_bands_are_exact(observations):
    bands = research.retrospective_bands(observations)
    assert bands["cutoffs"] == {"k40": 1289, "k45": 1450, "k50": 1611}
    assert len(bands["band_40_45"]) == 161
    assert sum(1 for r in bands["band_40_45"] if r["correct_corrected"]) == 107
    assert len(bands["band_45_50"]) == 161
    assert sum(1 for r in bands["band_45_50"] if r["correct_corrected"]) == 90


def test_committed_phase1_evidence_matches(observations, thresholds):
    payload = json.loads((EVIDENCE / "a1_marginal_cohorts_20261127.json").read_text())
    assert payload["cohorts"]["marginal_40_45"]["n"] == 45
    assert payload["cohorts"]["marginal_45_50"]["n"] == 56
    assert payload["cohorts"]["marginal_40_45"]["wins"] == 27
    assert payload["cohorts"]["marginal_45_50"]["wins"] == 32
    assert payload["retrospective_bands"]["band_40_45"]["wins"] == 107
    assert payload["retrospective_bands"]["band_45_50"]["wins"] == 90
    assert payload["thresholds"]["45"]["threshold"] == pytest.approx(
        thresholds["45"]["threshold"]
    )
    assert payload["marginal_pool"]["n"] == 101
    assert payload["marginal_pool"]["wins"] == 59


def test_phase2_committed_evidence():
    payload = json.loads((EVIDENCE / "a1_newcomer_favourite_20261127.json").read_text())
    bands = payload["full_settled"]["favourite"]
    assert bands["0-4"]["n"] == 99
    assert bands["0-4"]["wins"] == 33
    assert bands["0-4"]["accuracy"] == pytest.approx(33 / 99)
    assert bands["100+"]["n"] == 2123
    assert payload["information_states"]["FAVOURITE_NO_OBSERVABLE_HISTORY"]["n"] == 36
    assert payload["information_states"]["FAVOURITE_NO_OBSERVABLE_HISTORY"]["wins"] == 3
    bridge = payload["bridge_resolution"]
    assert bridge["original_limited_to_corrected_established_n"] == 83
    assert bridge["original_limited_stayed_limited_n"] == 143


def test_phase3_committed_evidence():
    payload = json.loads((EVIDENCE / "a1_atp_challenger_controlled_20261127.json").read_text())
    assert payload["overall"]["ATP_MAIN"]["n"] == 577
    assert payload["overall"]["CHALLENGER"]["n"] == 2646
    assert payload["crude_difference_ch_minus_atp"] == pytest.approx(
        payload["overall"]["CHALLENGER"]["accuracy"] - payload["overall"]["ATP_MAIN"]["accuracy"]
    )
    assert payload["market_base_50"]["ATP_MAIN"]["n"] == 0
    assert payload["market_full"]["CHALLENGER"]["n"] == 433
    assert payload["market_full"]["selected_rows"] == 501
    assert payload["market_full"]["quoted_any_age"] == 1610


def test_phase4_committed_evidence():
    payload = json.loads((EVIDENCE / "a1_uncertainty_audit_20261127.json").read_text())
    assert payload["research_candidate"]["justified"] is False
    assert payload["holdout_evaluation"]["status"] == "NOT_JUSTIFIED"
    assert payload["reliability_flag"]["probability_changed"] is False
    assert payload["reliability_flag"]["selection_changed"] is False
    assert payload["reliability_flag"]["high_risk_3plus"]["holdout"]["n"] == 110


def test_phase5_committed_evidence():
    payload = json.loads((EVIDENCE / "a1_inactivity_surface_20261127.json").read_text())
    assert payload["bands"]["181d+"]["all"]["accuracy"] > payload["bands"]["0-7d"]["all"]["accuracy"]
    assert payload["surface_transitions"]["coverage"]["settled_rows"] == 3223
    assert payload["surface_transitions"]["full"]["FIRST_ON_NEW_SURFACE"]["n"] == 102
    assert payload["surface_transitions"]["underdog_full"]["FIRST_ON_NEW_SURFACE"]["n"] == 86


def test_phase6_committed_evidence():
    payload = json.loads((EVIDENCE / "a1_identity_player_ledger_20261127.json").read_text())
    assert len(payload["selected"]) == 20
    mandatory = {3029, 3343, 3503, 4143}
    selected = {entry["model_player_id"] for entry in payload["selected"]}
    assert mandatory <= selected
    assert payload["slot_census"]["slots"] == 7552
    assert payload["slot_census"]["method_classified_slots"] == 7552
    assert payload["slot_census"]["resolved_slots"] == 6558
    assert payload["history_attribution"]["unique_matches_in_artifact"] == 16104
    assert payload["provenance"]["new_commercial_training"] is False


def test_slot_census_does_not_treat_fallback_as_resolved():
    rows = [
        {
            "prediction_id": 1,
            "match_id": 10,
            "p1_live": 100,
            "p1_resolution": {"method": "NO_HISTORICAL_CANDIDATE", "resolved_player_id": 100},
            "p2_live": 200,
            "p2_resolution": {"method": "AMBIGUOUS", "resolved_player_id": 200},
        },
        {
            "prediction_id": 2,
            "match_id": 20,
            "p1_live": 300,
            "p1_resolution": {"method": "ALIAS", "resolved_player_id": 301},
            "p2_live": None,
            "p2_resolution": {},
        },
    ]
    census = research.slot_census(rows)
    statuses = [record["status"] for record in census["records"]]
    assert statuses == ["NO_CANDIDATE", "AMBIGUOUS", "RESOLVED", "NO_PLAYER"]
    assert census["method_classified_slots"] == 3
    assert census["resolved_slots"] == 1
    assert census["unresolved_slots"] == 3
    assert census["records"][0]["model_player_id"] is None
    assert census["records"][0]["fallback_player_id"] == 100


def test_identity_sampling_is_outcome_independent(observations, thresholds):
    ledger = {}
    first = research.identity_ledger(observations, ledger, sample_size=20)
    shuffled = [dict(row) for row in observations]
    for row in shuffled:
        row["winner_side"] = 2 if row["winner_side"] == 1 else 1
        row["correct_corrected"] = not row["correct_corrected"]
        row["correct_original"] = not row["correct_original"]
    second = research.identity_ledger(shuffled, ledger, sample_size=20)
    assert [e["model_player_id"] for e in first["selected"]] == [
        e["model_player_id"] for e in second["selected"]
    ]


def test_json_safe_removes_non_finite_values():
    assert research.json_safe(float("nan")) is None
    assert research.json_safe(float("inf")) is None
    assert research.json_safe({"a": [1, math.nan]}) == {"a": [1, None]}
