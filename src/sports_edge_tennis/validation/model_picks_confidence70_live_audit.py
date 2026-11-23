"""Actual Model Picks vs frozen >=70% confidence subset, canonical PIT SQL.

Read-only, one repeatable-read original production database snapshot. Results
are the dashboard-aligned *original* first eligible live selection by match ID,
not a Manager C research candidate, and not a new retrospective forecast.
Confidence>=0.70 is policy PIT_CONFIDENCE_GE70_V1 (see qualified_shadow_evidence).
The filter is research shadow only: it has NOT published fewer Model Picks.

Date: original point-in-time scheduled start in Asia/Manila.
Scope: ATP/Challenger MAIN draws vs qualifying classified separately from
the ORIGINAL match version visible at original pick. Neither is a complete
official external eligible-fixture denominator.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import os

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)
from sports_edge_tennis.dashboard.model_pick_history import (
    load_aligned_daily_model_picks,
)
from sports_edge_tennis.performance.aligned_historical_replay import (
    aligned_replay_sql,
)
from sports_edge_tennis.performance.qualified_shadow_evidence import (
    POLICY_ID, MIN_CONFIDENCE,
)

VERSION="ORIGINAL_MODEL_PICKS_VS_70_SHADOW_REAL_20261123"
LOCAL_TZ="Asia/Manila"
OUTCOMES=("WON","LOST","VOID","PENDING")


def classify(row: dict) -> str:
    if row["original_pit_tour"] in ("ATP_MAIN","CHALLENGER"):
        if row["qualifying_flag"] is False:
            return "ATP_CH_MAIN_DRAW"
        if row["qualifying_flag"] is True:
            return "ATP_CH_QUALIFYING"
    return "UNKNOWN_OR_OUTSIDE"


def aggregate(rows: list[dict], *, baseline: list[dict] | None = None) -> dict:
    counts=Counter(p["status"] for p in rows)
    unknown=set(counts)-set(OUTCOMES)
    if unknown:
        raise ValueError("unknown result classification "+str(unknown))
    wins,losses,void,pending=(counts[s] for s in OUTCOMES)
    if sum((wins,losses,void,pending))!=len(rows):
        raise ValueError("match status accounting mismatch")
    selection_ids={p["match_id"] for p in rows}
    if len(selection_ids)!=len(rows):
        raise ValueError("duplicate canonical match IDs")
    baseline=rows if baseline is None else baseline
    base_ids={p["match_id"] for p in baseline}
    if not selection_ids.issubset(base_ids):
        raise ValueError("70% subset must nest in original baseline")
    omitted=[p for p in baseline if p["match_id"] not in selection_ids]
    return {
        "issued":len(rows),
        "wins":wins,"losses":losses,"void":void,"pending":pending,
        "settled":wins+losses,
        "accuracy":wins/(wins+losses) if wins+losses else None,
        "retained_fraction_of_same_scope_original":(
            len(rows)/len(baseline) if baseline else None),
        "retained_scored_fraction_of_original_settled":(
            (wins+losses)/sum(x["status"] in ("WON","LOST") for x in baseline)
            if any(x["status"] in ("WON","LOST") for x in baseline) else None),
        "avoided_losses":sum(p["status"]=="LOST" for p in omitted),
        "missed_winners":sum(p["status"]=="WON" for p in omitted),
        "discarded_void":sum(p["status"]=="VOID" for p in omitted),
        "discarded_pending":sum(p["status"]=="PENDING" for p in omitted),
        "selected_match_ids":sorted(selection_ids),
    }


def summarize(rows:list[dict]) -> dict:
    result={}
    for scope in ("ATP_CH_MAIN_DRAW","ALL_DASHBOARD_ALIGNED",
                  "ATP_CH_QUALIFYING"):
        included=(rows if scope=="ALL_DASHBOARD_ALIGNED"
                  else [r for r in rows if r["scope"]==scope])
        if scope=="ATP_CH_MAIN_DRAW" and not included:
            result[scope]={"status":"EMPTY"}
            continue
        policies={
            "original_all_model_picks":included,
            "manager_consensus_only":[r for r in included
                                      if r["type"]=="MANAGER_CONSENSUS"],
            "wide_spread_tracking_only":[r for r in included
                                          if r["type"]=="A1_A2_WIDE_SPREAD"],
            "confidence70_original_model_picks":[
                r for r in included if r["confidence"]>=MIN_CONFIDENCE],
            "confidence70_manager_consensus":[
                r for r in included if r["type"]=="MANAGER_CONSENSUS"
                and r["confidence"]>=MIN_CONFIDENCE],
            "confidence70_wide_spread":[
                r for r in included if r["type"]=="A1_A2_WIDE_SPREAD"
                and r["confidence"]>=MIN_CONFIDENCE],
            "confidence80_original_model_picks":[
                r for r in included if r["confidence"]>=Decimal("0.80")],
        }
        original=policies["original_all_model_picks"]
        manager=policies["manager_consensus_only"]
        wide=policies["wide_spread_tracking_only"]
        result[scope]={
            name:aggregate(population,baseline=(
                manager if name=="confidence70_manager_consensus"
                else wide if name=="confidence70_wide_spread"
                else original))
            for name,population in policies.items()
        }
        if len(manager)+len(wide)!=len(original):
            raise ValueError("unknown publication source")
    return result


def run(conn:psycopg.Connection, *, first_day:date, last_day:date)->dict:
    if first_day>last_day or (last_day-first_day).days>31:
        raise ValueError("invalid bounded original calendar date period")
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    asof=conn.execute("SELECT current_timestamp").fetchone()[0]
    raw=conn.execute(aligned_replay_sql(),(first_day,last_day)).fetchall()
    rows=[]
    by_id={}
    for row in raw:
        (mid,day,typ,prob,side,surface,a1_side,a2_side,a1_prob,a2_prob,
         outcome)=row
        mid=int(mid)
        if mid in by_id:
            raise ValueError("duplicate original Model Pick match")
        if outcome not in OUTCOMES or typ not in ("MANAGER_CONSENSUS",
                "A1_A2_WIDE_SPREAD"):
            raise ValueError("unexpected original pick status/type")
        probability=Decimal(prob)
        if not probability.is_finite() or not 0<=probability<=1:
            raise ValueError("original pre-match confidence invalid")
        d={"match_id":mid,"day":day.isoformat(),"type":typ,
           "confidence":probability,"selected_side":int(side),
           "status":outcome}
        by_id[mid]=d
        rows.append(d)
    # Verify exact same original population, outcomes, and original scores as
    # results dashboard. Never silently compare a different ledger's picks.
    seen_detail_ids=set()
    for i in range((last_day-first_day).days+1):
        day=first_day+timedelta(days=i)
        for r in load_aligned_daily_model_picks(
                conn,decision_date=day,timezone_name=LOCAL_TZ):
            mid=r.match_id
            if mid in seen_detail_ids:
                raise ValueError("dashboard duplicates one canonical fixture")
            seen_detail_ids.add(mid)
            row=by_id.get(mid)
            if row is None or row["day"]!=day.isoformat() or row["status"]!=r.result_status:
                raise ValueError("dashboard and historical replay disagree: "+str(mid))
            if row["type"]!=r.pick_type or row["confidence"]!=r.consensus_probability:
                raise ValueError("dashboard type/point-in-time confidence disagree")
    if set(by_id)!=seen_detail_ids:
        raise ValueError("dashboard and shadow differ in matched original IDs")
    if rows:
        scope_rows=conn.execute(f"""
            WITH {aligned_production_model_picks_cte()}
            SELECT p.match_id, v.tour_scope::text,v.competition_tier::text,
                   v.qualifying_flag,v.round_code::text
            FROM aligned_model_picks p
            LEFT JOIN LATERAL (
              SELECT mv.tour_scope,mv.competition_tier,
                     mv.qualifying_flag,mv.round_code
              FROM match_versions mv
              WHERE mv.match_id=p.match_id
                AND mv.as_of<=p.picked_at
                AND mv.source_observed_at<=p.picked_at
                AND mv.ingested_at<=p.picked_at
              ORDER BY mv.as_of DESC,mv.source_observed_at DESC,
                       mv.match_version_id DESC LIMIT 1
            ) v ON TRUE
            WHERE p.match_id=ANY(%s)
        """,(list(by_id),)).fetchall()
        if len(scope_rows)!=len(rows):
            raise ValueError("incomplete original PIT scope classification")
        for mid,tour,tier,qual,round_code in scope_rows:
            d=by_id[int(mid)]
            d.update({"original_pit_tour":str(tour),
                      "tier":str(tier),"qualifying_flag":qual,
                      "original_round_code":str(round_code)})
            d["scope"]=classify(d)
    per_day={}
    for i in range((last_day-first_day).days+1):
        day=(first_day+timedelta(days=i)).isoformat()
        per_day[day]=summarize([r for r in rows if r["day"]==day])
    all_result=summarize(rows)
    def serialize_id_digest(r):
        return hashlib.sha256(json.dumps(
            sorted(int(x["match_id"]) for x in r),separators=(",",":")).encode()
        ).hexdigest()
    immutable={
        "version":VERSION,
        "asof_utc":asof.astimezone(UTC).isoformat(),
        "first_manila_day":first_day.isoformat(),
        "last_manila_day":last_day.isoformat(),
        "correct_original_pit_confidence_policy":POLICY_ID,
        "inclusive_original_confidence_floor":str(MIN_CONFIDENCE),
        "research_only_not_live_selection_or_manager_policy":True,
        "original_dashboard_matched_population_size":len(rows),
        "exact_original_canonical_ids_digest":serialize_id_digest(rows),
        "scope_denominator":"INTERNAL_FIRST_ORIGINAL_ALIGNED_NOT_OFFICIAL_EXTERNAL",
        "outcomes_are_current_report_asof_no_retrospective_source_faking":True,
        "per_day":per_day,"whole_period":all_result,
        "records":[{
            **{key: (str(val) if isinstance(val,Decimal) else val)
               for key,val in r.items()}
        } for r in sorted(rows,key=lambda r:(r["day"],r["match_id"]))],
    }
    return immutable


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser()
    p.add_argument("--first-day",required=True)
    p.add_argument("--last-day",required=True)
    args=p.parse_args(argv)
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=False,connect_timeout=10) as conn:
        evidence=run(conn,first_day=date.fromisoformat(args.first_day),
                     last_day=date.fromisoformat(args.last_day))
    print("TENNIS_MODEL_PICKS_VS_CONFIDENCE70 "+json.dumps(
        evidence,sort_keys=True,default=str),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
