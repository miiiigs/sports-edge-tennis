"""Capture fixed, genuinely *future* first-issued Manager research shadows.

Runs read-only against the existing immutable original production runs.
Emits each frozen prospective shadow decision to timestamped stdout, one
record per match, followed by a SHA-256 manifest. External preserved logs
serve as research evidence; local generated replay files are not original
publication evidence. For durable recurring capture a separately authorised
append-only collector or scheduled log export is needed.

Already inspected 2026 historical outcomes affected the choice of B/C rules:
freeze is FORWARD-ONLY. No production Manager selection rule changes.
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

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import (
    CODES, _available_agents, _policy, source_sql,
)

VERSION="BRAZZY365_PROSPECTIVE_MANAGER_SHADOW_FROZEN_V2"
# Previously fitted 2026-10-15..2026-11-03 and subsequently INSPECTED
# Nov4..Nov20. These are fixed as research candidates before new forecasts,
# not untouched forward-test thresholds or proof of superiority.
FROZEN = {
    "production_original_as_issued": {
        "threshold":0.0,
        "earlier_validation_target":None,
    },
    "original_manager": {
        "threshold":0.612597,
        "earlier_validation_target":0.10,
    },
    "B_guarded2_floor60_spread20": {
        "threshold":0.667679,
        "earlier_validation_target":0.20,
    },
    "C_uncalibrated_mean_floor60": {
        "threshold":0.7070145,
        "earlier_validation_target":0.20,
    },
    "A1A2_equal_log_odds_blend": {
        "threshold":0.6711558673472298,
        "earlier_validation_target":0.40,
    },
}
TRAINING_VALIDATION_STATEMENT=(
    "Cutoffs sourced from already-inspected October-November 2026 "
    "historical original-run research. Exact policy rules and thresholds "
    "are now frozen for NEW FUTURE first-issued predictions only. "
    "Coverage target denominators differ between prior experiments; "
    "do not compare these target fractions as equivalent achieved coverage."
)


def _instant(t:datetime|None,label:str)->datetime:
    if t is None or t.tzinfo is None:
        raise ValueError(label+" must be timezone-aware")
    return t.astimezone(UTC)


def _p1(active:dict[str,tuple[int,float]], code:str)->float|None:
    row=active.get(code)
    return row[1] if row is not None else None


def _blend(a:float|None,b:float|None)->tuple[int,float]|None:
    if a is None or b is None:
        return None
    if not all(math.isfinite(p) and 0<p<1 for p in (a,b)):
        raise ValueError("nonfinite original agent probabilities")
    logit=lambda p:math.log(p/(1-p))
    value=(logit(a)+logit(b))/2.0
    p=(1/(1+math.exp(-value))) if value>=0 else math.exp(value)/(1+math.exp(value))
    if abs(p-.5)<1e-12:
        return None
    return (1 if p>.5 else 2,p)


def _candidate(name:str,pick:tuple[int,float]|None)->dict[str,Any]:
    config=FROZEN[name]
    if pick is None:
        return {"status":"ABSTAINED","side":None,"p1":None,
                "frozen_confidence_cutoff":config["threshold"]}
    side, p=float(pick[0]),float(pick[1])
    if side not in (1.,2.) or not math.isfinite(p) or not 0<p<1:
        raise ValueError("candidate side/probability invalid")
    if (side==1)!=(p>.5):
        raise ValueError("candidate player orientation mismatch")
    if max(p,1-p)+1e-12<config["threshold"]:
        return {"status":"BELOW_FROZEN_THRESHOLD",
                "side":None,"p1":p,
                "frozen_confidence_cutoff":config["threshold"]}
    return {"status":"SELECTED","side":int(side),"p1":p,
            "frozen_confidence_cutoff":config["threshold"]}


def collect(rows:list[tuple], *,
            cutoff:datetime,captured_at:datetime,
            window_start:datetime,window_end:datetime,
            source_sha:str,
            only_run_ids:set[int]|None=None)->dict[str,Any]:
    cutoff,captured_at,window_start,window_end=map(
        lambda x:_instant(x[1],x[0]),(
            ("cutoff",cutoff),("captured_at",captured_at),
            ("window_start",window_start),("window_end",window_end)
        ))
    if captured_at<cutoff or (captured_at-cutoff)>timedelta(minutes=5):
        raise ValueError("capture must occur within five minutes of data query cutoff")
    if not cutoff<=window_start<window_end:
        raise ValueError("only genuinely future schedule can be captured")
    if not source_sha or len(source_sha)<12:
        raise ValueError("exact source Git revision required")
    grouped=defaultdict(list)
    for raw in rows:
        grouped[int(raw[0])].append(raw)
    accepted=[]
    excluded=Counter()
    digest=hashlib.sha256()
    for match_id in sorted(grouped):
        records=grouped[match_id]
        if only_run_ids is not None and int(records[0][1]) not in only_run_ids:
            continue
        first=records[0]
        (mid,run_id,issued,match_start,tour,edition_id,
         _agent_code,_status,_side,_prob,_pred_asof,
         manager_status,manager_reason,manager_side,manager_probability,
         result_status,result_winner,result_known)=first
        if any((r[1:6],r[11:])!=(first[1:6],first[11:]) for r in records):
            raise ValueError("mixed first run or result records")
        if match_start is None or issued is None or edition_id is None:
            excluded["INCOMPLETE_ORIGINAL_MATCH_EVIDENCE"]+=1
            continue
        start=_instant(match_start,"original match start")
        issued_at=_instant(issued,"first issued")
        if not window_start<=start<window_end:
            raise ValueError("fixture not in frozen future window")
        if (tour not in ("ATP_MAIN","CHALLENGER")
            or issued_at>cutoff
            or captured_at>start-timedelta(minutes=30)
            or issued_at>start-timedelta(minutes=30)):
            excluded["LATE_UNSAFE_OR_OUT_OF_SCOPE"]+=1
            continue
        # Fail closed: first observed after the match is NOT a frozen shadow.
        if result_status=="COMPLETED" or result_winner in (1,2):
            raise ValueError("prospective fixture cannot have observed final winner")
        preds={}
        prediction_times={}
        for row in records:
            code,status,side,prob,pred_at=row[6:11]
            if code is None:
                continue
            if code in preds or code not in CODES:
                raise ValueError("duplicate or unexpected production agent")
            if pred_at is None or _instant(pred_at,"agent as_of")>issued_at:
                raise ValueError("future agent prediction in first run")
            preds[code]=(str(status),
                         int(side) if side is not None else None,
                         float(prob) if prob is not None else None)
            prediction_times[code]=_instant(pred_at,"agent as_of").isoformat()
        active=_available_agents(preds)
        proposed=_policy(active)
        proposed["original_manager"]=None
        if manager_status=="CONSENSUS":
            if manager_side not in (1,2) or manager_probability is None:
                raise ValueError("original Manager candidate incomplete")
            conf=float(manager_probability)
            if not .5<=conf<1 or not math.isfinite(conf):
                raise ValueError("invalid original Manager probability")
            proposed["original_manager"]=(
                int(manager_side), conf if int(manager_side)==1 else 1-conf
            )
        elif manager_status not in ("NO_BET",None):
            raise ValueError("invalid original Manager status")
        a=_p1(active,CODES[0])
        b=_p1(active,CODES[1])
        proposed["production_original_as_issued"]=proposed["original_manager"]
        proposed["A1A2_equal_log_odds_blend"]=_blend(a,b)
        selections={name:_candidate(name,proposed.get(name)) for name in FROZEN}
        record={
            "version":VERSION,
            "canonical_match_id":int(mid),
            "first_prediction_run_id":int(run_id),
            "tour":str(tour),
            "original_issued_at_utc":issued_at.isoformat(),
            "scheduled_start_at_utc":start.isoformat(),
            "captured_at_utc":captured_at.isoformat(),
            "original_manager_status":manager_status or "MISSING",
            "original_manager_reason":manager_reason,
            "original_agent_statuses":{
                code:preds.get(code,("MISSING",None,None))[0] for code in CODES
            },
            # V2: publish exact persisted ORIGINAL agent probabilities, not
            # only an ensemble. Keep source prediction times separate from
            # unverified underlying provider feature publication times.
            "original_agent_forecasts":{
                code:{
                    "status":preds.get(code,("MISSING",None,None))[0],
                    "selection_side":active[code][0] if code in active else None,
                    "player_1_probability":active[code][1] if code in active else None,
                    "prediction_as_of_utc":prediction_times.get(code),
                } for code in CODES
            },
            "underlying_feature_cutoff_provenance_complete":False,
            "underlying_feature_snapshot_sha256":None,
            "original_production_model_version_unavailable":True,
            "frozen_research_definition_commit":source_sha,
            "frozen_research_policies":selections,
            "not_a_published_model_pick":True,
            "new_feature_data_used":False,
            "outcomes_available_during_capture":False,
        }
        canonical=json.dumps(record,sort_keys=True,separators=(",",":"))
        digest.update((canonical+"\n").encode())
        accepted.append(record)
    return {
        "version":VERSION,
        "research_freeze_code_sha":source_sha,
        "original_query_cutoff_utc":cutoff.isoformat(),
        "capture_time_utc":captured_at.isoformat(),
        "window_start_utc":window_start.isoformat(),
        "window_end_utc":window_end.isoformat(),
        "first_issued_future_targets":len(accepted),
        "filtered_to_first_run_ids":sorted(only_run_ids) if only_run_ids is not None else None,
        "discarded_counts":dict(sorted(excluded.items())),
        "selection_counts":{
            name:sum(rec["frozen_research_policies"][name]["status"]=="SELECTED"
                     for rec in accepted) for name in FROZEN
        },
        "utc_capture_records_sha256":digest.hexdigest(),
        "frozen_rules":FROZEN,
        "prior_evidence_warning":TRAINING_VALIDATION_STATEMENT,
        "official_complete_fixture_denominator":None,
        "settled_match_results_at_capture":0,
        "genuine_future_shadow_records":accepted,
        "no_database_writes":True,"no_market_api_calls":True,
        "cannot_claim_future_accuracy_until_outcomes":True,
        "note":"External preservation of original timestamped logs required; after-the-fact rerun is historical reconstruction, NOT original frozen publication.",
    }


def run(conn:psycopg.Connection, *, captured_at:datetime,
        start:datetime,end:datetime,source_sha:str,
        only_run_ids:set[int]|None=None)->dict[str,Any]:
    # Capture once at query time. Do not substitute retrospective as-of.
    captured_at=_instant(captured_at,"captured_at")
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(source_sql(),{
        "as_of":captured_at,"start":_instant(start,"start"),
        "end":_instant(end,"end"),
    }).fetchall()
    return collect(rows,cutoff=captured_at,captured_at=captured_at,
                   window_start=start,window_end=end,source_sha=source_sha,
                   only_run_ids=only_run_ids)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-git-sha",required=True)
    p.add_argument("--horizon-days",type=int,default=7)
    a=p.parse_args(argv)
    if not 1<=a.horizon_days<=14:
        p.error("horizon days must be in [1,14]")
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL is required")
    original_now=datetime.now(UTC)
    start=original_now+timedelta(minutes=30)
    end=original_now+timedelta(days=a.horizon_days)
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        report=run(conn,captured_at=original_now,start=start,end=end,
                   source_sha=a.source_git_sha)
    # One immutable canonical JSON record per line. The final digest binds
    # ordering/content; GitHub+Railway log timestamps bind actual capture.
    for record in report.pop("genuine_future_shadow_records"):
        print("TENNIS_FROZEN_PROSPECTIVE_CANDIDATE "+json.dumps(record,sort_keys=True),flush=True)
    print("TENNIS_FROZEN_PROSPECTIVE_SUMMARY "+json.dumps(report,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
