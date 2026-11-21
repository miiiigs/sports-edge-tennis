"""Research-only original-run Agent/Manager A–C descriptive shadow.

This is not trained/calibrated Manager D/E, not a policy proposal for live
activation, and does not use market odds. A3 abstentions remain explicit.
The only selection/forecast evidence is each match's first completed
nonexperiment production run, before fixture start. Never reconstruct
retroactive postmatch forecasts or optimize thresholds from outcomes.
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

from sports_edge_tennis.performance.calibration import brier, log_loss

VERSION="MANAGER_ABC_FIRST_ISSUED_PIT_ABLATION_SHADOW_V1"
CODES=("AGENT_1_SURFACE_ELO","AGENT_2_HIERARCHICAL_MARKOV",
       "AGENT_3_INTRANSITIVITY")
PREMATCH_MINUTES=30
FLOOR=.60


def source_sql()->str:
    return """
        WITH first_run AS (
          SELECT DISTINCT ON(t.match_id)
             t.match_id,pr.prediction_run_id,
             pr.prediction_finalized_at AS issued_at
          FROM prediction_run_targets t
          JOIN prediction_runs pr ON pr.prediction_run_id=t.prediction_run_id
          WHERE pr.run_status='COMPLETED'
            AND pr.prediction_finalized_at IS NOT NULL
            AND pr.prediction_finalized_at <= %(as_of)s
            AND pr.experiment_id IS NULL
            AND pr.prediction_contract_version='predictive_agent_result_v1'
          ORDER BY t.match_id,pr.prediction_finalized_at,pr.prediction_run_id
        )
        SELECT fr.match_id,fr.prediction_run_id,fr.issued_at,
               v.match_start_at,v.tour_scope::text AS tour,
               m.tournament_edition_id,
               p.agent_code::text,p.prediction_status::text,
               p.selection_side,p.win_probability,p.as_of,
               mc.manager_status::text,mc.reason_code::text,
               mc.selected_side,mc.consensus_probability,
               res.outcome_status,res.winner_side,res.known_at
        FROM first_run fr
        JOIN matches m ON m.match_id=fr.match_id
        LEFT JOIN LATERAL (
          SELECT match_start_at,tour_scope
          FROM match_versions mv
          WHERE mv.match_id=fr.match_id
            AND mv.as_of <= fr.issued_at
            AND mv.source_observed_at <= fr.issued_at
            AND mv.ingested_at <= fr.issued_at
          ORDER BY mv.as_of DESC,mv.source_observed_at DESC,
                   mv.match_version_id DESC LIMIT 1
        ) v ON TRUE
        LEFT JOIN predictions p
          ON p.prediction_run_id=fr.prediction_run_id
         AND p.match_id=fr.match_id
         AND p.experiment_id IS NULL
        LEFT JOIN manager_consensus_results mc
          ON mc.prediction_run_id=fr.prediction_run_id
         AND mc.match_id=fr.match_id
        LEFT JOIN LATERAL (
          SELECT outcome_status::text AS outcome_status,winner_side,
                 GREATEST(as_of,source_observed_at,ingested_at) AS known_at
          FROM match_result_observations r
          WHERE r.match_id=fr.match_id
            AND r.as_of<=%(as_of)s
            AND r.source_observed_at<=%(as_of)s
            AND r.ingested_at<=%(as_of)s
          ORDER BY r.as_of DESC,r.source_observed_at DESC,
                   r.ingested_at DESC,r.match_result_observation_id DESC
          LIMIT 1
        ) res ON TRUE
        WHERE v.match_start_at >= %(start)s
          AND v.match_start_at < %(end)s
        ORDER BY fr.match_id,p.agent_code
    """


def _utc(t:datetime)->datetime:
    if t is None or t.tzinfo is None:
        raise ValueError("timestamp is missing or lacks timezone")
    return t.astimezone(UTC)


def _wilson(wins:int,n:int)->list[float]|None:
    if not n:
        return None
    z=1.959963984540054
    p=wins/n
    denom=1+z*z/n
    mid=(p+z*z/(2*n))/denom
    half=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/denom
    return [max(0,mid-half),min(1,mid+half)]


def _available_agents(preds:dict[str,tuple[str,int|None,float|None]])->dict[str,tuple[int,float]]:
    active={}
    for code in CODES:
        rec=preds.get(code)
        if rec is None:
            continue
        status,side,confidence=rec
        if status=="ABSTAINED":
            if side is not None or confidence is not None:
                raise ValueError("abstaining agent cannot supply a predicted side")
            continue
        if status!="PREDICTED" or side not in (1,2) or confidence is None:
            raise ValueError("active original agent result has invalid status/side")
        c=float(confidence)
        if not math.isfinite(c) or not .5<=c<1:
            raise ValueError("out-of-range historical agent confidence")
        active[code]=(int(side),c if int(side)==1 else 1-c)
    return active


def _policy(active:dict[str,tuple[int,float]])->dict[str,tuple[int,float]|None]:
    """A 3/3, B 2+ guarded, C raw probability mean; all frozen parameters.

    B is an exploratory, NOT validated safeguard: >=2 agents agree on side,
    each agreeing member >=60% and spread <=20 percentage points.
    C is explicitly *uncalibrated*: mean p1 of >=2 valid agent forecasts,
    publish if confidence >=60%. D/E need fit on chronological OOS inputs.
    """
    a=b=c=None
    values=list(active.values())
    side_counts=Counter(side for side,_ in values)
    if len(values)==3 and len(side_counts)==1:
        side=values[0][0]
        probability=sum(p for _,p in values)/3
        conf=max(probability,1-probability)
        if conf >= FLOOR:
            a=(side,probability)
    if len(values)>=2:
        agreeing=side_counts.most_common()
        if agreeing[0][1]>=2 and (len(agreeing)==1 or agreeing[0][1]>agreeing[1][1]):
            side=agreeing[0][0]
            agree=[p for s,p in values if s==side]
            confi=[max(p,1-p) for p in agree]
            if (min(confi)>=FLOOR and max(confi)-min(confi)<=.20):
                all_p=sum(p for s,p in values if s==side)/len(agree)
                b=(side,all_p)
        average=sum(p for _,p in values)/len(values)
        confidence=max(average,1-average)
        if confidence>=FLOOR and not math.isclose(average,.5,abs_tol=1e-12):
            c=(1 if average>.5 else 2,average)
    return {"A_unanimous3_floor60":a,
            "B_guarded2_floor60_spread20":b,
            "C_uncalibrated_mean_floor60":c}


def summarize(rows:list[tuple], *, start:datetime,end:datetime,as_of:datetime)->dict[str,Any]:
    start,end,as_of=_utc(start),_utc(end),_utc(as_of)
    if not start<end<=as_of:
        raise ValueError("require start<end<=asof")
    if (end-start).days>120:
        raise ValueError("maximum 120-day review window")
    groups=defaultdict(list)
    for raw in rows:
        groups[int(raw[0])].append(raw)
    exclusions=Counter()
    records=[]
    digest=hashlib.sha256()
    for mid in sorted(groups):
        bundle=groups[mid]
        base=bundle[0]
        (match_id,run_id,issued,start_at,tour,edition_id,
         _code,_status,_side,_conf,_pred_as_of,
         mc_status,mc_reason,mc_side,mc_conf,
         res_status,res_winner,res_known)=base
        # All repeated rows must be a single immutable original run/fixture.
        if any((r[1],r[2],r[3],r[4],r[5],r[11:])!=
               (run_id,issued,start_at,tour,edition_id,base[11:]) for r in bundle):
            raise ValueError("inconsistent joined original match / result / Manager")
        if (start_at is None or issued is None or edition_id is None or
            tour not in ("ATP_MAIN","CHALLENGER") or
            _utc(issued)>_utc(start_at)-timedelta(minutes=PREMATCH_MINUTES)):
            exclusions["MISSING_OR_LATE_FIRST_ISSUED_PIT"]+=1
            continue
        pred={}
        for r in bundle:
            code,status,side,prob,pred_time=r[6:11]
            if code is None:
                continue
            if code not in CODES or code in pred:
                raise ValueError("unexpected/duplicate first issue Agent row")
            if pred_time is None or _utc(pred_time)>_utc(issued):
                raise ValueError("future prediction feature timestamp")
            pred[code]=(str(status),int(side) if side is not None else None,
                       float(prob) if prob is not None else None)
        active=_available_agents(pred)
        proposed=_policy(active)
        manager=None
        if mc_status=="CONSENSUS" and mc_side in (1,2) and mc_conf is not None:
            prob=float(mc_conf)
            if not .5<=prob<1:
                raise ValueError("invalid existing Manager confidence")
            manager=(int(mc_side),prob if int(mc_side)==1 else 1-prob)
        outcome=(
            int(res_winner) if res_status=="COMPLETED" and res_winner in (1,2)
            and res_known is not None and _utc(res_known)>=_utc(start_at)
            and _utc(res_known)<=as_of else None
        )
        if outcome is None:
            exclusions["UNSETTLED_OR_VOID_GRADING_NOT_LOSS"]+=1
        digest.update((
            f"{mid}|{run_id}|{_utc(issued).isoformat()}|{tour}|"
            f"{sorted(pred.items())}|{res_status}|{outcome}\n"
        ).encode())
        records.append({
            "match_id":mid,"run_id":int(run_id),"tour":str(tour),
            "edition":str(edition_id),
            "a3_predicted":CODES[2] in active,
            "a3_abstained":pred.get(CODES[2],("",None,None))[0]=="ABSTAINED",
            "a3_missing":CODES[2] not in pred,
            "agents_predicted":len(active),
            "original_manager":manager,
            "original_manager_status":str(mc_status or "MISSING"),
            "original_manager_reason":str(mc_reason or "NONE"),
            "outcome":outcome,
            "policies":{**proposed,"original_manager":manager},
        })
    names=(*_policy({}).keys(),"original_manager")
    def grade(population:list[dict],name:str)->dict[str,Any]:
        issued=[(r,r["policies"].get(name)) for r in population if
                r["policies"].get(name) is not None]
        settled=[(r,p) for r,p in issued if r["outcome"] in (1,2)]
        won=sum(side==r["outcome"] for r,(side,p1) in settled)
        y=[(p1, int(r["outcome"]==1)) for r,(side,p1) in settled]
        return {
            "first_run_eligible_targets":len(population),
            "issued":len(issued),"settled":len(settled),
            "ungraded":len(issued)-len(settled),
            "wins":won,"losses":len(settled)-won,
            "issued_coverage_of_first_run_targets":len(issued)/len(population) if population else None,
            "winner_accuracy":won/len(settled) if settled else None,
            "accuracy_wilson95":_wilson(won,len(settled)),
            "brier":brier(y),"log_loss":log_loss(y),
            "not_full_eligible_coverage":True,
        }
    per_tour={}
    for tour in ("ATP_MAIN","CHALLENGER"):
        data=[r for r in records if r["tour"]==tour]
        per_tour[tour]={
            "first_issued_matches":len(data),
            "settled_for_grading":sum(r["outcome"] in (1,2) for r in data),
            "a3_predicted":sum(r["a3_predicted"] for r in data),
            "a3_abstained":sum(r["a3_abstained"] for r in data),
            "a3_missing":sum(r["a3_missing"] for r in data),
            "original_manager_reasons":dict(sorted(Counter(
                r["original_manager_reason"] for r in data
                if r["original_manager_status"]!="CONSENSUS"
            ).items())),
            "policies":{n:grade(data,n) for n in names},
        }
    return {
        "version":VERSION,"status":"HISTORICAL_ORIGINAL_RUN_SHADOW_ONLY",
        "window_start_utc":start.isoformat(),"window_end_utc":end.isoformat(),
        "as_of_utc":as_of.isoformat(),
        "dataset_sha256":digest.hexdigest(),
        "first_run_candidates":len(groups),
        "first_issued_pre_match_cohort":len(records),
        "unsafe_or_ungraded_counts":dict(sorted(exclusions.items())),
        "a3_predicted":sum(r["a3_predicted"] for r in records),
        "a3_abstained":sum(r["a3_abstained"] for r in records),
        "a3_missing":sum(r["a3_missing"] for r in records),
        "policies":{n:grade(records,n) for n in names},
        "per_tour":per_tour,
        "no_market_inputs":True,
        "db_writes":False,"production_rules_changed":False,
        "manager_d_validation_weights":"NOT_ASSESSED_NEEDS_PRIOR_VALIDATION",
        "manager_e_chronological_stacking":"NOT_ASSESSED_NEEDS_PREISSUED_OOS_TRAINING_AND_A3_SUPPORT",
        "cannot_claim_greenlight_validated":True,
        "full_eligible_fixture_coverage_verified":False,
        "interpretation":(
            "A and B are predeclared descriptive heuristics, C is raw (not "
            "calibrated) averaging. A3 abstentions are not losses. Original "
            "Manager historical consensus is evaluated separately from "
            "market bets or dashboard first-issued publication. This is "
            "not a walk-forward trained, prospectively frozen, full-slate or "
            "independent external competitor evaluation."
        ),
    }


def run(conn:psycopg.Connection, *, start:datetime,end:datetime,
        as_of:datetime)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    return summarize(conn.execute(source_sql(),{
        "start":_utc(start),"end":_utc(end),"as_of":_utc(as_of),
    }).fetchall(),start=start,end=end,as_of=as_of)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-start-utc",required=True)
    p.add_argument("--window-end-utc",required=True)
    p.add_argument("--as-of-utc",required=True)
    a=p.parse_args(argv)
    if any(not getattr(a,k).endswith("Z") for k in
           ("window_start_utc","window_end_utc","as_of_utc")):
        p.error("all timestamps must be UTC Z")
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL required")
    start,end,as_of=[
        datetime.fromisoformat(getattr(a,k).replace("Z","+00:00"))
        for k in ("window_start_utc","window_end_utc","as_of_utc")
    ]
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        out=run(conn,start=start,end=end,as_of=as_of)
    print("TENNIS_MANAGER_ABC_FIRST_ISSUE "+json.dumps(out,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
