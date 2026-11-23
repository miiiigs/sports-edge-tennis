"""Leakage-sensitive full Agent 1 Elo retraining with shuffled TRAINING outcomes.

This control destroys the player/outcome association IN THE TRAINING SET,
rebuilds all Elo ratings from scratch for each repeat, and holds true
post-cutoff evaluation match results fixed. Unlike shuffled-score sanity
checks, it cannot benefit from already-trained real rating states.

This uses a separate, fixed-2026 train/test boundary and applies only to
source observations whose original result visibility can be proven before
that boundary. Retrospective acquisition in 2026 is legitimate as research
training for a 2026 boundary, but does not establish that a reconstructed
2019 prediction was originally issued in 2019.

Caveat: this is a simple frozen pre-cutoff Elo benchmark, not a retrain of
all A1/A2/A3 + Manager, and not proof that EVERY feature is leakage-free.
No production code, forecasts, selection rules, API or DB writes.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from typing import Any

from sports_edge_tennis.agents.agent1_surface_elo import DEFAULT_CONFIG
from sports_edge_tennis.validation.agent1_walk_forward import (
    EvaluationTarget,RunnerState,ValidationObservation,predict_from_state,
)

VERSION="A1_FROZEN_CUTOFF_TRAIN_LABEL_SHUFFLE_V1"


def _utc(x:datetime|None,label:str)->datetime:
    if x is None or x.tzinfo is None:
        raise ValueError(label+" must be timezone-aware")
    return x.astimezone(UTC)


def split_and_validate(
    rows:list[ValidationObservation],
    *,train_cutoff:datetime,evaluation_end:datetime,
    min_train:int=60,min_eval:int=30,
)->tuple[list[ValidationObservation],list[ValidationObservation]]:
    cutoff,end=_utc(train_cutoff,"train_cutoff"),_utc(evaluation_end,"evaluation_end")
    if not cutoff<end or (end-cutoff).days>365:
        raise ValueError("evaluation period must follow training cutoff by <=365d")
    seen=set()
    training=[]
    evaluating=[]
    for rec in rows:
        if rec.match_id in seen:
            raise ValueError("duplicate match could leak from train into test")
        seen.add(rec.match_id)
        if rec.outcome_status!="COMPLETED" or rec.winner_side not in (1,2):
            continue
        start=_utc(rec.match_start_at,"match_start_at")
        observed=_utc(rec.source_observed_at,"source_observed_at")
        asof=_utc(rec.as_of,"as_of")
        visibility=max(observed,asof)
        if visibility<start:
            raise ValueError("result visibility before match start is invalid")
        if start<cutoff:
            # Only legitimately knowable results may train the model.
            if visibility<=cutoff:
                training.append(rec)
            # No backfilled post-cutoff record is allowed to train, even if
            # its sporting event happened years earlier.
        elif cutoff<=start<end:
            evaluating.append(rec)
    if len(training)<min_train or len(evaluating)<min_eval:
        raise ValueError(
            f"insufficient time-safe data train={len(training)} eval={len(evaluating)}"
        )
    training.sort(key=lambda r:(r.visibility_at,r.as_of,r.match_id))
    evaluating.sort(key=lambda r:(r.match_start_at,r.match_id))
    return training,evaluating


def _evaluate(training:list[ValidationObservation],
              evaluating:list[ValidationObservation],
              *,shuffled_training_winners:list[int]|None
              )->dict[str,Any]:
    state=RunnerState.empty()
    if shuffled_training_winners is not None and len(shuffled_training_winners)!=len(training):
        raise ValueError("training labels length mismatch")
    for i,rec in enumerate(training):
        state.apply_revealed_observation(
            replace(rec,winner_side=shuffled_training_winners[i])
            if shuffled_training_winners is not None else rec,
            DEFAULT_CONFIG,
        )
    n=0
    wins=0
    brier_sum=0.
    loss_sum=0.
    predictions=[]
    forecast_only=[]
    for rec in evaluating:
        target=EvaluationTarget(
            season_year=rec.season_year,
            match_id=rec.match_id,
            player_1_id=rec.player_1_id,
            player_2_id=rec.player_2_id,
            surface=rec.surface,
            tour_scope=rec.tour_scope,
            competition_tier=rec.competition_tier,
            best_of_sets=rec.best_of_sets,
            qualifying_flag=rec.qualifying_flag,
            prediction_boundary=_utc(rec.match_start_at,"match_start_at")-timedelta(minutes=30),
            original_player_1_id=rec.original_player_1_id or rec.player_1_id,
            original_player_2_id=rec.original_player_2_id or rec.player_2_id,
            side_swapped=rec.side_swapped,
        )
        result=predict_from_state(target,state,DEFAULT_CONFIG)
        p=float(result.player_1_probability)
        y=int(rec.winner_side==1)
        if not math.isfinite(p) or not 0<p<1:
            raise ValueError("invalid Elo probability")
        n+=1
        wins+=int(result.selection_side==rec.winner_side)
        brier_sum+=(p-y)**2
        loss_sum+=-(y*math.log(p)+(1-y)*math.log(1-p))
        predictions.append((int(rec.match_id),round(p,6),y))
        forecast_only.append((int(rec.match_id),round(p,6)))
    return {
        "n":n,"wins":wins,
        "accuracy":round(wins/n,8),
        "brier":round(brier_sum/n,8),
        "log_loss":round(loss_sum/n,8),
        "predictions_sha256":hashlib.sha256(
            json.dumps(predictions,separators=(",",":")).encode()
        ).hexdigest(),
        "forecast_only_sha256":hashlib.sha256(
            json.dumps(forecast_only,separators=(",",":")).encode()
        ).hexdigest(),
        "trained_history_count":state.eligible_history_match_count,
    }


def run_negative_control(
    rows:list[ValidationObservation],
    *,train_cutoff:datetime,evaluation_end:datetime,
    seed:int=91367,repeats:int=5,min_train:int=60,min_eval:int=30,
)->dict[str,Any]:
    if not 2<=repeats<=30:
        raise ValueError("must predeclare between 2 and 30 label permutations")
    train,eval_rows=split_and_validate(
        rows,train_cutoff=train_cutoff,evaluation_end=evaluation_end,
        min_train=min_train,min_eval=min_eval,
    )
    positive=_evaluate(train,eval_rows,shuffled_training_winners=None)
    labels=[int(r.winner_side) for r in train]
    negatives=[]
    for i in range(repeats):
        rng=random.Random(seed+i)
        shuffled=list(labels)
        rng.shuffle(shuffled)
        if shuffled==labels:
            # Deterministic low-probability guard for small synthetic cases:
            # force a rotation rather than accidentally passing untouched labels.
            shuffled=shuffled[1:]+shuffled[:1]
        negatives.append({
            "repeat":i+1,"seed":seed+i,
            "training_winner_side_1_count":shuffled.count(1),
            "training_winner_side_2_count":shuffled.count(2),
            **_evaluate(train,eval_rows,shuffled_training_winners=shuffled),
        })
    return {
        "version":VERSION,
        "method":"full from-scratch Elo retrain after TRAIN ONLY winner permutation",
        "historical_evidence_classification":"AMBER_RECONSTRUCTED_RESEARCH_ONLY",
        "no_original_live_prediction_claim":True,
        "training_cutoff_utc":_utc(train_cutoff,"train_cutoff").isoformat(),
        "evaluation_end_utc":_utc(evaluation_end,"evaluation_end").isoformat(),
        "training_visible_original_outcomes":len(train),
        "fixed_evaluation_completed_matches":len(eval_rows),
        "training_label_counts":{"winner_side_1":labels.count(1),
                                 "winner_side_2":labels.count(2)},
        "negative_control_seed_start":seed,
        "negative_control_repeats":repeats,
        "real_label_trained_model":positive,
        "shuffled_training_retrains":negatives,
        "shuffled_mean_accuracy":round(
            sum(r["accuracy"] for r in negatives)/repeats,8),
        "shuffled_mean_brier":round(
            sum(r["brier"] for r in negatives)/repeats,8),
        "observed_real_vs_shuffled_accuracy_difference":round(
            positive["accuracy"]-
            sum(r["accuracy"] for r in negatives)/repeats,8),
        "no_live_implementation_or_policy_change":True,
        "no_commercial_data_usage_permission_inferred":True,
        "not_complete_a1_a2_a3_manager_retraining":True,
        "never_training_on_holdout_winners":True,
    }
