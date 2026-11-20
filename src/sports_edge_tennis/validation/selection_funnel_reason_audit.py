"""Read-only same-run Model Picks funnel audit with mutually clear stage counts.

Predictions -> Manager -> dashboard-eligible Model Picks are not the same
population as independent market-qualified betting decisions. Reconcile by
canonical match ID and preserve every exclusion reason without inventing an
upstream provider-eligible fixture count that wasn't snapshotted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)

VERSION="RUN_SPECIFIC_PIT_SELECTION_FUNNEL_V1"
AGENTS=(
    "AGENT_1_SURFACE_ELO",
    "AGENT_2_HIERARCHICAL_MARKOV",
    "AGENT_3_INTRANSITIVITY",
)


def discover(conn:psycopg.Connection, *, cutoff:datetime,
             limit:int=200)->list[dict[str,Any]]:
    if cutoff.tzinfo is None or not 1<=limit<=500:
        raise ValueError("aware cutoff and limit 1..500 required")
    rows=conn.execute("""
        WITH recent AS (
            SELECT prediction_run_id, prediction_finalized_at
            FROM prediction_runs
            WHERE run_status='COMPLETED'
              AND prediction_finalized_at IS NOT NULL
              AND prediction_finalized_at<=%s
              AND experiment_id IS NULL
            ORDER BY prediction_finalized_at DESC,prediction_run_id DESC
            LIMIT %s
        )
        SELECT pr.prediction_run_id,pr.prediction_finalized_at,
               (SELECT count(*) FROM prediction_run_targets t
                WHERE t.prediction_run_id=pr.prediction_run_id) AS targets,
               (SELECT count(*) FROM manager_consensus_results m
                WHERE m.prediction_run_id=pr.prediction_run_id
                  AND m.manager_status='CONSENSUS') AS consensus,
               (SELECT count(DISTINCT d.match_id) FROM decisions d
                WHERE d.prediction_run_id=pr.prediction_run_id
                  AND d.signal_type='TYPE_A_MODEL_EDGE'
                  AND d.decision_status='BET') AS market_bets
        FROM recent pr
        ORDER BY pr.prediction_finalized_at DESC,pr.prediction_run_id DESC
        """,(cutoff,limit)).fetchall()
    return [{
        "prediction_run_id":int(mid),
        "finalized_at":finalized.isoformat(),
        "targets":int(targets),
        "manager_consensus":int(consensus),
        "market_bet_matches":int(bets),
    } for mid,finalized,targets,consensus,bets in rows]


def _reason(agent_rows:dict[str,tuple[str,int|None,float|None]],
            manager:tuple[str,str,int|None,Decimal|None]|None,
            aligned_type:str|None)->str:
    if aligned_type=="MANAGER_CONSENSUS":
        return "DASHBOARD_MANAGER_CONSENSUS_PICK"
    if aligned_type=="A1_A2_WIDE_SPREAD":
        return "DASHBOARD_A1_A2_TRACKING_ONLY"
    missing=[name for name in AGENTS if name not in agent_rows]
    if missing:
        return "MISSING_AGENT_RECORD:" + ",".join(missing)
    if manager is None:
        return "MISSING_MANAGER_RECORD"
    status,reason,_,prob=manager
    if status=="CONSENSUS":
        if prob is not None and prob<Decimal(".60"):
            return "MANAGER_CONSENSUS_BELOW_60_PCT_DASHBOARD_FLOOR"
        return "CONSENSUS_NOT_IN_FIRST_ALIGNED_LEDGER"
    return "MANAGER_REJECTED:" + reason


def summarise(*, run_id:int,targets:list[int],
              agent_rows:dict[int,dict[str,tuple[str,int|None,float|None]]],
              managers:dict[int,tuple[str,str,int|None,Decimal|None]],
              aligned:dict[int,tuple[str,Decimal]],
              market:dict[int,list[tuple[str,str,str,bool]]],
              finalized_at:datetime)->dict[str,Any]:
    if len(set(targets))!=len(targets):
        raise ValueError("duplicate canonical targets")
    tset=set(targets)
    if any(mid not in tset for mid in aligned):
        raise ValueError("aligned picks exist outside run targets")
    if any(mid not in tset for mid in managers):
        raise ValueError("Manager exists outside run targets")
    reasons=Counter()
    agent_counts={
        agent:{"predicted":0,"abstained":0,"missing":0}
        for agent in AGENTS
    }
    all_three=0
    details=[]
    for mid in sorted(targets):
        agents=agent_rows.get(mid,{})
        statuses={}
        for code in AGENTS:
            status=agents.get(code,("MISSING",None,None))[0]
            statuses[code]=status
            if status=="PREDICTED":
                agent_counts[code]["predicted"]+=1
            elif status=="ABSTAINED":
                agent_counts[code]["abstained"]+=1
            else:
                agent_counts[code]["missing"]+=1
        if all(statuses[code]=="PREDICTED" for code in AGENTS):
            all_three+=1
        mgr=managers.get(mid)
        pick=aligned.get(mid)
        code=_reason(agents,mgr,pick[0] if pick else None)
        reasons[code]+=1
        decisions=market.get(mid,[])
        if len(decisions)>10:
            raise ValueError("unexpected duplicate market decisions")
        details.append({
            "match_id":mid,
            "agent_statuses":statuses,
            "manager_status":mgr[0] if mgr else "MISSING",
            "manager_reason":mgr[1] if mgr else "MISSING",
            "dashboard_aligned_type":pick[0] if pick else None,
            "dashboard_aligned_confidence":float(pick[1]) if pick else None,
            "classification":code,
            "market_decision_statuses":[
                {"decision_status":d[0],"reason":d[1],"signal_type":d[2]}
                for d in decisions
            ],
            "market_bet":any(x[0]=="BET" and x[2]=="TYPE_A_MODEL_EDGE"
                             for x in decisions),
        })
    digest=hashlib.sha256()
    for d in details:
        digest.update(json.dumps(d,sort_keys=True,separators=(",",":"))
                      .encode("utf-8"))
    all_market=sum(bool(d["market_bet"]) for d in details)
    dashboard=len(aligned)
    manager_cons=sum(m[0]=="CONSENSUS" for m in managers.values())
    confidence70=sum(p>=Decimal(".70") for _,p in aligned.values())
    # These are *independent branches*, not a single linear funnel.
    if dashboard>len(targets) or all_market>len(targets):
        raise ValueError("funnel count exceeds targets")
    return {
        "status":"RUN_FUNNEL_AUDITED_READ_ONLY","version":VERSION,
        "prediction_run_id":run_id,
        "finalized_at":finalized_at.astimezone(UTC).isoformat(),
        "target_count":len(targets),
        "agent_counts":agent_counts,
        "all_three_agents_predicted":all_three,
        "manager_consensus_count":manager_cons,
        "manager_rejected_count":len(targets)-manager_cons,
        "dashboard_aligned_picks":dashboard,
        "dashboard_manager_picks":sum(x[0]=="MANAGER_CONSENSUS" for x in aligned.values()),
        "dashboard_a1a2_tracking_only":sum(x[0]=="A1_A2_WIDE_SPREAD" for x in aligned.values()),
        "aligned_confidence70_research_only_count":confidence70,
        "market_bet_matches":all_market,
        "market_is_separate_from_model_picks":True,
        "per_target_exclusion_reasons":dict(sorted(reasons.items())),
        "detail_sha256":digest.hexdigest(),
        "per_match_evidence":details,
        "provider_eligible_fixtures_before_targeting":"UNAVAILABLE_IN_RUN_LEDGER",
        "production_rules_changed":False,"db_writes":False,
        "qualification_note":(
            "Dashboard-aligned picks are read-model eligible Model Picks, not "
            "proof of individually recorded delivery to a client. Market bet "
            "counts are an independent pricing branch. Confidence>=70 is a "
            "descriptive shadow criterion on same-run aligned picks, not the "
            "frozen multi-day confidence-filter report."
        ),
    }


def read_run(conn:psycopg.Connection, *, run_id:int)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    head=conn.execute("""
        SELECT prediction_finalized_at
        FROM prediction_runs
        WHERE prediction_run_id=%s
          AND run_status='COMPLETED'
          AND prediction_finalized_at IS NOT NULL
          AND experiment_id IS NULL
    """,(run_id,)).fetchone()
    if head is None:
        return {"status":"NOT_COMPLETED_NONEXPERIMENT_RUN",
                "run_id":run_id,"db_writes":False}
    targets=[int(r[0]) for r in conn.execute(
        "SELECT match_id FROM prediction_run_targets "
        "WHERE prediction_run_id=%s ORDER BY match_id",(run_id,)
    ).fetchall()]
    raw=conn.execute("""
        SELECT match_id,agent_code::text,prediction_status::text,
               selection_side,win_probability
        FROM predictions WHERE prediction_run_id=%s
        ORDER BY match_id,agent_code
    """,(run_id,)).fetchall()
    agents={}
    for match_id,code,status,side,p in raw:
        if code in agents.get(int(match_id),{}):
            raise ValueError("duplicate agent result for match/run")
        agents.setdefault(int(match_id),{})[code]=(
            status,int(side) if side is not None else None,
            float(p) if p is not None else None)
    managers={}
    for match_id,status,selected,side,prob in conn.execute("""
        SELECT match_id,manager_status::text,reason_code::text,
               selected_side,consensus_probability
        FROM manager_consensus_results
        WHERE prediction_run_id=%s
    """,(run_id,)).fetchall():
        if int(match_id) in managers:
            raise ValueError("duplicate Manager result for run")
        managers[int(match_id)]=(status,selected,side,prob)
    aligned={}
    rows=conn.execute(
        f"WITH {aligned_production_model_picks_cte()} "
        "SELECT match_id,aligned_pick_type,aligned_probability "
        "FROM aligned_model_picks WHERE prediction_run_id=%s",
        (run_id,)
    ).fetchall()
    for mid,pick_type,prob in rows:
        if int(mid) in aligned:
            raise ValueError("duplicate aligned first-issued Model Pick")
        aligned[int(mid)]=(str(pick_type),Decimal(prob))
    market={}
    for mid,status,reason,signal,published in conn.execute("""
        SELECT match_id,decision_status::text,reason_code::text,
               signal_type::text,published_at IS NOT NULL
        FROM decisions
        WHERE prediction_run_id=%s
          AND signal_type='TYPE_A_MODEL_EDGE'
        ORDER BY match_id,decision_id
    """,(run_id,)).fetchall():
        market.setdefault(int(mid),[]).append(
            (str(status),str(reason),str(signal),bool(published))
        )
    return summarise(run_id=run_id,targets=targets,agent_rows=agents,
                     managers=managers,aligned=aligned,market=market,
                     finalized_at=head[0])


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id",type=int)
    p.add_argument("--as-of-utc",required=True)
    p.add_argument("--targets",type=int,default=26)
    p.add_argument("--consensus",type=int,default=7)
    p.add_argument("--max-candidate-runs",type=int,default=300)
    args=p.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        p.error("as-of-utc must have Z offset")
    if not os.environ.get("TENNIS_DATABASE_URL"):
        p.error("TENNIS_DATABASE_URL required")
    cutoff=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00"))
    with psycopg.connect(os.environ["TENNIS_DATABASE_URL"],
                         autocommit=True,connect_timeout=10) as conn:
        conn.execute("SET default_transaction_read_only=on")
        recent=discover(conn,cutoff=cutoff,limit=args.max_candidate_runs)
        if args.run_id is not None:
            target=args.run_id
        else:
            candidates=[x for x in recent
                        if x["targets"]==args.targets
                        and x["manager_consensus"]==args.consensus]
            if not candidates:
                print("TENNIS_SELECTION_FUNNEL "+json.dumps({
                    "status":"EXACT_TARGET_CONSENSUS_COHORT_NOT_FOUND",
                    "requested":{"targets":args.targets,"consensus":args.consensus},
                    "runs_examined":len(recent),
                    "recent_run_counts":recent[:15],
                    "db_writes":False,
                },sort_keys=True))
                return 0
            target=candidates[0]["prediction_run_id"]
        report=read_run(conn,run_id=target)
    print("TENNIS_SELECTION_FUNNEL "+json.dumps(report,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
