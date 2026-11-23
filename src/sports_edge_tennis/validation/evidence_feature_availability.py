"""Explicit original/retrospective/unverified tennis forecast evidence contract.

Green: original immutable pre-match production forecast with proof its recorded
features existed before its cutoff. Amber: retrospectively reconstructed,
chronologically sound research forecast, with documented historical
publication availability. Red: unverified or unsafe historical claim.

A historical result imported in 2026 is not automatically invalid for a
historical 2020 backtest: its independent *historical publication proof* can
establish it was knowable by 2020. But it can never be retroactively represented
as an original 2020 production-issued forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Literal

PREMATCH_MINUTES=30
CONTRACT_VERSION="TENNIS_FEATURE_AVAILABILITY_EVIDENCE_V1"


class EvidenceGrade(str,Enum):
    GREEN="GREEN_VERIFIED_ORIGINAL"
    AMBER="AMBER_RECONSTRUCTED_HISTORICAL_BACKTEST"
    RED="RED_UNVERIFIED_OR_UNSAFE"


@dataclass(frozen=True)
class FeatureProof:
    name:str
    source:str
    event_time:datetime|None
    earliest_knowable_at:datetime|None
    historical_publication_verified:bool
    verification_reference:str|None
    original_provider_observed_at:datetime|None
    original_ingested_at:datetime|None
    # The feature must belong to one of these declared, not silently renameable
    # temporal categories. The actual clock is evidence, not a trusted flag.
    nature:Literal[
        "MATCH_RESULT","RANKING","MATCH_STATISTIC","SURFACE",
        "PLAYER_HISTORY","ODDS","CONTEXTUAL"
    ]


@dataclass(frozen=True)
class ForecastProof:
    record_kind:Literal["ORIGINAL","RECONSTRUCTED","LEGACY_UNVERIFIED"]
    model_version:str|None
    code_commit:str|None
    dataset_digest:str|None
    prediction_cutoff:datetime
    created_at:datetime
    match_start_at:datetime
    source_prediction_record_id:str|None
    immutable_original_record:bool
    feature_proofs:tuple[FeatureProof,...]
    training_window_end:datetime|None
    validation_window_end:datetime|None
    calibration_window_end:datetime|None
    parameter_freeze_time:datetime|None
    # A causal check: source result or selection label must not influence any
    # purportedly pre-match feature.
    target_result_in_features:bool=False
    final_test_reused_for_model_selection:bool=False


def utc(moment:datetime|None, field:str)->datetime:
    if moment is None or moment.tzinfo is None:
        raise ValueError(field+" requires an explicit timezone")
    return moment.astimezone(UTC)


def _known(feature:FeatureProof, cutoff:datetime,
           *,is_original:bool)->list[str]:
    reasons=[]
    name=feature.name.strip() or "UNNAMED"
    if not feature.source.strip():
        reasons.append(name+":SOURCE_UNDECLARED")
    if feature.earliest_knowable_at is None:
        reasons.append(name+":HISTORICAL_AVAILABILITY_UNKNOWN")
        return reasons
    known=utc(feature.earliest_knowable_at,name+".earliest_knowable_at")
    if known>cutoff:
        reasons.append(name+":PUBLISHED_AFTER_FORECAST_CUTOFF")
    if not feature.historical_publication_verified or not (feature.verification_reference or "").strip():
        reasons.append(name+":INDEPENDENT_PUBLICATION_EVIDENCE_MISSING")
    if feature.event_time is not None and utc(feature.event_time,name+".event_time")>cutoff:
        # A future event can be anticipated (weather forecast etc), but then
        # the feature must reference the original pre-event forecast PUBLICATION
        # observation and not the event's realized facts.
        if feature.nature in ("MATCH_RESULT","MATCH_STATISTIC","PLAYER_HISTORY"):
            reasons.append(name+":FUTURE_REALIZED_EVENT")
    if is_original:
        if feature.original_provider_observed_at is None:
            reasons.append(name+":ORIGINAL_PROVIDER_OBSERVATION_MISSING")
        elif utc(feature.original_provider_observed_at,name+".original_provider_observed_at")>cutoff:
            reasons.append(name+":OBSERVED_ONLY_AFTER_FORECAST_CUTOFF")
        if feature.original_ingested_at is None:
            reasons.append(name+":ORIGINAL_INGESTION_PROOF_MISSING")
        elif utc(feature.original_ingested_at,name+".original_ingested_at")>cutoff:
            reasons.append(name+":INGESTED_ONLY_AFTER_FORECAST_CUTOFF")
    return reasons


def grade(record:ForecastProof)->dict[str,object]:
    cutoff=utc(record.prediction_cutoff,"prediction_cutoff")
    created=utc(record.created_at,"created_at")
    match_start=utc(record.match_start_at,"match_start_at")
    reasons=[]
    if record.record_kind=="LEGACY_UNVERIFIED":
        reasons.append("LEGACY_CLAIM_UNRECONSTRUCTED")
    if cutoff>match_start-timedelta(minutes=PREMATCH_MINUTES):
        reasons.append("FORECAST_INFORMATION_CUTOFF_TOO_LATE")
    if record.record_kind=="ORIGINAL" and created>match_start-timedelta(minutes=PREMATCH_MINUTES):
        reasons.append("ORIGINAL_FORECAST_CREATED_AFTER_PREMATCH_DEADLINE")
    if record.record_kind=="RECONSTRUCTED" and created<cutoff:
        reasons.append("RETROSPECTIVE_RECONSTRUCTION_CREATION_BEFORE_SIMULATED_CUTOFF")
    if not record.model_version or not record.code_commit:
        reasons.append("MODEL_OR_COMMIT_VERSION_MISSING")
    if not record.dataset_digest:
        reasons.append("DATASET_VERSION_MISSING")
    if not record.feature_proofs:
        reasons.append("FEATURE_PROVENANCE_MANIFEST_MISSING")
    if record.target_result_in_features:
        reasons.append("TARGET_RESULT_IN_FEATURES")
    if record.final_test_reused_for_model_selection:
        reasons.append("REPEATED_FINAL_TEST_MODEL_SELECTION")
    features_seen=set()
    for f in record.feature_proofs:
        if not f.name.strip() or f.name in features_seen:
            reasons.append("DUPLICATE_OR_EMPTY_FEATURE_NAME")
        features_seen.add(f.name)
        reasons.extend(_known(f,cutoff,is_original=record.record_kind=="ORIGINAL"))
    if record.record_kind=="ORIGINAL":
        if not record.immutable_original_record or not record.source_prediction_record_id:
            reasons.append("IMMUTABLE_PREMATCH_ISSUANCE_NOT_VERIFIED")
        if created<cutoff:
            reasons.append("CREATION_BEFORE_REPORTED_FEATURE_CUTOFF")
    else:
        if record.record_kind=="RECONSTRUCTED":
            if not all((
                record.training_window_end,
                record.validation_window_end,
                record.calibration_window_end,
                record.parameter_freeze_time,
            )):
                reasons.append("CHRONOLOGICAL_PARTITIONS_OR_FROZEN_CONFIG_MISSING")
            else:
                train=utc(record.training_window_end,"training_window_end")
                val=utc(record.validation_window_end,"validation_window_end")
                cal=utc(record.calibration_window_end,"calibration_window_end")
                frozen=utc(record.parameter_freeze_time,"parameter_freeze_time")
                if not train<val<=cal<cutoff:
                    reasons.append("NON_CHRONOLOGICAL_TRAIN_VALIDATION_CALIBRATION")
                # Retrospective research is allowed to be designed in 2026
                # when simulating a 2020 forecast, but must freeze its
                # configuration before actually running its evaluation.
                if frozen>created:
                    reasons.append("MODEL_SELECTED_AFTER_RETROSPECTIVE_EVALUATION")
        # A reconstruction *must* be labelled as retrospective, even if its
        # underlying historical facts are legitimate and verified.
        if record.record_kind=="LEGACY_UNVERIFIED" and created<cutoff:
            reasons.append("INCONSISTENT_LEGACY_SOURCE")
    if reasons:
        color=EvidenceGrade.RED
    elif record.record_kind=="ORIGINAL":
        color=EvidenceGrade.GREEN
    elif record.record_kind=="RECONSTRUCTED":
        color=EvidenceGrade.AMBER
    else:
        color=EvidenceGrade.RED
    return {
        "contract_version":CONTRACT_VERSION,
        "grade":color.value,
        "record_kind":record.record_kind,
        "reason_codes":sorted(set(reasons)),
        "verified_feature_count":sum(
            bool(f.historical_publication_verified and f.verification_reference)
            for f in record.feature_proofs),
        "declared_feature_count":len(record.feature_proofs),
        "retrospectively_imported_history_allowed_for_amber":(
            record.record_kind=="RECONSTRUCTED"),
        "not_evidence_of_sustained_accuracy":True,
    }


FEATURE_RULES={
    "past_match_result":"Original match completed before prediction; independently documented earliest official result-publication time",
    "historical_ranking":"Date the actual historical ranking list became public, not the later updated database ranking",
    "serve_return_stats":"Only historical completed matches whose match statistics were public before prediction; never the target-match statistics",
    "overall_surface_elo":"Computed sequentially only from historically available results and frozen rating configuration",
    "common_opponent_network":"Edges only from completed and knowable prior matches; future graph edges prohibited",
    "tournament_surface":"Original pre-match official tournament surface information, not a later inferred correction",
    "bookmaker_probabilities":"Pair both player quotes from same bookmaker and timestamp, with source observation and original ingestion strictly before issue",
    "verified_player_context":"Contemporaneously published source with exact player identity and publication time",
}
