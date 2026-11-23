"""Do not claim a small trained selection difference to be 72-75% progress."""
import json
from pathlib import Path

F=Path(__file__).parents[1]/"docs/research/evidence/manager_c_logistic_reliability_real_20261123.json"


def test_live_readonly_manager_c_logistic_experiment_not_promotable():
    e=json.loads(F.read_text());r=e["report"]
    assert e["scientific_decision"]=="HOLD_UNPROVEN_RELIABILITY_CHOOSER"
    assert e["model_status"]=="RESEARCH_ONLY_NOT_PROMOTED"
    assert r["independent_prospective_confirmation"] is False
    assert r["no_live_selection_changes"] is True
    assert r["training_original_c_settled_known_at_cutoff"]==403
    assert r["validation_targets"]==1027
    assert r["evaluation_targets"]==656
    assert len(r["feature_names"])==6
    a=r["threshold_experiments"]["0.4"]
    assert (a["original"]["issued"],a["original"]["settled"],
            a["original"]["wins"],a["original"]["losses"])==(275,256,176,80)
    assert (a["reliability"]["issued"],a["reliability"]["settled"],
            a["reliability"]["wins"],a["reliability"]["losses"])==(260,241,168,73)
    assert a["reliability"]["accuracy"]>a["original"]["accuracy"]
    assert a["reliability"]["issued"]<a["original"]["issued"]
    assert a["achieved_coverage_first_runs"]["independent_official"] is None
    b=r["full_candidate_correctness_calibration"]
    assert b["calibrated_selection_model"]["n"]==283
    assert b["original_c_confidence"]["n"]==283
    assert b["calibrated_selection_model"]["brier"]>b["original_c_confidence"]["brier"]
    assert b["calibrated_selection_model"]["log_loss"]>b["original_c_confidence"]["log_loss"]
    assert a["same_volume_not_guaranteed"] is True
    assert r["result_known_cutoff_enforced"] is True
