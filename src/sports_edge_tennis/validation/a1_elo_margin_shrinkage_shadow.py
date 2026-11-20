"""Frozen Elo log-odds margin shrinkage experiment on first-issued A1 predictions.

Tests whether simply reducing the existing Surface Elo model's confidence
addresses Brian's measured overconfidence. This does NOT create a new
Weighted Elo rating system or claim to improve winner identification.
No prediction or database writes; do not promote without prospective proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import psycopg

from sports_edge_tennis.performance.calibration import brier, log_loss
from sports_edge_tennis.validation.a1_pit_calibration_shadow import (
    FirstIssue, normalize, source_sql,
)

VERSION = "A1_ELO_MARGIN_SHRINKAGE_PIT_SHADOW_V1"
# Frozen before looking at validation and holdout. Raw benchmark goes first.
SLOPES = (1.0, 0.70, 0.80, 0.90, 1.10)
SEED = 20261120


def scaled_probability(selected_win_probability: float, slope: float) -> float:
    """Shrink/exaggerate the original Elo log-odds, retaining winning side.

    This operates on genuine first-issued probabilities. A slope <1 moves
    estimates toward 0.5; it cannot change the selected match winner.
    """
    p = float(selected_win_probability)
    if not math.isfinite(p) or not 0.5 <= p < 1:
        raise ValueError("original selected probability must be [0.5, 1)")
    if not math.isfinite(slope) or slope <= 0:
        raise ValueError("slope must be positive and finite")
    x = slope * math.log(p / (1-p))
    return 1/(1+math.exp(-x)) if x >= 0 else math.exp(x)/(1+math.exp(x))


def _summary(rows: list[FirstIssue], slope: float) -> dict[str, float | int | None]:
    data = [(scaled_probability(r.probability, slope), int(r.outcome))
            for r in rows if r.outcome is not None]
    if not data:
        return {"scored":0, "winner_accuracy":None,
                "brier":None, "log_loss":None,
                "mean_selected_confidence":None}
    return {"scored":len(data),
            "winner_accuracy":sum(y for _,y in data)/len(data),
            "brier":brier(data), "log_loss":log_loss(data),
            "mean_selected_confidence":sum(p for p,_ in data)/len(data)}


def _date_cluster_ci(rows: list[FirstIssue], slope: float,
                     *, reps: int=500) -> dict[str,Any]:
    by_date: dict[str,list[FirstIssue]] = defaultdict(list)
    for r in rows:
        by_date[r.start_at.astimezone(UTC).date().isoformat()].append(r)
    dates = sorted(by_date)
    if len(dates) < 4:
        return {"status":"INSUFFICIENT_DATE_CLUSTERS", "days":len(dates)}
    rng = random.Random(SEED)
    brier_improvements = []
    loss_improvements = []
    for _ in range(reps):
        selected = [r for _ in dates for r in by_date[rng.choice(dates)]]
        raw = _summary(selected,1.0)
        shrunk = _summary(selected,slope)
        brier_improvements.append(raw["brier"]-shrunk["brier"])
        loss_improvements.append(raw["log_loss"]-shrunk["log_loss"])
    brier_improvements.sort()
    loss_improvements.sort()
    return {
        "status":"RESEARCH_ONLY_DATE_CLUSTER_BOOTSTRAP",
        "days":len(dates), "reps":reps, "seed":SEED,
        "brier_improvement_95pct": [
            brier_improvements[int(.025*(reps-1))],
            brier_improvements[int(.975*(reps-1))],
        ],
        "log_loss_improvement_95pct": [
            loss_improvements[int(.025*(reps-1))],
            loss_improvements[int(.975*(reps-1))],
        ],
    }


def evaluate(records: list[FirstIssue], *, as_of: datetime) -> dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("evaluation cutoff must include timezone")
    if len({r.match_id for r in records}) != len(records):
        raise ValueError("repeated canonical match: cannot benchmark")
    dates=sorted({r.start_at.astimezone(UTC).date() for r in records})
    digest=hashlib.sha256()
    for r in sorted(records,key=lambda x:x.match_id):
        digest.update(f"{r.match_id}|{r.issued_at.isoformat()}|{r.start_at.isoformat()}|"
                      f"{r.probability:.12f}|{r.outcome}|{r.tour}|{r.surface}\n"
                      .encode("utf-8"))
    base={
        "version":VERSION,"as_of_utc":as_of.astimezone(UTC).isoformat(),
        "first_issued_pre_match":len(records),"unique_dates":len(dates),
        "dataset_sha256":digest.hexdigest(),"slope_candidates":list(SLOPES),
        "train_fit":False, "research_only":True, "db_writes":False,
        "live_model_or_manager_changed":False, "winner_selection_changed":False,
        "not_a_new_weighted_elo_model":True,
    }
    if len(dates)<6:
        return {**base,"status":"INSUFFICIENT_UNIQUE_DATES"}
    val_idx=max(1,int(len(dates)*.6))
    hold_idx=max(val_idx+1,int(len(dates)*.8))
    if hold_idx>=len(dates):
        return {**base,"status":"INSUFFICIENT_SPLIT"}
    val_begin,hold_begin=dates[val_idx],dates[hold_idx]
    validation=[r for r in records
                if val_begin <= r.start_at.astimezone(UTC).date() < hold_begin
                and r.outcome is not None and r.result_known_at is not None
                and r.result_known_at.astimezone(UTC).date()<hold_begin]
    holdout=[r for r in records
             if r.start_at.astimezone(UTC).date()>=hold_begin
             and r.outcome is not None and r.result_known_at is not None
             and r.result_known_at<=as_of]
    base["splits"]={
        "validation_from":val_begin.isoformat(),"holdout_from":hold_begin.isoformat(),
        "validation_settled_known_before_holdout":len(validation),
        "holdout_settled":len(holdout),
        "holdout_unique_dates":len({r.start_at.date() for r in holdout}),
    }
    if len(validation)<30 or len(holdout)<40:
        return {**base,"status":"INSUFFICIENT_NONLEAKY_VALIDATION_OR_HOLDOUT"}
    val_metrics={str(s):_summary(validation,s) for s in SLOPES}
    # Min validation Brier, then log-loss, then original if tied.
    # Absolutely no holdout tuning. The fixed candidate set is immutable.
    winner=min(SLOPES,key=lambda s:(val_metrics[str(s)]["brier"],
                                     val_metrics[str(s)]["log_loss"],
                                     SLOPES.index(s)))
    original=_summary(holdout,1.0)
    selected=_summary(holdout,winner)
    per_tour={}
    for tour in sorted({r.tour for r in holdout}):
        block=[r for r in holdout if r.tour==tour]
        per_tour[tour]={"original":_summary(block,1.0),
                        "selected":_summary(block,winner)}
    per_surface={}
    for surface in sorted({r.surface for r in holdout}):
        block=[r for r in holdout if r.surface==surface]
        per_surface[surface]={"original":_summary(block,1.0),
                              "selected":_summary(block,winner)}
    return {
        **base,"status":"EVALUATED_RESEARCH_ONLY",
        "validation_metrics":val_metrics, "selected_from_validation":winner,
        "holdout_raw":original,"holdout_selected":selected,
        "holdout_brier_improvement":original["brier"]-selected["brier"],
        "holdout_log_loss_improvement":original["log_loss"]-selected["log_loss"],
        "holdout_accuracy_difference":selected["winner_accuracy"]-original["winner_accuracy"],
        "holdout_date_cluster_ci":_date_cluster_ci(holdout,winner),
        "per_tour":per_tour,"per_surface":per_surface,
        "release_eligible":False,
        "interpretation":(
            "This is a pre-registered Elo log-odds confidence-shrinkage ablation "
            "on immutable first-issued predictions. It cannot change selected winners. "
            "Validation chooses the slope; untouched chronological holdout "
            "evaluates its probability quality. Does not implement scoreline-weighted "
            "Elo or prove that overall ATP/Challenger winner accuracy increased."
        ),
    }


def run(conn:psycopg.Connection, *, as_of:datetime)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(source_sql(),{"as_of":as_of}).fetchall()
    records,rejected=normalize(rows,as_of=as_of)
    return {**evaluate(records,as_of=as_of),
            "source_rows":len(rows),"rejections":rejected}


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of-utc",required=True)
    args=p.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        p.error("as-of-utc must be explicit Z")
    if not os.environ.get("TENNIS_DATABASE_URL"):
        p.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(os.environ["TENNIS_DATABASE_URL"],
                         autocommit=True,connect_timeout=10) as conn:
        r=run(conn,as_of=datetime.fromisoformat(
            args.as_of_utc.replace("Z","+00:00")
        ))
    print("A1_ELO_SHRINKAGE_RESEARCH "+json.dumps(r,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
