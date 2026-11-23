"""PIT-only A3 alternative: soft-decayed common opponent evidence.

This is a RESEARCH HYPOTHESIS, not a calibrated predictor or production A3.
Keeps A3's original independent production model (predict) as a control.
Three predeclared research settings examine rejection of real but older
common-opponent edges and cross-surface soft weighting. Effective sample
size (Kish ESS), neutral-prior shrinkage and confidence/abstention are
reported separately. No past winner is touched or fitted to an outcome.

IMPORTANT: original snapshots must include independently verified
provider observation/ingestion metadata as of the declared cutoff. Such
metadata is NOT included in Agent3HistorySnapshot, so the caller must
prove source availability separately. No retrospective calculation is a
genuine live-issued A3 prediction.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC,datetime,timedelta
from typing import Any

from sports_edge_tennis.agents.agent3_intransitivity import (
    DEFAULT_CONFIG, Agent3HistorySnapshot,PredictionRequest,
    predict,
)
from sports_edge_tennis.validation.a3_frozen_history_windows_shadow import (
    _validate_original_snapshot,_result_payload,
)

VERSION="A3_SOFT_COMMON_OPPONENT_ESS_SHADOW_V1"
# Prospective research profiles, not adopted from an ATP/WTA paper.
PROFILES={
    "original_edge_threshold_diagnostic":(1.0,1.0),
    "relaxed_edge_050_unweighted":(.5,1.0),
    "relaxed_edge_050_surface_soft065":(.5,.65),
}
SHRINKAGE_PRIOR_WEIGHT=8.0
MIN_ELIGIBLE_OPPONENTS=3
MIN_KISH_ESS=2.0
ABSTENTION_MIN_CONFIDENCE=.53
PROBABILITY_MIN=.35
PROBABILITY_MAX=.65


def _utc(x:datetime)->datetime:
    if x is None or x.tzinfo is None:
        raise ValueError("UTC-aware original cutoff, capture and observation required")
    return x.astimezone(UTC)


def _pair(snapshot:Agent3HistorySnapshot,a:int,b:int):
    return snapshot.pairs.get((min(a,b),max(a,b)))


def _edge(snapshot:Agent3HistorySnapshot,player:int,opponent:int,
          *,cutoff:datetime,surface:str|None,
          surface_cross_weight:float)->tuple[float,float]:
    """(effective evidence, oriented residual). No future labels allowed."""
    edge=_pair(snapshot,player,opponent)
    if edge is None:
        return 0.,0.
    surf_records=set(edge.surface_observations.get(surface,())) if surface else set()
    weighted_sum=0.
    effective=0.
    for x in edge.all_observations:
        t=_utc(x.observed_at)
        age=(cutoff-t).total_seconds()/86400
        if age<0:
            raise ValueError("future observation in historical support graph")
        if age>DEFAULT_CONFIG.lookback_days:
            continue
        weight=2**(-age/DEFAULT_CONFIG.half_life_days)
        if surface is not None and x not in surf_records:
            weight*=surface_cross_weight
        effective+=weight
        orientation=1 if player==edge.player_low_id else -1
        weighted_sum+=float(x.residual)*orientation*weight
    return effective,weighted_sum/effective if effective>0 else 0.


def _soft_variant(request:PredictionRequest, *,edge_min:float,
                  cross_surface_weight:float)->dict[str,Any]:
    if request.surface is None or not request.pit_state_resolved or not request.supported_matchup:
        return {"status":"ABSTAINED","reason":"ORIGINAL_SURFACE_OR_PIT_UNKNOWN",
                "p1":None,"eligible_shared_opponents":0,"kish_ess":0.}
    a,b=request.player_1_id,request.player_2_id
    snapshot=request.history_snapshot
    opps=(set(snapshot.adjacency.get(a,()))&
          set(snapshot.adjacency.get(b,)))-{a,b}
    cutoff=_utc(request.as_of)
    evidence=[]
    for opp in sorted(opps):
        ea,va=_edge(snapshot,a,opp,cutoff=cutoff,surface=request.surface,
                    surface_cross_weight=cross_surface_weight)
        eb,vb=_edge(snapshot,b,opp,cutoff=cutoff,surface=request.surface,
                    surface_cross_weight=cross_surface_weight)
        if ea<edge_min or eb<edge_min:
            continue
        w=min(ea,eb)
        evidence.append((w,va-vb))
    k=len(evidence)
    eff=sum(w for w,_ in evidence)
    kish=eff*eff/sum(w*w for w,_ in evidence) if evidence else 0.
    output={
        "eligible_shared_opponents":k,
        "raw_shared_opponents":len(opps),
        "effective_evidence_sum":round(eff,8),
        "kish_ess":round(kish,8),
        "hard_gate_edge_min":edge_min,
        "cross_surface_evidence_weight":cross_surface_weight,
        "minimum_kish_ess":MIN_KISH_ESS,
        "prior_weight":SHRINKAGE_PRIOR_WEIGHT,
        "calibration_status":"NOT_CALIBRATED",
        "not_original_a3_model":True,
    }
    if k<MIN_ELIGIBLE_OPPONENTS or kish<MIN_KISH_ESS:
        return {**output,"status":"ABSTAINED",
                "reason":"INSUFFICIENT_SOFT_EFFECTIVE_SUPPORT",
                "p1":None,"selected_side":None}
    raw_gap=sum(w*gap for w,gap in evidence)/eff
    shrink=eff/(eff+SHRINKAGE_PRIOR_WEIGHT)
    evidence_gap=raw_gap*shrink
    # Explicitly exploratory link. Score must be tested for calibration,
    # not promoted into the production A3 probability interface.
    p1=1/(1+math.exp(-DEFAULT_CONFIG.logistic_lambda.__float__()*evidence_gap))
    p1=max(PROBABILITY_MIN,min(PROBABILITY_MAX,p1))
    confidence=max(p1,1-p1)
    if confidence<ABSTENTION_MIN_CONFIDENCE:
        return {**output,"status":"ABSTAINED","reason":"WEAK_SHRUNK_SIGNAL",
                "p1":round(p1,8),"selected_side":None,
                "raw_gap":round(raw_gap,8),"shrink_factor":round(shrink,8)}
    return {**output,"status":"EXPERIMENTAL_PREDICTION",
            "reason":None,"p1":round(p1,8),
            "selected_side":1 if p1>.5 else 2,
            "raw_gap":round(raw_gap,8),"shrink_factor":round(shrink,8)}


def compare_snapshot(
    request:PredictionRequest, *,capture_at:datetime,
    match_start_at:datetime,verified_original_source_ingestion:bool,
    source_manifest_sha256:str,
)->dict[str,Any]:
    at=_utc(capture_at)
    cutoff=_utc(request.as_of)
    start=_utc(match_start_at)
    if not cutoff<=at<=start-timedelta(minutes=30):
        raise ValueError("must generate genuine research forecast before fixture")
    if not verified_original_source_ingestion or len(source_manifest_sha256)!=64:
        raise ValueError("independent original source-ingestion manifest required")
    _validate_original_snapshot(request.history_snapshot,cutoff)
    original=predict(request,DEFAULT_CONFIG)
    variants={
        name:_soft_variant(request,edge_min=edge,cross_surface_weight=surface)
        for name,(edge,surface) in PROFILES.items()
    }
    canonical=json.dumps(variants,sort_keys=True,separators=(",",":"))
    digest=hashlib.sha256((
        f"{VERSION}|{request.player_1_id}|{request.player_2_id}|{cutoff.isoformat()}|"
        f"{at.isoformat()}|{start.isoformat()}|{source_manifest_sha256}|{canonical}"
    ).encode()).hexdigest()
    return {
        "version":VERSION,
        "source_snapshot_manifest_sha256":source_manifest_sha256,
        "cutoff_utc":cutoff.isoformat(),
        "captured_at_utc":at.isoformat(),
        "match_start_utc":start.isoformat(),
        "record_sha256":digest,
        "original_agent3_production_control":_result_payload(original),
        "research_variants":variants,
        "not_outcome_tested":True,
        "not_calibrated":True,
        "no_production_inference_or_manager_change":True,
        "not_retroactively_representable_as_live_issued":True,
        "requires_true_future_outcomes":True,
    }
