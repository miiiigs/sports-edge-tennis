"""PIT Manager C Challenger selection-reliability audit, research ONLY.

A tiny, strongly regularised logistic reliability model is frozen BEFORE the
Nov 4 original-forecast holdout. It predicts correctness of the already-issued
C winner using only C confidence, ATP/Challenger, original A1/A2 probability
separation, opposition/disagreement, and whether BOTH agents were present.

It DOES NOT produce a new winner, use odds, add a bookmaker bet or change the
live Manager. The Nov 4-20 historical cohort was already inspected repeatedly:
results are exploratory, NEVER untouched evidence of a new 72-75% strategy.

Predictor/tour features MUST be as-of original issue; no postmatch ratings,
history or opponent statistics are fabricated here. The training labels are
further restricted to outcomes KNOWN by Nov 4 (as-of, observed, ingested).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import UTC, datetime
from typing import Any

import psycopg

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES, _utc, _wilson, source_sql,
)
from sports_edge_tennis.validation.manager_c_marginal_cohort import (
    POLICY, _original_agent_evidence,
)
from sports_edge_tennis.validation.manager_abc_validation_frozen_risk_coverage import (
    normalize,
)

VERSION = "BRAZZY365_MANAGER_C_CHOOSER_PIT_LOGISTIC_V1"
FEATURES = ("intercept", "confidence_scaled", "is_challenger",
            "a1_a2_p1_distance", "a1_a2_disagree", "a1_a2_missing")
L2 = 8.0  # fixed before original evaluation and never tuned against outcomes
ITERATIONS = 350
LEARNING_RATE = 0.12
TARGET_COVERAGES = (0.20, 0.40)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def features(record: dict, agent_evidence: dict[int, dict]) -> tuple[float, ...] | None:
    c = record["candidates"][POLICY]
    if c is None:
        return None
    if record["tour"] not in ("ATP_MAIN", "CHALLENGER"):
        raise ValueError("unsupported tour")
    probability = float(c[1])
    if not math.isfinite(probability) or not 0 < probability < 1:
        raise ValueError("invalid original C probability")
    confidence = max(probability, 1 - probability)
    agents = agent_evidence.get(record["match_id"], {})
    a1 = agents.get(CODES[0])
    a2 = agents.get(CODES[1])
    has_both = a1 is not None and a2 is not None
    sep = abs(float(a1[1]) - float(a2[1])) if has_both else 0
    disagree = int(a1[0] != a2[0]) if has_both else 0
    return (1.0, _clip((confidence - .60)/.20, 0, 2.0),
            float(record["tour"] == "CHALLENGER"),
            _clip(sep/.25, 0, 3.0), float(disagree),
            float(not has_both))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1/(1+math.exp(-z))
    z = math.exp(z)
    return z/(1+z)


def fit(training: list[tuple[tuple[float, ...], int]]) -> list[float]:
    """Predeclared full batch ridge logistic: intercept never penalized."""
    if len(training) < 30:
        raise ValueError("at least 30 genuinely time-released historical labels")
    if any(len(x) != len(FEATURES) or y not in (0,1) for x,y in training):
        raise ValueError("invalid training input")
    if len({y for _,y in training}) < 2:
        raise ValueError("need both correct and incorrect original predictions")
    n = len(training)
    weights = [0.0]*len(FEATURES)
    for _ in range(ITERATIONS):
        grad = [0.0]*len(FEATURES)
        for x,y in training:
            p = _sigmoid(sum(w*v for w,v in zip(weights,x)))
            for i,value in enumerate(x):
                grad[i] += (p-y)*value
        for i in range(len(grad)):
            grad[i] = grad[i]/n + (L2/n*weights[i] if i else 0.0)
            weights[i] -= LEARNING_RATE*grad[i]
    return weights


def score(weights: list[float], x: tuple[float, ...]) -> float:
    if len(weights) != len(FEATURES) or len(x) != len(FEATURES):
        raise ValueError("feature/model dimension mismatch")
    return _sigmoid(sum(w*v for w,v in zip(weights,x)))


def _frozen_cutoff(values: list[float], denominator: int, coverage: float) -> dict:
    if denominator <= 0 or not 0 < coverage <= 1:
        raise ValueError("invalid threshold denominator")
    count = math.ceil(coverage*denominator)
    if len(values) < count:
        return {"status": "INSUFFICIENT_PREMATCH_C_CANDIDATES",
                "cutoff": None, "capacity": len(values)/denominator}
    return {"status":"FROZEN_ON_EARLIER_ORIGINAL_FORECASTS_ONLY",
            "cutoff": sorted(values, reverse=True)[count-1],
            "capacity":len(values)/denominator}


def _accuracy(rows: list[dict]) -> dict[str, Any]:
    settled=[r for r in rows if r["winner"] in (1,2)]
    wins=sum(r["winner"]==r["candidates"][POLICY][0] for r in settled)
    tours={}
    for name in ("ATP_MAIN", "CHALLENGER"):
        subset=[r for r in settled if r["tour"]==name]
        w=sum(r["winner"]==r["candidates"][POLICY][0] for r in subset)
        tours[name]={"settled":len(subset),"wins":w,"losses":len(subset)-w,
                     "accuracy":w/len(subset) if subset else None}
    return {"issued":len(rows),"settled":len(settled),"ungraded":len(rows)-len(settled),
            "wins":wins,"losses":len(settled)-wins,
            "accuracy":wins/len(settled) if settled else None,
            "wilson95":_wilson(wins,len(settled)),
            "by_tour":tours}


def _proper(labels: list[tuple[float,int]]) -> dict:
    if not labels:
        return {"n":0,"brier":None,"log_loss":None}
    p=[_clip(p,1e-12,1-1e-12) for p,_ in labels]
    return {
        "n":len(labels),
        "brier":sum((prob-y)**2 for prob,(_,y) in zip(p,labels))/len(labels),
        "log_loss":-sum(y*math.log(prob)+(1-y)*math.log1p(-prob)
                        for prob,(_,y) in zip(p,labels))/len(labels),
    }


def analyze(records: list[dict], *, split: datetime,
            result_known_at: dict[int, datetime | None],
            original_agents: dict[int, dict],
            dataset_sha256: str | None = None) -> dict:
    split=_utc(split)
    if len({r["match_id"] for r in records})!=len(records):
        raise ValueError("duplicate first-run canonical fixture")
    if any(r["issued_at"]>r["start_at"] for r in records):
        raise ValueError("unsafe original prediction chronology")
    earlier=[r for r in records if r["start_at"]<split]
    later=[r for r in records if r["start_at"]>=split]
    if not earlier or not later:
        raise ValueError("must have earlier validation and later evaluation")
    original_features={r["match_id"]: features(r,original_agents)
                       for r in records if r["candidates"][POLICY] is not None}
    # Most recent result at report-as-of is NOT automatically a pre-split training
    # label. Require exact result-known clock <= split, otherwise withhold.
    train=[]
    for r in earlier:
        x=original_features.get(r["match_id"])
        if x is None or r["winner"] not in (1,2):
            continue
        known=result_known_at.get(r["match_id"])
        if known is None or _utc(known)>split or _utc(known)<r["start_at"]:
            continue
        train.append((x,int(r["candidates"][POLICY][0]==r["winner"])))
    weights=fit(train)
    trained={r["match_id"]:score(weights,original_features[r["match_id"]])
             for r in records if r["match_id"] in original_features}
    train_sha=hashlib.sha256(json.dumps(
        sorted([(r["match_id"],r["start_at"].isoformat(),r["winner"],
                 _utc(result_known_at[r["match_id"]]).isoformat())
                for r in earlier if r["winner"] in (1,2)
                and r["match_id"] in original_features
                and result_known_at.get(r["match_id"]) is not None
                and _utc(result_known_at[r["match_id"]])<=split]),
        separators=(",",":"),sort_keys=True).encode()).hexdigest()
    report={"version":VERSION,"evidence":"AMBER_HISTORICAL_ALREADY_INSPECTED",
            "training_cutoff_utc":split.isoformat(),"validation_targets":len(earlier),
            "evaluation_targets":len(later),"training_original_c_settled_known_at_cutoff":len(train),
            "candidate_train_sha256":train_sha,"dataset_sha256":dataset_sha256,
            "feature_names":FEATURES,"ridge_lambda":L2,
            "iterations":ITERATIONS,"fixed_learning_rate":LEARNING_RATE,
            "fitted_weights":dict(zip(FEATURES,weights)),
            "independent_prospective_confirmation":False,
            "not_full_official_fixture_denominator":True,
            "no_live_selection_changes":True}
    c_later=[r for r in later if r["match_id"] in original_features]
    good=[r for r in c_later if r["winner"] in (1,2)]
    report["full_candidate_correctness_calibration"]={
        "calibrated_selection_model":_proper([
            (trained[r["match_id"]],int(r["candidates"][POLICY][0]==r["winner"]))
            for r in good]),
        "original_c_confidence":_proper([
            (max(float(r["candidates"][POLICY][1]),
                 1-float(r["candidates"][POLICY][1])),
             int(r["candidates"][POLICY][0]==r["winner"]))
            for r in good]),
    }
    report["threshold_experiments"]={}
    for target in TARGET_COVERAGES:
        raw_values=[max(float(r["candidates"][POLICY][1]),
                        1-float(r["candidates"][POLICY][1]))
                    for r in earlier if r["match_id"] in original_features]
        learned_values=[trained[r["match_id"]] for r in earlier
                        if r["match_id"] in original_features]
        raw_cut=_frozen_cutoff(raw_values,len(earlier),target)
        learned_cut=_frozen_cutoff(learned_values,len(earlier),target)
        if raw_cut["cutoff"] is None or learned_cut["cutoff"] is None:
            report["threshold_experiments"][str(target)]={"status":"UNREACHABLE"}
            continue
        original=[r for r in c_later
                  if max(float(r["candidates"][POLICY][1]),
                         1-float(r["candidates"][POLICY][1]))+1e-12>=raw_cut["cutoff"]]
        selected=[r for r in c_later
                  if trained[r["match_id"]]+1e-12>=learned_cut["cutoff"]]
        id_a={r["match_id"] for r in original}
        id_b={r["match_id"] for r in selected}
        ids={r["match_id"]:r for r in c_later}
        common=[ids[mid] for mid in sorted(id_a&id_b)]
        only_original=[ids[mid] for mid in sorted(id_a-id_b)]
        only_learned=[ids[mid] for mid in sorted(id_b-id_a)]
        report["threshold_experiments"][str(target)]={
            "original_confidence_cutoff":raw_cut["cutoff"],
            "reliability_cutoff":learned_cut["cutoff"],
            "original":_accuracy(original),
            "reliability":_accuracy(selected),
            "identical_match_id_overlap":len(common),
            "original_only":_accuracy(only_original),
            "reliability_only":_accuracy(only_learned),
            "achieved_coverage_first_runs":{
                "original":len(original)/len(later),
                "reliability":len(selected)/len(later),
                "independent_official":None,
            },
            "selected_match_ids_sha256":{
                name:hashlib.sha256(json.dumps(sorted(ids_),separators=(",",":")).encode()).hexdigest()
                for name,ids_ in (("original",id_a),("reliability",id_b))
            },
            "same_volume_not_guaranteed":True,
            "no_unseen_result_used_in_rank_or_cutoff":True,
        }
    return report


def run(conn: psycopg.Connection, *, start:datetime,end:datetime,
        as_of:datetime,split:datetime) -> dict:
    conn.execute("SET default_transaction_read_only=on")
    raw=conn.execute(source_sql(),{"start":_utc(start),"end":_utc(end),
                                   "as_of":_utc(as_of)}).fetchall()
    records,excluded,sha=normalize(raw,start=start,end=end,as_of=as_of)
    known={int(row[0]):row[17] for row in raw}
    report=analyze(records,split=split,result_known_at=known,
                   original_agents=_original_agent_evidence(raw),
                   dataset_sha256=sha)
    report["original_pit_exclusions"]=excluded
    report["result_known_cutoff_enforced"]=True
    return report


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("start","end","as_of","split"):
        parser.add_argument("--"+name.replace("_","-")+"-utc",required=True)
    args=parser.parse_args(argv)
    times=[getattr(args,name+"_utc") for name in ("start","end","as_of","split")]
    if any(not x.endswith("Z") for x in times):
        parser.error("all times must be explicit UTC Z")
    start,end,as_of,split=[
        datetime.fromisoformat(x.replace("Z","+00:00")) for x in times]
    if not _utc(start)<_utc(split)<_utc(end)<=_utc(as_of):
        parser.error("require start < split < end <= as-of")
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        result=run(conn,start=start,end=end,as_of=as_of,split=split)
    print("TENNIS_MANAGER_C_CHOOSER "+json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
