"""Read-only original Model Pick / bookmaker as-of provenance gap census.

No odds API calls, paid requests, fuzzy identity links, database mutation,
historical outcome replays, changed production rules or data purchases.

Cohort comes from the dashboard's canonical aligned Model Pick definition,
with a separate original production prediction finalization timestamp.
These are *aligned historical original-production records*, not proven
push notification timestamps. Market qualification additionally requires
actual LIVE_PROVIDER raw receipt <= first issuance, both OPEN sides in
the exact same original source snapshot/time/bookmaker and all database
observation/ingestion clocks <= issuance. No historical backfill is passed
off as a genuine original odds observation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import UTC,datetime,timedelta
from decimal import Decimal
from typing import Any

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)

VERSION="FIRST_ISSUE_PREISSUE_BOOKMAKER_GAP_V1"
PREMATCH_MINUTES=30


def _utc(t:datetime|None,name:str)->datetime:
    if t is None or t.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return t.astimezone(UTC)


def _parse(t:str,name:str)->datetime:
    if not t or t[-1]!="Z":
        raise ValueError(f"{name} must be ISO UTC ending Z")
    return _utc(datetime.fromisoformat(t[:-1]+"+00:00"),name)


def load_cohort(conn:psycopg.Connection,*,start:datetime,
                end:datetime,as_of:datetime)->list[dict[str,Any]]:
    sql=f"""
    WITH {aligned_production_model_picks_cte()}
    SELECT p.match_id,p.prediction_run_id,p.picked_at,
           pr.prediction_finalized_at,p.aligned_pick_type,
           p.selected_side,p.aligned_probability,
           mv.match_start_at,mv.tour_scope::text,
           (SELECT count(*) FROM match_source_ids ids
            WHERE ids.match_id=p.match_id
              AND ids.source_system='ODDSPAPI'
              AND ids.source_match_id IS NOT NULL) AS oddspapi_id_count,
           (SELECT count(DISTINCT ids.source_match_id)
            FROM match_source_ids ids
            WHERE ids.match_id=p.match_id
              AND ids.source_system='ODDSPAPI'
              AND ids.source_match_id IS NOT NULL) AS unique_oddspapi_ids
    FROM aligned_model_picks p
    JOIN prediction_runs pr ON pr.prediction_run_id=p.prediction_run_id
    LEFT JOIN LATERAL (
      SELECT mv0.match_start_at,mv0.tour_scope
      FROM match_versions mv0
      WHERE mv0.match_id=p.match_id
        AND mv0.as_of <= GREATEST(p.picked_at,pr.prediction_finalized_at)
        AND mv0.source_observed_at <= GREATEST(p.picked_at,pr.prediction_finalized_at)
        AND mv0.ingested_at <= GREATEST(p.picked_at,pr.prediction_finalized_at)
      ORDER BY mv0.as_of DESC,mv0.source_observed_at DESC,
               mv0.ingested_at DESC,mv0.match_version_id DESC
      LIMIT 1
    ) mv ON TRUE
    WHERE p.picked_at >= %s AND p.picked_at < %s
      AND p.picked_at <= %s
      AND pr.prediction_finalized_at IS NOT NULL
      AND pr.prediction_finalized_at<=%s
    ORDER BY p.picked_at DESC,p.match_id
    LIMIT 2500
    """
    rows=conn.execute(sql,(start,end,as_of,as_of)).fetchall()
    return [
        {
            "match_id":int(r[0]),"run_id":int(r[1]),
            "model_as_of":r[2],"prediction_finalized_at":r[3],
            "pick_type":str(r[4]),"selected_side":int(r[5]),
            "model_probability":float(r[6]),
            "scheduled_start":r[7],
            "tour":str(r[8]) if r[8] else None,
            "oddspapi_id_count":int(r[9]),
            "unique_oddspapi_ids":int(r[10]),
        }
        for r in rows
    ]


def load_quote_rows(conn:psycopg.Connection, *,
                    match_ids:list[int],as_of:datetime)->list[dict[str,Any]]:
    if not match_ids:
        return []
    sql="""
    SELECT os.match_id,os.odds_snapshot_id,os.source_system,
           os.bookmaker_code,os.source_snapshot_id,
           os.source_observed_at,os.as_of,os.ingested_at,
           os.market_status::text,os.selection_type::text,
           os.decimal_price,os.odds_origin::text,
           ro.provider_code,ro.received_at,ro.provider_observed_at,
           ro.observation_time_basis::text
    FROM odds_snapshots os
    LEFT JOIN raw_odds_observations ro
      ON ro.raw_odds_observation_id=os.raw_odds_observation_id
    WHERE os.match_id=ANY(%s)
      AND os.market_type='MATCH_WINNER'
      AND os.selection_type IN ('PLAYER_1','PLAYER_2')
      AND os.ingested_at<=%s
      AND os.as_of<=%s
      AND os.source_observed_at<=%s
    ORDER BY os.match_id,os.source_observed_at,os.odds_snapshot_id
    """
    rows=conn.execute(sql,(match_ids,as_of,as_of,as_of)).fetchall()
    return [{
        "match_id":int(r[0]),"odds_snapshot_id":int(r[1]),
        "source_system":str(r[2]),"bookmaker_code":str(r[3]),
        "source_snapshot_id":str(r[4]) if r[4] is not None else None,
        "source_observed_at":r[5],"as_of":r[6],
        "ingested_at":r[7],"market_status":str(r[8]),
        "selection_type":str(r[9]),"decimal_price":float(r[10]),
        "odds_origin":str(r[11]) if r[11] else None,
        "raw_provider":str(r[12]) if r[12] else None,
        "raw_received_at":r[13],"raw_provider_observed_at":r[14],
        "raw_time_basis":str(r[15]) if r[15] else None,
    } for r in rows]


def _record_time_pass(q:dict[str,Any],issue:datetime)->bool:
    return all(
        _utc(q.get(name),name)<=issue
        for name in ("source_observed_at","as_of","ingested_at")
    )


def _safe_live_quote(q:dict[str,Any],issue:datetime)->bool:
    if not _record_time_pass(q,issue):
        return False
    if (q["odds_origin"]!="LIVE_PROVIDER"
        or q.get("raw_provider") is None
        or q.get("raw_received_at") is None
        or _utc(q["raw_received_at"],"raw_received_at")>issue
        or q["market_status"]!="OPEN"
        or q["source_snapshot_id"] is None
        or q["selection_type"] not in ("PLAYER_1","PLAYER_2")
        or not math.isfinite(q["decimal_price"])
        or q["decimal_price"]<=1):
        return False
    if q.get("raw_provider_observed_at") is not None:
        if _utc(q["raw_provider_observed_at"],"raw_provider_observed_at")>issue:
            return False
    return True


def _pair(qs:list[dict[str,Any]],*,issue:datetime)->list[dict[str,Any]]:
    groups=defaultdict(lambda:defaultdict(list))
    for q in qs:
        if not _safe_live_quote(q,issue):
            continue
        key=(
            q["source_system"],q["bookmaker_code"],
            q["source_snapshot_id"],
            _utc(q["source_observed_at"],"source_observed_at"),
        )
        groups[key][q["selection_type"]].append(float(q["decimal_price"]))
    pairs=[]
    for (source,book,snapshot,observed),sides in groups.items():
        if set(sides)!={"PLAYER_1","PLAYER_2"}:
            continue
        # One snapshot with conflicting price duplicates is ambiguous, not valid.
        if any(len(set(prices))!=1 for prices in sides.values()):
            continue
        p1=sides["PLAYER_1"][0]
        p2=sides["PLAYER_2"][0]
        implied=1/p1+1/p2
        if not math.isfinite(implied) or not .5<=implied<=1.5:
            continue
        pairs.append({
            "source_system":source,"bookmaker_code":book,
            "observed_at_utc":observed.isoformat(),
            "decimal_player_1":p1,"decimal_player_2":p2,
            "implied_overround":round(implied,8),
            "bookmaker_favourite":1 if p1<p2 else 2 if p2<p1 else None,
        })
    pairs.sort(key=lambda x:(x["observed_at_utc"],x["source_system"],
                             x["bookmaker_code"]),reverse=True)
    return pairs


def classify(pick:dict[str,Any],quotes:list[dict[str,Any]])->dict[str,Any]:
    issue=_utc(max(pick["model_as_of"],pick["prediction_finalized_at"]),
               "original first issue")
    start=pick["scheduled_start"]
    if (start is None or pick["tour"] not in ("ATP_MAIN","CHALLENGER")
        or _utc(start,"match start")-issue<timedelta(minutes=PREMATCH_MINUTES)):
        category="INVALID_OR_LATE_PIT_ORIGINAL_ISSUE"
        pairs=[]
    else:
        pairs=_pair(quotes,issue=issue)
        if pairs:
            category="QUALIFIED_ORIGINAL_LIVE_PREISSUE_PAIR"
        elif any(_safe_live_quote(q,issue) for q in quotes):
            category="PREISSUE_PARTIAL_OR_UNPAIRED_LIVE_MARKET"
        elif any(_record_time_pass(q,issue) for q in quotes):
            category="PREISSUE_ODDS_NONLIVE_SUSPENDED_OR_UNSAFE"
        elif quotes:
            category="ONLY_LATER_OR_BACKFILLED_ODDS"
        elif pick["unique_oddspapi_ids"]>1:
            category="AMBIGUOUS_ODDSPAPI_SOURCE_IDS_NO_QUOTE"
        elif pick["oddspapi_id_count"]==0:
            category="NO_ODDSPAPI_FIXTURE_MAPPING_NO_QUOTE"
        else:
            category="MAPPED_ODDSPAPI_BUT_NO_STORED_ODDS"
    return {
        "match_id":pick["match_id"],
        "prediction_run_id":pick["run_id"],
        "original_model_as_of_utc":_utc(pick["model_as_of"],"model as_of").isoformat(),
        "original_prediction_finalized_utc":_utc(
            pick["prediction_finalized_at"],"finalized").isoformat(),
        "conservative_first_issue_utc":issue.isoformat(),
        "scheduled_start_utc":(
            _utc(start,"start").isoformat() if start else None),
        "tour":pick["tour"],"aligned_pick_type":pick["pick_type"],
        "odds_quote_count":len(quotes),
        "oddspapi_id_count":pick["oddspapi_id_count"],
        "unique_oddspapi_ids":pick["unique_oddspapi_ids"],
        "category":category,"qualified_pair_count":len(pairs),
        "latest_qualified_pair":pairs[0] if pairs else None,
        "raw_source_required":True,
        "qualifying_market_is_live_and_received_before_first_issue":bool(pairs),
    }


def audit(*,picks:list[dict[str,Any]],quotes:list[dict[str,Any]],
          as_of:datetime,sample_size:int=100)->dict[str,Any]:
    as_of=_utc(as_of,"as_of")
    if not 1<=sample_size<=250:
        raise ValueError("sample_size 1..250")
    by_match=defaultdict(list)
    for q in quotes:
        by_match[int(q["match_id"])].append(q)
    cases=[classify(p,by_match.get(p["match_id"],[])) for p in picks]
    seen=set()
    for case in cases:
        if case["match_id"] in seen:
            raise ValueError("duplicate original aligned match ID")
        seen.add(case["match_id"])
    counts=Counter(c["category"] for c in cases)
    unmatched=[c for c in cases
               if c["category"]!="QUALIFIED_ORIGINAL_LIVE_PREISSUE_PAIR"]
    sample=unmatched[:sample_size]
    serialized=json.dumps(sample,sort_keys=True,separators=(",",":"))
    digest=hashlib.sha256(serialized.encode()).hexdigest()
    return {
        "version":VERSION,"report_as_of_utc":as_of.isoformat(),
        "historical_selection_source":"dashboard canonical aligned first production evidence",
        "not_independent_original_publication_proof":True,
        "original_selection_rows":len(picks),
        "fully_qualifying_live_preissue_two_sided":counts.get(
            "QUALIFIED_ORIGINAL_LIVE_PREISSUE_PAIR",0),
        "unqualified_original_selections":len(unmatched),
        "failure_categories":dict(sorted(counts.items())),
        "unmatched_sample_count":len(sample),
        "unmatched_sample_rows":sample,
        "sample_sha256":digest,
        "actual_official_eligible_fixture_denominator":None,
        "historical_odds_provider_rights_unverified":True,
        "source_import_after_issue_never_qualifies":True,
        "no_new_provider_calls":True,
        "no_purchases":True,
        "no_db_writes":True,
        "do_not_label_mapping_absent_as_proven_provider_coverage_absent":True,
    }


def run(conn:psycopg.Connection,*,start:datetime,end:datetime,
        as_of:datetime,sample_size:int=100)->dict[str,Any]:
    start,end,as_of=(_utc(start,"start"),_utc(end,"end"),_utc(as_of,"as_of"))
    if not start<end<=as_of or (end-start).days>150:
        raise ValueError("invalid bounded audited historical period")
    conn.execute("SET default_transaction_read_only=on")
    picks=load_cohort(conn,start=start,end=end,as_of=as_of)
    quotes=load_quote_rows(conn,
        match_ids=[p["match_id"] for p in picks],as_of=as_of)
    return audit(picks=picks,quotes=quotes,as_of=as_of,sample_size=sample_size)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start-utc",default="2026-09-10T00:00:00Z")
    p.add_argument("--end-utc")
    p.add_argument("--sample-size",type=int,default=100)
    a=p.parse_args(argv)
    as_of=datetime.now(UTC)
    end=_parse(a.end_utc,"end") if a.end_utc else as_of
    if end>as_of:
        p.error("end cannot be in the future")
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL is required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        result=run(conn,start=_parse(a.start_utc,"start"),
                   end=end,as_of=as_of,sample_size=a.sample_size)
    print("TENNIS_PREISSUE_ODDS_GAP_AUDIT "+json.dumps(result,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
