from __future__ import annotations

import json
from pathlib import Path

import pytest

from sports_edge_tennis.validation import a1_reliability_shadow as reliability

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "research" / "evidence"


def features(**overrides):
    base = {
        "prediction_id": 1,
        "match_id": 2,
        "prediction_as_of": "2026-11-01T00:00:00+00:00",
        "match_start_at": "2026-11-01T02:00:00+00:00",
        "feature_as_of": "2026-11-01T00:00:00+00:00",
        "a1_probability_p1": 0.67,
        "a1_selected_side": 1,
        "effective_rating_gap_abs": 120.0,
        "surface_rating_gap_abs": 90.0,
        "surface_rating_missing": False,
        "rating_divergence_favourite": 40.0,
        "favourite_overall_matches": 50,
        "underdog_overall_matches": 100,
        "min_overall_matches": 50,
        "favourite_surface_matches": 20,
        "min_surface_matches": 20,
        "inactivity_favourite_days": 10.0,
        "inactivity_underdog_days": 12.0,
        "identity_method_favourite": "ALIAS",
        "identity_candidate_count_favourite": 1,
        "identity_any_unresolved": False,
        "identity_any_bridged": True,
        "recovered_matches_favourite": 10,
        "forecast_probability_delta_abs": 0.02,
        "forecast_side_changed": False,
        "surface_transition_state": "CONTINUING",
        "tour": "CHALLENGER",
        "surface": "HARD",
        "round": "R32",
        "qualifying": False,
        "a2_status": "MISSING",
    }
    base.update(overrides)
    return reliability.ReliabilityFeatures(**base)


def test_feature_contract_is_frozen_and_rejects_outcomes():
    contract = reliability.feature_contract()
    names = {feature["name"] for feature in contract["features"]}
    assert "a1_probability_p1" in names
    assert "reliability_score" not in names
    assert "winner_side" in contract["outcome_keys_rejected"]


def test_score_preserves_original_probability_and_side():
    assessment = reliability.score_reliability(features())
    assert assessment.a1_probability_p1 == 0.67
    assert assessment.a1_selected_side == 1
    assert 0.0 <= assessment.reliability_score <= 1.0
    assert assessment.reliability_tier in {"HIGH", "MEDIUM", "LOW"}
    assert assessment.selection_score <= max(0.67, 1 - 0.67)


def test_risk_points_are_deterministic_and_monotone():
    clean = reliability.score_reliability(features())
    risky = reliability.score_reliability(
        features(
            favourite_overall_matches=3,
            min_surface_matches=1,
            identity_method_favourite="NO_HISTORICAL_CANDIDATE",
            identity_any_unresolved=True,
            identity_candidate_count_favourite=0,
            forecast_side_changed=True,
            forecast_probability_delta_abs=0.30,
            rating_divergence_favourite=200.0,
            effective_rating_gap_abs=20.0,
            surface="UNKNOWN",
        )
    )
    assert risky.risk_points > clean.risk_points
    assert risky.reliability_score < clean.reliability_score
    assert risky.reliability_tier == "LOW"


def test_missing_context_contributes_neutral_points():
    present = reliability.score_reliability(features())
    missing = reliability.score_reliability(
        features(inactivity_favourite_days=None, inactivity_underdog_days=None)
    )
    assert missing.risk_points == present.risk_points + 0.5
    assert "inactivity" in missing.missing_features
    surface_missing = reliability.score_reliability(
        features(surface_rating_missing=True, rating_divergence_favourite=0.0)
    )
    assert "surface_rating" in surface_missing.missing_features


def test_capture_is_outcome_free_and_settlement_is_separate():
    capture = reliability.build_capture(features(), captured_at="2026-11-01T00:05:00+00:00")
    reliability.validate_capture(capture)
    assert capture["outcome_included"] is False
    assert "winner_side" not in capture
    assert capture["a1_probability_p1"] == 0.67
    settlement = reliability.settle_capture(
        capture, winner_side=1, settled_at="2026-11-01T03:00:00+00:00"
    )
    assert settlement["a1_correct"] is True
    assert settlement["outcome_included"] is True
    assert capture["outcome_included"] is False


def test_capture_rejects_post_issue_features_and_outcome_keys():
    invalid = features(feature_as_of="2026-11-01T01:00:00+00:00")
    with pytest.raises(reliability.ReliabilityValidationError):
        reliability.score_reliability(invalid)
    capture = reliability.build_capture(features(), captured_at="2026-11-01T00:05:00+00:00")
    tampered = dict(capture)
    tampered["winner_side"] = 1
    with pytest.raises(reliability.ReliabilityValidationError):
        reliability.validate_capture(tampered)


def test_capture_rejects_tampered_snapshot_and_id():
    capture = reliability.build_capture(features(), captured_at="2026-11-01T00:05:00+00:00")
    tampered = dict(capture)
    tampered["feature_snapshot"] = dict(capture["feature_snapshot"])
    tampered["feature_snapshot"]["favourite_overall_matches"] = 1
    with pytest.raises(reliability.ReliabilityValidationError):
        reliability.validate_capture(tampered)
    tampered = dict(capture)
    tampered["capture_id"] = "0" * 64
    with pytest.raises(reliability.ReliabilityValidationError):
        reliability.validate_capture(tampered)


def test_capture_must_precede_match_start():
    with pytest.raises(reliability.ReliabilityValidationError):
        reliability.build_capture(features(), captured_at="2026-11-01T02:00:00+00:00")
    with pytest.raises(reliability.ReliabilityValidationError):
        reliability.build_capture(features(), captured_at="2026-10-31T23:59:00+00:00")


def test_a2_fields_must_be_consistent():
    with pytest.raises(reliability.ReliabilityValidationError):
        reliability.score_reliability(features(a2_status="PRESENT"))
    with pytest.raises(reliability.ReliabilityValidationError):
        reliability.score_reliability(features(a2_status="MISSING", a2_agrees=True))


def test_risk_coverage_known_values():
    rows = [
        {"prediction_id": 1, "match_id": 1, "score": 0.9, "correct": True},
        {"prediction_id": 2, "match_id": 2, "score": 0.8, "correct": False},
        {"prediction_id": 3, "match_id": 3, "score": 0.7, "correct": True},
        {"prediction_id": 4, "match_id": 4, "score": 0.6, "correct": False},
    ]
    result = reliability.risk_coverage(rows, "score")
    assert result["n"] == 4
    assert result["errors"] == 2
    assert result["aurc"] == pytest.approx((0 + 1 / 2 + 1 / 3 + 2 / 4) / 4)
    assert result["augrc_paper"] == pytest.approx((3 - 0.5) / 16 + (1 - 0.5) / 16)


def test_auroc_handles_ties():
    assert reliability.auroc([0.1, 0.2, 0.3, 0.4], [0, 0, 1, 1]) == pytest.approx(1.0)
    assert reliability.auroc([0.5, 0.5, 0.5, 0.5], [0, 0, 1, 1]) == pytest.approx(0.5)
    assert reliability.auroc([0.1], [0]) is None


def test_tier_calibration_is_monotone_on_committed_scores():
    payload = json.loads((EVIDENCE / "a1_reliability_actual_20261127.json").read_text())
    tiers = payload["tier_calibration"]
    assert tiers["HIGH"]["accuracy"] > tiers["MEDIUM"]["accuracy"] > tiers["LOW"]["accuracy"]


def test_committed_actual_evidence_reproduces_exact_counts():
    payload = json.loads((EVIDENCE / "a1_reliability_actual_20261127.json").read_text())
    assert payload["n"] == 3202
    r0 = payload["r0_r1"]["R0_original_confidence"]["points"]
    r1 = payload["r0_r1"]["R1_reliability_adjusted"]["points"]
    assert r0["0.40"]["n"] == 1280
    assert r0["0.40"]["wins"] == 926
    assert r0["0.45"]["wins"] == 1033
    assert r0["0.50"]["wins"] == 1127
    assert r1["0.40"]["wins"] == 928
    assert r1["0.45"]["wins"] == 1038
    assert r1["0.50"]["wins"] == 1132
    assert payload["can_promote"] is False
    assert payload["classification"] == "AUTHORIZED_DEVELOPMENT_REUSED_NOT_INDEPENDENT"


def test_feature_audit_exposes_missing_a2_and_recent_counts():
    payload = json.loads((EVIDENCE / "a1_reliability_feature_audit_20261127.json").read_text())
    assert payload["source_settled_rows"] == 3223
    assert payload["excluded_at_or_after_start"] == 21
    assert payload["settled_rows"] == 3202
    assert payload["availability_share"]["agent2_specialist_support"] == 0.0
    assert payload["availability_share"]["recent_match_counts_90d"] == 0.0
    assert payload["missingness"]["surface_rating_missing"] == 248
    assert payload["missingness"]["inactivity_favourite_missing"] == 38
    assert payload["missingness"]["inactivity_underdog_missing"] == 77


def test_synthetic_suite_has_all_arms_and_gates():
    payload = json.loads((EVIDENCE / "a1_reliability_synthetic_20261127.json").read_text())
    assert payload["n"] == 6000
    assert payload["r2"]["train_n"] == 3600
    assert payload["r3_gate_met"] is True
    assert payload["r3"]["model_kind"] == "R3"
    assert payload["r4"]["include_a2"] is True
    assert payload["r2"]["test_error_auroc"] > 0.5


def test_manifest_preserves_thresholds_and_research_flags():
    payload = json.loads((EVIDENCE / "a1_reliability_manifest_20261127.json").read_text())
    assert payload["frozen_thresholds"] == {"primary": 0.66806, "secondary": 0.648708}
    assert payload["can_promote"] is False
    assert payload["agent3_replaced"] is False
    assert payload["historical_model_training"] is False
    assert payload["validation"]["a2_available"] == 0
