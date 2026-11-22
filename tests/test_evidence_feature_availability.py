"""Feature event/known/observed/ingested provenance and original vs replay."""
from dataclasses import replace
from datetime import UTC,datetime,timedelta
import pytest

from sports_edge_tennis.validation.evidence_feature_availability import (
    EvidenceGrade,FeatureProof,ForecastProof,FEATURE_RULES,grade,
)

EARLY=datetime(2020,1,15,tzinfo=UTC)
CUTOFF=datetime(2020,1,19,10,tzinfo=UTC)
MATCH=datetime(2020,1,19,12,tzinfo=UTC)
MODERN=datetime(2026,11,21,tzinfo=UTC)


def feature(*,earliest=EARLY,ingested=EARLY,observed=EARLY,
            verified=True,nature="MATCH_RESULT"):
    return FeatureProof(
        name="opponent_result",source="official_archival_results",
        event_time=EARLY-timedelta(days=1),earliest_knowable_at=earliest,
        historical_publication_verified=verified,
        verification_reference="hash-of-archived-official-publication" if verified else None,
        original_provider_observed_at=observed,
        original_ingested_at=ingested,nature=nature,
    )


def proof(kind="ORIGINAL", *,features=None,created=None):
    return ForecastProof(
        record_kind=kind,model_version="a1-v1",
        code_commit="abc123",dataset_digest="sha256:dataset",
        prediction_cutoff=CUTOFF,
        created_at=created or (CUTOFF+timedelta(minutes=10)),
        match_start_at=MATCH,
        source_prediction_record_id="original-pick-9" if kind=="ORIGINAL" else None,
        immutable_original_record=kind=="ORIGINAL",
        feature_proofs=(feature(),) if features is None else features,
        training_window_end=datetime(2018,1,1,tzinfo=UTC),
        validation_window_end=datetime(2019,1,1,tzinfo=UTC),
        calibration_window_end=datetime(2019,8,1,tzinfo=UTC),
        parameter_freeze_time=datetime(2026,11,20,tzinfo=UTC)
    )


def test_green_verified_original_pit_and_immutable():
    x=grade(proof())
    assert x["grade"]==EvidenceGrade.GREEN.value
    assert x["reason_codes"]==[]
    assert x["declared_feature_count"]==1


def test_original_backfilled_2026_ingestion_is_not_green_2020_original():
    x=grade(proof(features=(feature(ingested=MODERN,observed=MODERN),)))
    assert x["grade"]==EvidenceGrade.RED.value
    assert "opponent_result:INGESTED_ONLY_AFTER_FORECAST_CUTOFF" in x["reason_codes"]


def test_historically_knowable_replay_remains_amber_with_2026_import():
    x=grade(proof(
        "RECONSTRUCTED",
        features=(feature(ingested=MODERN,observed=MODERN),),
        created=MODERN+timedelta(days=1),
    ))
    assert x["grade"]==EvidenceGrade.AMBER.value
    assert x["reason_codes"]==[]
    assert x["retrospectively_imported_history_allowed_for_amber"] is True


def test_retrospective_replay_never_upgraded_to_original_green():
    x=grade(proof("RECONSTRUCTED",created=MODERN+timedelta(days=1)))
    assert x["grade"]==EvidenceGrade.AMBER.value


def test_uncertain_original_publication_date_fails_even_if_event_was_past():
    original=proof(features=(feature(verified=False),))
    x=grade(original)
    assert x["grade"]==EvidenceGrade.RED.value
    assert "opponent_result:INDEPENDENT_PUBLICATION_EVIDENCE_MISSING" in x["reason_codes"]


def test_future_post_match_stats_may_not_enter_historical_features():
    later=replace(feature(),event_time=MATCH+timedelta(hours=1),
                  earliest_knowable_at=MATCH+timedelta(hours=2),
                  nature="MATCH_STATISTIC")
    x=grade(proof(features=(later,)))
    assert "opponent_result:FUTURE_REALIZED_EVENT" in x["reason_codes"]
    assert "opponent_result:PUBLISHED_AFTER_FORECAST_CUTOFF" in x["reason_codes"]


def test_legacy_14000_at_75_percent_stays_red_until_reconstructed():
    x=grade(proof("LEGACY_UNVERIFIED",created=MODERN))
    assert x["grade"]==EvidenceGrade.RED.value
    assert "LEGACY_CLAIM_UNRECONSTRUCTED" in x["reason_codes"]


def test_no_silent_training_on_evaluation_or_target_result():
    x=grade(replace(proof(),target_result_in_features=True,
                    final_test_reused_for_model_selection=True))
    assert "TARGET_RESULT_IN_FEATURES" in x["reason_codes"]
    assert "REPEATED_FINAL_TEST_MODEL_SELECTION" in x["reason_codes"]


def test_original_late_issuance_is_red_but_replay_generation_may_be_late():
    late=replace(proof(),created_at=MATCH+timedelta(minutes=1))
    assert "ORIGINAL_FORECAST_CREATED_AFTER_PREMATCH_DEADLINE" in grade(late)["reason_codes"]
    replay=proof("RECONSTRUCTED",created=MODERN+timedelta(days=1))
    assert grade(replay)["grade"]==EvidenceGrade.AMBER.value


def test_no_original_record_id_is_not_green():
    x=grade(replace(proof(),source_prediction_record_id=None))
    assert "IMMUTABLE_PREMATCH_ISSUANCE_NOT_VERIFIED" in x["reason_codes"]


def test_reconstructed_future_leakage_or_missing_partitions_is_red():
    replay=proof("RECONSTRUCTED",created=MODERN+timedelta(days=1))
    x=grade(replace(replay,training_window_end=None))
    assert "CHRONOLOGICAL_PARTITIONS_OR_FROZEN_CONFIG_MISSING" in x["reason_codes"]
    x=grade(replace(replay,parameter_freeze_time=MODERN+timedelta(days=2)))
    assert "MODEL_SELECTED_AFTER_RETROSPECTIVE_EVALUATION" in x["reason_codes"]


def test_contract_has_all_major_risky_feature_families():
    assert set(FEATURE_RULES)>={"past_match_result","historical_ranking",
      "serve_return_stats","overall_surface_elo","bookmaker_probabilities",
      "common_opponent_network","tournament_surface"}


def test_naive_timestamps_rejected():
    with pytest.raises(ValueError,match="timezone"):
        grade(replace(proof(),prediction_cutoff=CUTOFF.replace(tzinfo=None)))
