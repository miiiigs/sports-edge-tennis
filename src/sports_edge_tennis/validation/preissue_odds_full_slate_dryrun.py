"""No-provider-call preissue OddsPapi slate/budget feasibility research.

Produces a dry-run of whether existing canonical fixture population and
verified fresh OddsPapi event-identity cache can produce bookmaker snapshots
BEFORE first Manager prediction. Crucially, unlike the existing live
qualification path this covers all internal pre-prediction scheduled ATP and
Challenger MAIN singles fixtures, NOT only Manager consensus fixtures.

Nothing is priced, fetched or written here; a preissue fetch integration must
first pass the per-cycle + monthly OddsPapi ledger and gain explicit approval.
Never count a missing canonical fixture as absent from the OFFICIAL ATP census.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC,datetime,timedelta
from typing import Any

import psycopg

from sports_edge_tennis.live.oddspapi import ODDSPAPI_PLAN_BOOKMAKERS
from sports_edge_tennis.live.oddspapi_budget import (
    OddspapiBudgetPolicy,SchedulingItem,schedule_oddspapi_work,
)
from sports_edge_tennis.live.oddspapi_identity_cache import (
    identity_invalidation_reason,load_cached_identity,
)
from sports_edge_tennis.live.price_slate import load_price_scanner_candidate_match_ids

VERSION="PREISSUE_ODDSPAPI_FULL_SLATE_DRYRUN_V1"


def _utc(x:datetime|None)->datetime:
    if x is None or x.tzinfo is None:
        raise ValueError("all schedule and observation clocks must be UTC-aware")
    return x.astimezone(UTC)


def load_candidates(conn:psycopg.Connection,*,now:datetime,
                    horizon_days:int)->list[dict[str,Any]]:
    now=_utc(now)
    if not 1<=horizon_days<=7:
        raise ValueError("horizon must be 1..7 days")
    mids=load_price_scanner_candidate_match_ids(
        conn,horizon_start=now.date(),horizon_days=horizon_days+1,
        as_of=now,source_systems=("SPORTRADAR","API_TENNIS"),
    )
    if not mids:
        return []
    rows=conn.execute("""
    SELECT m.match_id,v.match_start_at,v.tour_scope::text,
           v.qualifying_flag,
           (
             SELECT ids.source_match_id FROM match_source_ids ids
             WHERE ids.match_id=m.match_id
               AND ids.source_system='SPORTRADAR'
               AND ids.source_match_id IS NOT NULL
             ORDER BY ids.match_source_id DESC LIMIT 1
           ) AS sportradar_event_id
    FROM matches m
    JOIN LATERAL (
      SELECT mv.match_start_at,mv.tour_scope,mv.qualifying_flag
      FROM match_versions mv
      WHERE mv.match_id=m.match_id
        AND mv.as_of <= %s
        AND mv.source_observed_at <= %s
        AND mv.ingested_at <= %s
      ORDER BY mv.as_of DESC,mv.source_observed_at DESC,
               mv.match_version_id DESC
      LIMIT 1
    ) v ON TRUE
    WHERE m.match_id=ANY(%s)
    ORDER BY v.match_start_at,m.match_id
    """,(now,now,now,list(mids))).fetchall()
    return [{
        "match_id":int(q[0]),"match_start":q[1],
        "tour_scope":str(q[2]),"qualifying_flag":q[3],
        "sportradar_event_id":str(q[4]) if q[4] else None,
    } for q in rows]


def dry_run(*,candidates:list[dict[str,Any]],
            identity_resolver,now:datetime,
            policy:OddspapiBudgetPolicy=OddspapiBudgetPolicy(),
            books:tuple[str,...]=ODDSPAPI_PLAN_BOOKMAKERS)->dict[str,Any]:
    now=_utc(now)
    counters=Counter()
    warm=[]
    unmatched=[]
    for c in candidates:
        start=_utc(c["match_start"])
        mid=int(c["match_id"])
        if c["tour_scope"] not in ("ATP_MAIN","CHALLENGER"):
            counters["out_of_scope_tour"]+=1
            continue
        if c["qualifying_flag"] is not False:
            counters["qualifying_or_unknown_draw_excluded"]+=1
            continue
        if not now+timedelta(minutes=30)<=start<=now+timedelta(days=7):
            counters["outside_prematch_or_horizon"]+=1
            continue
        counters["internal_eligible_main_singles"]+=1
        cached=identity_resolver(mid)
        if cached is None:
            counters["identity_cache_missing"]+=1
            unmatched.append({"match_id":mid,"reason":"NO_CACHED_ODDSPAPI_ID"})
            continue
        reason=identity_invalidation_reason(
            cached,sportradar_event_id=c.get("sportradar_event_id"),
            scheduled_start=start,now=now,
        )
        if reason:
            counters["identity_"+reason]+=1
            unmatched.append({"match_id":mid,"reason":reason})
            continue
        counters["warm_verified_oddspapi_identity"]+=1
        warm.append(SchedulingItem(
            match_id=mid,tournament_id=cached.oddspapi_tournament_id,
            scheduled_start=start,has_cached_identity=True,
        ))
    schedule=schedule_oddspapi_work(warm,books=books,policy=policy) if warm else None
    selected=schedule.scheduled_match_ids if schedule else ()
    deferred=schedule.deferred_match_ids if schedule else ()
    selected_tournaments=schedule.scheduled_tournament_ids if schedule else ()
    estimated_requests=schedule.planned_requests if schedule else 0
    if estimated_requests>policy.per_cycle_limit:
        raise ValueError("dry-run planned more calls than configured quota")
    # NOTE projection is only a mathematical what-if. No request is made.
    return {
        "version":VERSION,"audited_at_utc":now.isoformat(),
        "input_unique_canonical_fixture_count":len({
            c["match_id"] for c in candidates
        }),
        "counts":dict(sorted(counters.items())),
        "verified_warm_fixture_count":len(warm),
        "warm_unique_tournament_count":len({
            x.tournament_id for x in warm
        }),
        "provisionally_schedulable_warm_fixture_count":len(selected),
        "deferred_warm_fixture_count":len(deferred),
        "estimated_bookmaker_queries_per_cycle_IF_APPROVED":estimated_requests,
        "estimated_monthly_queries_if_same_cadence":policy.projected_monthly(estimated_requests),
        "dryrun_bookmakers":list(books),
        "per_cycle_quota_limit":policy.per_cycle_limit,
        "monthly_quota_limit":policy.monthly_limit,
        "monthly_reserved_calls":policy.reserve,
        "missing_or_invalid_cache_sample":unmatched[:100],
        "estimated_repairable_future_coverage":None,
        "no_cold_unknown_tournament_requests_planned":True,
        "not_all_official_fixture_denominator":True,
        "provider_identity_not_equivalent_to_valid_odds":True,
        "no_provider_calls_made":True,"no_database_writes":True,
        "no_new_subscription_or_incremental_bill":True,
        "no_manager_pick_or_market_gate_change":True,
        "requires_separate_approval_before_actual_request_work":True,
    }


def run(conn:psycopg.Connection,*,now:datetime,
        horizon_days:int=3)->dict[str,Any]:
    now=_utc(now)
    conn.execute("SET default_transaction_read_only=on")
    candidates=load_candidates(conn,now=now,horizon_days=horizon_days)
    return dry_run(
        candidates=candidates,
        identity_resolver=lambda match_id:load_cached_identity(
            conn,match_id=match_id),
        now=now,
    )


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--horizon-days",type=int,default=3)
    args=p.parse_args(argv)
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL required")
    now=datetime.now(UTC)
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        result=run(conn,now=now,horizon_days=args.horizon_days)
    print("TENNIS_PREISSUE_ODDS_QUOTA_DRYRUN "+
          json.dumps(result,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
