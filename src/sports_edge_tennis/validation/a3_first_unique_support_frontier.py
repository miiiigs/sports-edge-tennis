"""PIT first-evaluation Agent3 support bottleneck audit from real diagnostics.

Converts repeated A3 evaluation telemetry into per-UNIQUE-match first
evaluation, preserving original abstention outcome and tour/known surface.
It does not replay or modify A3, invent probabilities, loosen thresholds,
or classify graph support as a correct future winner forecast. The original
four-year lookback is unchanged. A 'soft structural candidate' is only a
hypothesis for later full frozen PIT shadow experiments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter,defaultdict
from datetime import UTC,datetime,timedelta
from typing import Any

import psycopg

VERSION="A3_UNIQUE_FIRST_SUPPORT_FRONTIER_V1"


def _utc(x:datetime|None,name:str)->datetime:
    if x is None or x.tzinfo is None:
        raise ValueError(name+" must be timezone-aware")
    return x.astimezone(UTC)


def load_rows(conn:psycopg.Connection,*,start:datetime,end:datetime,
              as_of:datetime)->list[dict[str,Any]]:
    rows=conn.execute("""
    WITH first_a3 AS (
      SELECT DISTINCT ON (d.match_id)
        d.match_id,d.evaluated_at,d.identity_status,
        d.direct_raw_support,d.direct_effective_support,
        d.raw_shared_opponents,d.eligible_shared_opponents,
        d.q_count,d.support_path,d.gate_failed,d.final_status,
        d.graph_degree_player1,d.graph_degree_player2,
        d.history_count_player1,d.history_count_player2
      FROM a3_support_diagnostics d
      WHERE d.evaluated_at >= %s
        AND d.evaluated_at < %s
        AND d.evaluated_at <= %s
      ORDER BY d.match_id,d.evaluated_at,d.a3_support_diagnostic_id
    )
    SELECT f.*,mv.match_start_at,mv.tour_scope::text,mv.surface::text
    FROM first_a3 f
    LEFT JOIN LATERAL (
      SELECT v.match_start_at,v.tour_scope,v.surface
      FROM match_versions v
      WHERE v.match_id=f.match_id
        AND v.as_of<=f.evaluated_at
        AND v.source_observed_at<=f.evaluated_at
        AND v.ingested_at<=f.evaluated_at
      ORDER BY v.as_of DESC,v.source_observed_at DESC,
               v.match_version_id DESC
      LIMIT 1
    ) mv ON TRUE
    ORDER BY f.evaluated_at,f.match_id
    LIMIT 12000
    """,(start,end,as_of)).fetchall()
    return [{
        "match_id":int(q[0]),"evaluated_at":q[1],
        "identity_status":str(q[2]),
        "direct_raw":int(q[3] or 0),
        "direct_ess":float(q[4] or 0),
        "raw_shared":int(q[5] or 0),
        "eligible_shared":int(q[6] or 0),
        "network_q":float(q[7] or 0),
        "support_path":str(q[8]) if q[8] else None,
        "gate_failed":str(q[9]) if q[9] else None,
        "final_status":str(q[10]),
        "degree_1":int(q[11] or 0),"degree_2":int(q[12] or 0),
        "history_1":int(q[13] or 0),"history_2":int(q[14] or 0),
        "match_start":q[15],
        "tour":str(q[16]) if q[16] is not None else None,
        "surface":str(q[17]) if q[17] is not None else None,
    } for q in rows]


def classify(q:dict[str,Any])->dict[str,Any]:
    at=_utc(q["evaluated_at"],"evaluated_at")
    start=q["match_start"]
    if start is None or q["tour"] not in ("ATP_MAIN","CHALLENGER"):
        issue="UNKNOWN_OR_UNQUALIFIED_PIT_MATCH"
    elif _utc(start,"start")-at<timedelta(minutes=30):
        issue="LATE_OR_NON_FUTURE_EVALUATION"
    elif q["final_status"]=="PREDICTED":
        issue="ORIGINAL_A3_PREDICTED"
    elif q["surface"] is None:
        issue="UNKNOWN_SURFACE"
    elif q["identity_status"]!="RESOLVED":
        issue="BOTH_HISTORY_IDENTITIES_UNRESOLVED"
    elif min(q["history_1"],q["history_2"])==0:
        issue="ONE_OR_BOTH_PLAYERS_ZERO_MATCH_HISTORY"
    elif q["raw_shared"]==0:
        issue="NO_RAW_SHARED_OPPONENTS"
    elif q["eligible_shared"]==0:
        issue="RAW_SHARED_BUT_ZERO_ELIGIBLE_EDGES"
    elif q["eligible_shared"]<5 or q["network_q"]<8:
        issue="BELOW_NORMAL_NETWORK_SHARED_OR_Q_GATE"
    else:
        issue="POTENTIALLY_SUPPORTED_BUT_SIGNAL_OR_OTHER_GATE"
    structural=(
        issue not in ("UNKNOWN_OR_UNQUALIFIED_PIT_MATCH",
                      "LATE_OR_NON_FUTURE_EVALUATION",
                      "ORIGINAL_A3_PREDICTED",
                      "UNKNOWN_SURFACE",
                      "BOTH_HISTORY_IDENTITIES_UNRESOLVED",
                      "ONE_OR_BOTH_PLAYERS_ZERO_MATCH_HISTORY")
        and q["raw_shared"]>=3
        and q["degree_1"]>=3 and q["degree_2"]>=3
    )
    return {
        "match_id":q["match_id"],
        "tour":q["tour"],"surface":q["surface"],
        "original_status":q["final_status"],
        "original_abstain_reason":q["gate_failed"],
        "category":issue,
        "raw_shared_opponents":q["raw_shared"],
        "eligible_shared_opponents":q["eligible_shared"],
        "network_effective_q":q["network_q"],
        "direct_raw":q["direct_raw"],
        "direct_effective_support":q["direct_ess"],
        "history_player1":q["history_1"],
        "history_player2":q["history_2"],
        "raw_structural_soft_research_candidate_only":structural,
        "true_counterfactual_a3_probability_available":False,
    }


def audit(rows:list[dict[str,Any]],*,as_of:datetime,
          sample_limit:int=100)->dict[str,Any]:
    as_of=_utc(as_of,"as_of")
    if not 1<=sample_limit<=250:
        raise ValueError("sample 1..250")
    seen=set()
    cases=[]
    for q in rows:
        mid=q["match_id"]
        if mid in seen:
            raise ValueError("repeated A3 evaluations cannot become unique new matches")
        seen.add(mid)
        if _utc(q["evaluated_at"],"evaluated_at")>as_of:
            raise ValueError("future diagnostics not observed")
        cases.append(classify(q))
    counts=Counter(x["category"] for x in cases)
    tour=defaultdict(Counter)
    for x in cases:
        tour[x["tour"] or "UNKNOWN"][x["category"]]+=1
    candidates=[q for q in cases if q["raw_structural_soft_research_candidate_only"]
                and q["original_status"]=="ABSTAINED"]
    candidates.sort(key=lambda q:(-q["raw_shared_opponents"],q["match_id"]))
    sample=candidates[:sample_limit]
    return {
        "version":VERSION,"report_as_of_utc":as_of.isoformat(),
        "first_unique_match_diagnostics_count":len(cases),
        "original_a3_predicted":counts["ORIGINAL_A3_PREDICTED"],
        "original_a3_abstained_logged":sum(
            x["original_status"]=="ABSTAINED" for x in cases),
        "first_unique_match_category_counts":dict(sorted(counts.items())),
        "by_tour_categories":{
            key:dict(sorted(c.items())) for key,c in sorted(tour.items())
        },
        "original_gate_reasons":dict(sorted(Counter(
            q["original_abstain_reason"] or "UNSPECIFIED" for q in cases
            if q["original_status"]=="ABSTAINED"
        ).items())),
        "raw_graph_soft_candidate_hypothesis_count":len(candidates),
        "candidate_sample_count":len(sample),
        "candidate_samples":sample,
        "candidate_sample_sha256":hashlib.sha256(json.dumps(
            sample,sort_keys=True,separators=(",",":")
        ).encode()).hexdigest(),
        "exact_soft_edge_posterior_replay_performed":False,
        "not_actual_shuffled_or_ensembled_predictions":True,
        "no_outcomes_or_accuracy_computed":True,
        "not_complete_official_fixture_denominator":True,
        "pre_match_source_ingestion_verified_by_match_version_only":True,
        "individual_historical_edge_source_availability_unverified":True,
        "no_production_changes":True,"no_provider_calls":True,"no_db_writes":True,
    }


def run(conn:psycopg.Connection,*,start:datetime,end:datetime,
        as_of:datetime)->dict[str,Any]:
    start,end,as_of=(
        _utc(start,"start"),_utc(end,"end"),_utc(as_of,"as_of")
    )
    if not start<end<=as_of or (end-start).days>95:
        raise ValueError("invalid bounded diagnostics period")
    conn.execute("SET default_transaction_read_only=on")
    rows=load_rows(conn,start=start,end=end,as_of=as_of)
    return audit(rows,as_of=as_of)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-utc",default="2026-10-01T00:00:00Z")
    args=p.parse_args(argv)
    if not args.start_utc.endswith("Z"):
        p.error("start must be ISO UTC ending Z")
    now=datetime.now(UTC)
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL is required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        report=run(conn,start=datetime.fromisoformat(
            args.start_utc.replace("Z","+00:00")),end=now,as_of=now)
    print("TENNIS_A3_SUPPORT_FRONTIER "+json.dumps(report,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
