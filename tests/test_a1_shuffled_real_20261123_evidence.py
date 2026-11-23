"""A1 full retrain actual one-off read-only study provenance and arithmetic."""
import json
from pathlib import Path

P=Path(__file__).parents[1]/"docs/research/evidence/a1_real_label_shuffle_20261123.json"


def test_real_negative_control_preserves_training_and_untouched_evaluation():
    e=json.loads(P.read_text())
    r=e["original_report"]
    assert e["evidence_class"]=="AMBER_RECONSTRUCTED_HISTORICAL_CONTROL"
    assert e["not_original_14k_proof"] is True
    assert e["no_live_model_or_manager_change"] is True
    assert r["historical_data_original_import_not_original_live_forecast"]
    assert r["no_db_mutations_or_provider_calls"]
    assert r["observed_and_ingested_before_training_cutoff"]
    assert r["evaluation_fixtures_have_original_30min_PIT_visibility"]
    assert r["training_visible_original_outcomes"]==14229
    assert r["fixed_evaluation_completed_matches"]==623
    assert r["negative_control_seed_start"]==91367
    assert r["negative_control_repeats"]==5
    assert [x["seed"] for x in r["shuffled_training_retrains"]]==list(range(91367,91372))
    real=r["real_label_trained_model"]
    assert (real["wins"],real["n"])==(385,623)
    assert round(real["accuracy"]*100,2)==61.80
    assert real["brier"]==0.23545407
    assert r["shuffled_mean_accuracy"]==0.50401284
    assert r["observed_real_vs_shuffled_accuracy_difference"]==0.11396469
    assert round(sum(x["log_loss"] for x in r["shuffled_training_retrains"])/5,5)==0.74556
    assert r["training_side_1_fraction"]<.8
    assert r["potential_winner_first_orientation_flag"] is False
    assert len(r["source_rows_sha256"])==64
    assert r["cannot_validate_original_14000_match_claim"]
