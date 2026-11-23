"""Read-only original-manager false-veto AND avoided-loss counterfactuals.

Use first completed nonexperiment prediction-run only, original PIT agent
forecasts and stored Manager decision; no reconstructed AI predictions, odds,
retro bets or post-issue features. A proposed B/C decision is never treated
as the actual issued Model Pick. All matches retain ungraded cases.

This is DESCRIPTIVE historical research on already inspected cases. B/C
selection rules were not set on a fresh independent validation cohort.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES, PREMATCH_MINUTES, _available_agents, _policy, _utc, _wilson,
    source_sql,
)

VERSION="MANAGER_FIRST_RUN_FALSE_VETO_AND_ADMITTED_LOSS_AUDIT_V1"
ALT={
    "B_guarded2_floor60_spread20":"B",
    "C_uncalibrated_mean_floor60":"C",
}


def _group(rows:list[tuple], *,start:datetime,end:datetime,
           as_of:datetime)->tuple[list[dict[str,Any]],dict[str,int],str]:
    start,end,as_of=_utc(start),_utc(end),_utc(as_of)
    if not start<end<=as_of or (end-start).days>120:
        raise ValueError("invalid original fixture window")
    by_match:dict[int,list[tuple]]=defaultdict(list)
    for raw in rows:
        by_match[int(raw[0])].append(raw)
    exclusions=Counter()
    records=[]
    digest=hashlib.sha256()
    for mid in sorted(by_match):
        bundle=by_match[mid]
        first=bundle[0]
        (match_id,run_id,issued,match_start,tour,edition_id,
         _code,_status,_side,_conf,_p_asof,
         manager_status,manager_reason,manager_side,manager_conf,
         outcome_status,winner_side,result_known)=first
        if any((r[1:6],r[11:])!=(first[1:6],first[11:]) for r in bundle):
            raise ValueError("mixed first-run / manager / outcomes for match")
        if (issued is None or match_start is None or
            edition_id is None or tour not in ("ATP_MAIN","CHALLENGER") or
            _utc(issued)>_utc(match_start)-timedelta(minutes=PREMATCH_MINUTES)):
            exclusions["NOT_SAFE_FIRST_PREMATCH_ISSUE"]+=1
            continue
        if not start<=_utc(match_start)<end or _utc(issued)>as_of:
            raise ValueError("fixture/time beyond locked window")
        preds={}
        for r in bundle:
            code,status,side,prob,p_asof=r[6:11]
            if code is None:
                continue
            if code not in CODES or code in preds:
                raise ValueError("duplicate/unexpected original agent")
            if p_asof is None or _utc(p_asof)>_utc(issued):
                raise ValueError("future agent prediction timestamp")
            preds[code]=(str(status),int(side) if side is not None else None,
                         float(prob) if prob is not None else None)
        active=_available_agents(preds)
        alts=_policy(active)
        original=None
        if manager_status=="CONSENSUS":
            if manager_side not in (1,2):
                raise ValueError("original Manager consensus missing selected side")
            if manager_conf is None or not .5<=float(manager_conf)<1:
                raise ValueError("original Manager consensus invalid confidence")
            original=int(manager_side)
        elif manager_status not in ("NO_BET",None):
            raise ValueError("invalid original Manager status")
        result=(
            int(winner_side) if outcome_status=="COMPLETED" and winner_side in (1,2)
            and result_known is not None and
            _utc(result_known)>=_utc(match_start) and _utc(result_known)<=as_of
            else None
        )
        digest.update((
            f"{mid}|{run_id}|{_utc(issued).isoformat()}|{tour}|"
            f"{sorted(preds.items())}|{manager_status}|{manager_reason}|"
            f"{original}|{outcome_status}|{result}\n"
        ).encode())
        records.append({
            "tour":tour,
            "manager":original,
            "manager_reason":str(manager_reason or "NO_REASON"),
            "a3_status":preds.get(CODES[2],("MISSING",None,None))[0],
            "result":result,
            "B":alts["B_guarded2_floor60_spread20"],
            "C":alts["C_uncalibrated_mean_floor60"],
        })
    return records,dict(sorted(exclusions.items())),digest.hexdigest()


def _grade(cases:list[dict[str,Any]],selection:str)->dict[str,Any]:
    issued=[r for r in cases if r[selection] is not None]
    graded=[r for r in issued if r["result"] in (1,2)]
    wins=sum(
        (r[selection] if selection=="manager" else r[selection][0])
        ==r["result"] for r in graded
    )
    return {
        "issued":len(issued),"settled":len(graded),
        "ungraded":len(issued)-len(graded),
        "wins":wins,"losses":len(graded)-wins,
        "accuracy":wins/len(graded) if graded else None,
        "wilson95":_wilson(wins,len(graded)),
    }


def _contingency(cases:list[dict[str,Any]],alt:str)->dict[str,Any]:
    candidates=[r for r in cases if r[alt] is not None]
    added=[r for r in candidates if r["manager"] is None]
    removed=[r for r in cases if r["manager"] is not None and r[alt] is None]
    both=[r for r in candidates if r["manager"] is not None]
    graded_both=[r for r in both if r["result"] in (1,2)]
    label_counts=Counter()
    for r in graded_both:
        old_correct=r["manager"]==r["result"]
        alt_correct=r[alt][0]==r["result"]
        label_counts["both_correct" if old_correct and alt_correct else
                     "only_original_correct" if old_correct else
                     "only_experiment_correct" if alt_correct else
                     "both_wrong"]+=1
    by_reason=defaultdict(list)
    for r in added:
        by_reason[r["manager_reason"]].append(r)
    a3=defaultdict(list)
    for r in added:
        a3[r["a3_status"]].append(r)
    return {
        "alternative_total":_grade(cases,alt),
        "original_manager_total":_grade(cases,"manager"),
        "alternative_admits_original_rejections":_grade(added,alt),
        "original_manager_selections_removed_by_alternative":_grade(removed,"manager"),
        "both_select_same_match":len(both),
        "same_match_both_settled":len(graded_both),
        "on_shared_settled":dict(sorted(label_counts.items())),
        "different_side_on_shared_settled":sum(r["manager"]!=r[alt][0] for r in graded_both),
        "admitted_rejected_by_original_reason":{
            reason:_grade(v,alt) for reason,v in sorted(by_reason.items())
        },
        "admitted_rejected_by_A3_status":{
            status:_grade(v,alt) for status,v in sorted(a3.items())
        },
        "not_prospective":True,
        "not_equal_eligible_coverage":True,
        "no_claim_of_causal_false_veto":True,
    }


def summarize(rows:list[tuple],*,start:datetime,end:datetime,
              as_of:datetime)->dict[str,Any]:
    cases,exclusions,digest=_group(rows,start=start,end=end,as_of=as_of)
    all_counts={alt:_contingency(cases,alt) for alt in ("B","C")}
    by_tour={}
    for tour in ("ATP_MAIN","CHALLENGER"):
        subset=[r for r in cases if r["tour"]==tour]
        by_tour[tour]={
            "original_first_run_matches":len(subset),
            "safely_settled":sum(r["result"] in (1,2) for r in subset),
            "a3_abstained":sum(r["a3_status"]=="ABSTAINED" for r in subset),
            "manager_rejection_reasons":dict(sorted(Counter(
                r["manager_reason"] for r in subset if r["manager"] is None
            ).items())),
            "comparisons":{alt:_contingency(subset,alt) for alt in ("B","C")},
        }
    return {
        "version":VERSION,"status":"ORIGINAL_RUN_DESCRIPTIVE_COUNTERFACTUAL_ONLY",
        "as_of":_utc(as_of).isoformat(),"start":_utc(start).isoformat(),
        "end":_utc(end).isoformat(),"dataset_sha256":digest,
        "original_unique_first_run_matches":len(cases),
        "exclusions":exclusions,
        "original_manager_consensus":_grade(cases,"manager"),
        "original_manager_rejected":sum(r["manager"] is None for r in cases),
        "a3_status_distribution":dict(sorted(Counter(
            r["a3_status"] for r in cases
        ).items())),
        "counterfactuals":all_counts,"by_tour":by_tour,
        "observed_outcomes_unchanged":True,"db_writes":False,
        "production_manager_changed":False,"market_value_gate_used":False,
        "official_eligible_fixture_denominator":False,
        "notes":[
            "False-veto is colloquial: an admitted winner is matched by an admitted loss and by all ungraded cases; no retrospective rule is approved.",
            "Counts are conditional on first completed, genuinely before-match targeted production runs; not every official eligible ATP/Challenger fixture.",
            "B/C were tested on earlier research and are NOT truly untouched chronological or prospective holdouts.",
            "Original Manager consensus and experimental alternatives select different fixture sets; shared-match discordance is reported separately.",
            "No bookmaker odds, stake profit or legally verified competitor API forecasts are in this evaluation.",
            "Existing original Manager may permit two-agent fallback; experiment B/C are not necessarily its previously deployed rule.",
        ],
    }


def run(conn:psycopg.Connection,*,start:datetime,
        end:datetime,as_of:datetime)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(source_sql(),{
        "start":_utc(start),"end":_utc(end),"as_of":_utc(as_of),
    }).fetchall()
    return summarize(rows,start=start,end=end,as_of=as_of)


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ("start","end","as_of"):
        parser.add_argument("--"+field.replace("_","-")+"-utc",required=True)
    a=parser.parse_args(argv)
    texts=[getattr(a,v+"_utc") for v in ("start","end","as_of")]
    if any(not v.endswith("Z") for v in texts):
        parser.error("all dates must be UTC Z")
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    times=[datetime.fromisoformat(s.replace("Z","+00:00")) for s in texts]
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        result=run(conn,start=times[0],end=times[1],as_of=times[2])
    print("TENNIS_MANAGER_FALSE_VETO_ORIGINALS "+json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
