"""Manager original/B/C validation-frozen risk-coverage on original first issues.

This intentionally does NOT train Manager D/E/F, calibrate outcomes, or
manufacture a score for a rejected fixture. Each policy has its own frozen
candidate gate, and coverage targets above that policy's validation capacity
are explicitly UNREACHABLE rather than extrapolated. Thresholds are fitted
to earlier validation *prediction confidence only*, then applied to later
first-issued predictions without using later confidence distributions or
later outcomes. Previously inspected November holdout remains exploratory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.manager import __name__ as _manager_module_name
from sports_edge_tennis.performance.calibration import brier, log_loss
from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES, PREMATCH_MINUTES, _available_agents, _policy, _utc, _wilson, source_sql,
)

VERSION="MANAGER_ABC_LOCKED_VALIDATION_RISK_COVERAGE_V1"
COVERAGE_TARGETS=(.10,.20,.25,.40,.50,.75,1.00)
POLICIES=("original_manager","B_guarded2_floor60_spread20",
          "C_uncalibrated_mean_floor60")


def normalize(rows:list[tuple],*,start:datetime,end:datetime,
              as_of:datetime)->tuple[list[dict[str,Any]],dict[str,int],str]:
    start,end,as_of=_utc(start),_utc(end),_utc(as_of)
    if not start<end<=as_of or (end-start).days>120:
        raise ValueError("invalid historical match window")
    grouped=defaultdict(list)
    for raw in rows:
        grouped[int(raw[0])].append(raw)
    records=[]
    exclusions=Counter()
    digest=hashlib.sha256()
    for mid in sorted(grouped):
        bundle=grouped[mid]
        first=bundle[0]
        (match_id,run_id,issued,match_start,tour,edition_id,
         _agent_code,_status,_side,_confidence,_pred_as_of,
         manager_status,manager_reason,manager_side,manager_probability,
         result_status,result_winner,result_known)=first
        if any((r[1:6],r[11:])!=(first[1:6],first[11:]) for r in bundle):
            raise ValueError("mixed original runs or result versions")
        if (issued is None or match_start is None or edition_id is None
            or tour not in ("ATP_MAIN","CHALLENGER")
            or _utc(issued)>_utc(match_start)-timedelta(minutes=PREMATCH_MINUTES)):
            exclusions["UNSAFE_OR_LATE_FIRST_PREMATCH_ISSUE"]+=1
            continue
        if not start<=_utc(match_start)<end or _utc(issued)>as_of:
            raise ValueError("original fixture outside frozen window")
        preds={}
        for r in bundle:
            code,status,side,prob,p_time=r[6:11]
            if code is None:
                continue
            if code not in CODES or code in preds:
                raise ValueError("duplicate or unexpected original agent prediction")
            if p_time is None or _utc(p_time)>_utc(issued):
                raise ValueError("original agent prediction appeared after publication")
            preds[code]=(str(status),int(side) if side is not None else None,
                         float(prob) if prob is not None else None)
        candidates=_policy(_available_agents(preds))
        original=None
        if manager_status=="CONSENSUS":
            if manager_side not in (1,2) or manager_probability is None:
                raise ValueError("original Manager consensus lacks valid probability")
            v=float(manager_probability)
            if not math.isfinite(v) or not .5<=v<1:
                raise ValueError("invalid original Manager probability")
            original=(int(manager_side),v if manager_side==1 else 1-v)
        elif manager_status not in ("NO_BET",None):
            raise ValueError("invalid original Manager decision status")
        candidates["original_manager"]=original
        winner=None
        if result_status=="COMPLETED" and result_winner in (1,2):
            if result_known is None or _utc(result_known)>as_of or _utc(result_known)<_utc(match_start):
                exclusions["UNSAFE_RESULT_GRADED_AS_UNRESOLVED"]+=1
            else:
                winner=int(result_winner)
        for name,pick in candidates.items():
            if pick is not None:
                side,prob=pick
                if side not in (1,2) or not math.isfinite(float(prob)) or not 0<float(prob)<1:
                    raise ValueError("invalid candidate selection probability")
                if (side==1)!=(prob>.5):
                    raise ValueError("side/probability orientation mismatch")
        digest.update((
            f"{mid}|{run_id}|{_utc(issued).isoformat()}|{tour}|"
            f"{sorted(preds.items())}|{manager_status}|{manager_reason}|"
            f"{candidates}|{result_status}|{winner}\n"
        ).encode())
        records.append({
            "match_id":int(mid),"run_id":int(run_id),
            "tour":tour,"edition":str(edition_id),
            "issued_at":_utc(issued),
            "start_at":_utc(match_start),
            "winner":winner,"result_status":result_status or "UNOBSERVED",
            "candidates":candidates,
        })
    return records,dict(sorted(exclusions.items())),digest.hexdigest()


def freeze_threshold(validation:list[dict[str,Any]], *,
                     policy:str, coverage_target:float)->dict[str,Any]:
    if policy not in POLICIES or coverage_target not in COVERAGE_TARGETS:
        raise ValueError("must use registered policy and coverage target")
    n=len(validation)
    if n==0:
        return {"status":"NO_VALIDATION_FIRST_RUN_TARGETS","threshold":None,
                "validation_target_fixtures":0,"validation_policy_candidates":0}
    scores=sorted((
        max(float(v[1]),1-float(v[1]))
        for record in validation
        if (v:=record["candidates"][policy]) is not None
    ),reverse=True)
    target_n=math.ceil(n*coverage_target)
    if len(scores)<target_n:
        return {
            "status":"UNREACHABLE_ON_VALIDATION_CANDIDATE_CAPACITY",
            "threshold":None,
            "validation_target_fixtures":n,
            "validation_policy_candidates":len(scores),
            "validation_policy_candidate_maximum_coverage":len(scores)/n,
        }
    return {
        "status":"VALIDATION_CONFIDENCE_CUTOFF_FROZEN",
        "threshold":scores[target_n-1],
        "validation_target_fixtures":n,
        "validation_policy_candidates":len(scores),
        "validation_policy_candidate_maximum_coverage":len(scores)/n,
    }


def evaluate_selected(population:list[dict[str,Any]], *,
                      policy:str, cutoff:float|None)->tuple[dict[str,Any],list[dict[str,Any]]]:
    if cutoff is None:
        raise ValueError("cutoff must have passed earlier validation capacity")
    selected=[]
    for row in population:
        pick=row["candidates"][policy]
        if pick is None:
            continue
        if max(float(pick[1]),1-float(pick[1]))+1e-12>=cutoff:
            selected.append(row)
    settled=[row for row in selected if row["winner"] in (1,2)]
    wins=sum(r["candidates"][policy][0]==r["winner"] for r in settled)
    pairs=[(float(r["candidates"][policy][1]),int(r["winner"]==1))
           for r in settled]
    return {
        "holdout_first_run_targets":len(population),
        "selected":len(selected),"settled":len(settled),
        "ungraded":len(selected)-len(settled),
        "wins":wins,"losses":len(settled)-wins,
        "target_conditional_coverage":len(selected)/len(population) if population else None,
        "winner_accuracy":wins/len(settled) if settled else None,
        "winner_wilson95":_wilson(wins,len(settled)),
        "brier":brier(pairs) if pairs else None,
        "log_loss":log_loss(pairs) if pairs else None,
        "full_official_eligible_match_coverage":None,
        "odds_or_matched_market_accuracy":None,
    },selected


def paired_overlap(first:list[dict],second:list[dict],
                   *,first_policy:str,second_policy:str)->dict[str,Any]:
    a={r["match_id"]:r for r in first if r["winner"] in (1,2)}
    b={r["match_id"]:r for r in second if r["winner"] in (1,2)}
    shared=sorted(set(a)&set(b))
    correct_a=correct_b=a_only=b_only=both_correct=both_wrong=0
    p1_a,p1_b=[],[]
    for mid in shared:
        r=a[mid]
        if r["winner"]!=b[mid]["winner"]:
            raise ValueError("same match has incompatible known winner")
        pa=r["candidates"][first_policy]
        pb=r["candidates"][second_policy]
        wa=pa[0]==r["winner"]
        wb=pb[0]==r["winner"]
        correct_a+=int(wa);correct_b+=int(wb)
        both_correct+=int(wa and wb)
        both_wrong+=int(not wa and not wb)
        a_only+=int(wa and not wb)
        b_only+=int(wb and not wa)
        y=int(r["winner"]==1)
        p1_a.append((float(pa[1]),y))
        p1_b.append((float(pb[1]),y))
    return {
        "same_selected_settled":len(shared),
        "correct_first":correct_a,"correct_second":correct_b,
        "both_correct":both_correct,"both_wrong":both_wrong,
        "first_only_correct":a_only,"second_only_correct":b_only,
        "brier_first":brier(p1_a) if p1_a else None,
        "brier_second":brier(p1_b) if p1_b else None,
        "different_selected_populations_not_a_paired_accuracy_test":True,
    }



def exact_issued_coverage_descriptive(
    population:list[dict[str,Any]], *, target:float
)->dict[str,Any]:
    """Descriptive exact-K equal-coverage evaluation, NOT a deployable cutoff.

    Knowing the evaluation cohort's full confidence distribution to choose
    top K is only permitted for RESEARCH presentation. The separately
    reported validation-frozen thresholds are the deployable methodology.
    Both use only original pre-match prediction scores, never outcomes
    for ranking.
    """
    if target not in COVERAGE_TARGETS:
        raise ValueError("coverage must use registered fixed targets")
    k=math.ceil(len(population)*target)
    output={
        "target_fraction_of_first_run_targets":target,
        "target_exact_issued_count":k,
        "full_eligible_fixture_coverage":None,
        "historical_cohort_ranking_not_operational_threshold":True,
        "policies":{},
    }
    chosen={}
    for policy in POLICIES:
        available=[r for r in population if r["candidates"][policy] is not None]
        available.sort(key=lambda r:(
            -max(float(r["candidates"][policy][1]),
                 1-float(r["candidates"][policy][1])),
            r["match_id"],
        ))
        if len(available)<k:
            output["policies"][policy]={
                "status":"UNACHIEVABLE_WITH_EXISTING_PREDICTIONS",
                "existing_candidates":len(available),
                "needed_exact_issue_count":k,
                "cannot_fabricate_missing_forecasts":True,
            }
            continue
        selected=available[:k]
        chosen[policy]=selected
        graded=[r for r in selected if r["winner"] in (1,2)]
        wins=sum(r["candidates"][policy][0]==r["winner"] for r in graded)
        scored=[
            (float(r["candidates"][policy][1]),int(r["winner"]==1))
            for r in graded
        ]
        output["policies"][policy]={
            "status":"EXACT_K_DESCRIPTIVE_ONLY",
            "issued":len(selected),"settled":len(graded),
            "ungraded":len(selected)-len(graded),
            "wins":wins,"losses":len(graded)-wins,
            "accuracy":wins/len(graded) if graded else None,
            "accuracy_wilson95":_wilson(wins,len(graded)),
            "brier":brier(scored) if scored else None,
            "log_loss":log_loss(scored) if scored else None,
            "conditional_first_run_target_coverage":len(selected)/len(population) if population else None,
            "actual_official_fixture_coverage":None,
        }
    if "B_guarded2_floor60_spread20" in chosen and "C_uncalibrated_mean_floor60" in chosen:
        output["paired_B_vs_C_on_exact_K_overlap_only"]=paired_overlap(
            chosen["B_guarded2_floor60_spread20"],
            chosen["C_uncalibrated_mean_floor60"],
            first_policy="B_guarded2_floor60_spread20",
            second_policy="C_uncalibrated_mean_floor60",
        )
    if "original_manager" in chosen and "B_guarded2_floor60_spread20" in chosen:
        output["paired_original_vs_B_on_exact_K_overlap_only"]=paired_overlap(
            chosen["original_manager"],
            chosen["B_guarded2_floor60_spread20"],
            first_policy="original_manager",
            second_policy="B_guarded2_floor60_spread20",
        )
    return output


def evaluate(records:list[dict[str,Any]], *,start:datetime,end:datetime,
             as_of:datetime,validation_end:datetime,
             exclusion_counts:dict[str,int]|None=None,
             dataset_sha256:str|None=None)->dict[str,Any]:
    start,end,as_of,validation_end=map(_utc,(start,end,as_of,validation_end))
    if not start<validation_end<end<=as_of:
        raise ValueError("fixed earlier validation must precede later holdout")
    if validation_end.time()!=datetime.min.time():
        raise ValueError("validation split must be midnight UTC")
    if any(r["issued_at"]>r["start_at"]-timedelta(minutes=PREMATCH_MINUTES)
           for r in records):
        raise ValueError("late original candidate")
    if len({r["match_id"] for r in records})!=len(records):
        raise ValueError("duplicate original first-run fixture")
    validation=[r for r in records if r["start_at"]<validation_end]
    holdout=[r for r in records if r["start_at"]>=validation_end]
    result={
        "version":VERSION,
        "window_start_utc":start.isoformat(),"validation_end_utc":validation_end.isoformat(),
        "window_end_utc":end.isoformat(),"as_of_utc":as_of.isoformat(),
        "dataset_sha256":dataset_sha256,
        "exclusion_counts":exclusion_counts or {},
        "all_first_run_targets":len(records),
        "earlier_validation_first_run_targets":len(validation),
        "later_holdout_first_run_targets":len(holdout),
        "holdout_safely_settled":sum(r["winner"] in (1,2) for r in holdout),
        "full_official_eligible_match_population":None,
        "research_only_historical_holdout_previously_inspected":True,
        "manager_D_calibrated_average":"NOT_FITTED_CHRONOLOGICAL_CALIBRATION_REQUIRED",
        "manager_E_weighted":"NOT_FITTED_CHRONOLOGICAL_AGENT_OOS_REQUIRED",
        "manager_F_stacking":"NOT_FITTED_INDEPENDENT_OOS_AGENT_HISTORY_REQUIRED",
        "manager_A_strict_unanimity":"NOT_INCLUDED_HERE_4_TOTAL_PREDICTIONS_UNDER_PRIOR_A3_PARTICIPATION",
        "no_market_odds":True,"no_purchases":True,
        "no_original_model_pick_mutation":True,"no_live_rules_changed":True,
        "curve":{},
        "descriptive_equal_issued_count_curves":{},
    }
    for target in COVERAGE_TARGETS:
        name=str(round(target,4))
        row={}
        selected={}
        for policy in POLICIES:
            lock=freeze_threshold(validation,policy=policy,coverage_target=target)
            if lock["threshold"] is None:
                row[policy]={**lock,"holdout":None,"unreachable":True}
                continue
            stats,cases=evaluate_selected(
                holdout,policy=policy,cutoff=float(lock["threshold"]))
            row[policy]={**lock,"holdout":stats,"unreachable":False}
            selected[policy]=cases
        if "B_guarded2_floor60_spread20" in selected and "C_uncalibrated_mean_floor60" in selected:
            row["paired_B_vs_C_on_overlap_only"]=paired_overlap(
                selected["B_guarded2_floor60_spread20"],
                selected["C_uncalibrated_mean_floor60"],
                first_policy="B_guarded2_floor60_spread20",
                second_policy="C_uncalibrated_mean_floor60",
            )
        if "original_manager" in selected and "B_guarded2_floor60_spread20" in selected:
            row["paired_original_vs_B_on_overlap_only"]=paired_overlap(
                selected["original_manager"],
                selected["B_guarded2_floor60_spread20"],
                first_policy="original_manager",
                second_policy="B_guarded2_floor60_spread20",
            )
        result["curve"][name]=row
        result["descriptive_equal_issued_count_curves"][name]=exact_issued_coverage_descriptive(holdout,target=target)
    result["per_tour_holdout_capacity"]={
        tour:{
            "first_run_targets":sum(r["tour"]==tour for r in holdout),
            "settled":sum(r["tour"]==tour and r["winner"] in (1,2) for r in holdout),
            "candidate_counts":{
                p:sum(r["tour"]==tour and r["candidates"][p] is not None for r in holdout)
                for p in POLICIES
            },
        }
        for tour in ("ATP_MAIN","CHALLENGER")
    }
    result["interpretation"]=(
        "These validation-frozen confidence cutoffs use only earlier first-issued "
        "model scores, no outcomes or later confidence distributions. They do NOT "
        "train new models or prove forward superiority; November holdout has been "
        "inspected in earlier studies. Policy candidate capacity limits achievable "
        "first-run target coverage. First-run targets exclude matches not discovered "
        "by the system and cannot substitute for independently enumerated official fixtures. "
        "A/B/C different selected populations require identical-match and "
        "equivalent-coverage comparisons, not raw percentages alone. "
        "Only original Manager was actually issued. Exact-K descriptive curves "
        "rank the FULL later evaluation cohort by original scores and are "
        "therefore retrospective and NOT usable as frozen live thresholds."
    )
    return result


def run(conn:psycopg.Connection,*,start:datetime,end:datetime,
        as_of:datetime,validation_end:datetime)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    raw=conn.execute(source_sql(),{
        "start":_utc(start),"end":_utc(end),"as_of":_utc(as_of)}).fetchall()
    records,exclude,sha=normalize(raw,start=start,end=end,as_of=as_of)
    return evaluate(records,start=start,end=end,as_of=as_of,
                    validation_end=validation_end,
                    exclusion_counts=exclude,dataset_sha256=sha)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    for field in ("start","end","as_of","validation_end"):
        p.add_argument("--"+field.replace("_","-")+"-utc",required=True)
    x=p.parse_args(argv)
    values=[getattr(x,field+"_utc") for field in
            ("start","end","as_of","validation_end")]
    if any(not value.endswith("Z") for value in values):
        p.error("all timestamps must explicitly end in UTC Z")
    start,end,as_of,validation_end=[
        datetime.fromisoformat(value.replace("Z","+00:00")) for value in values
    ]
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        result=run(conn,start=start,end=end,as_of=as_of,validation_end=validation_end)
    print("TENNIS_MANAGER_ABC_VALIDATION_FROZEN_RISK_COVERAGE "+
          json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
