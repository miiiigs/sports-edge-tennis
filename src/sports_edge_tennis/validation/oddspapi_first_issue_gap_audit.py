"""Read-only first-issued Model Pick bookmaker mapping and timestamp triage.

Separates ever-existing provider match identity from *pre-issue* mapping, and
later recorded odds from genuinely pre-issued synchronized two-sided quotes.
No retrospective market-implied predictions or external provider/API calls.
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

from sports_edge_tennis.validation.pit_bookmaker_model_picks_benchmark import (
    source_sql as matched_quote_sql,
)

VERSION="FIRST_ISSUED_ODDSPAPI_MAPPING_AND_PIT_ODDS_TRIAGE_V1"
MAX_EXAMPLES=50


def query()->str:
    return f"""
      SELECT b.match_id,b.picked_at,b.match_start_at,
             b.tour_scope,b.player_order_conflict,
             b.book_source,b.bookmaker,b.odds1,b.odds2,
             ids.ever_odds_papi,ids.preissue_odds_papi,
             ids.mapping_created_later,
             o.ever_quotes,o.pit_quotes,
             o.recent_pit_quotes,o.pit_open_quotes,
             o.pit_p1_quotes,o.pit_p2_quotes,
             o.latest_observation,o.first_ingestion,
             o.pit_bookmakers
      FROM ({matched_quote_sql().strip().rstrip(";")}) b
      LEFT JOIN LATERAL (
        SELECT
          COUNT(*) FILTER (
            WHERE UPPER(msi.source_system) LIKE '%%ODDS%%PAPI%%'
              AND msi.source_match_id IS NOT NULL
          ) AS ever_odds_papi,
          COUNT(*) FILTER (
            WHERE UPPER(msi.source_system) LIKE '%%ODDS%%PAPI%%'
              AND msi.source_match_id IS NOT NULL
              AND msi.created_at <= b.picked_at
          ) AS preissue_odds_papi,
          COUNT(*) FILTER (
            WHERE UPPER(msi.source_system) LIKE '%%ODDS%%PAPI%%'
              AND msi.source_match_id IS NOT NULL
              AND msi.created_at > b.picked_at
          ) AS mapping_created_later
        FROM match_source_ids msi
        WHERE msi.match_id=b.match_id
      ) ids ON TRUE
      LEFT JOIN LATERAL (
        SELECT COUNT(*) AS ever_quotes,
               COUNT(*) FILTER (
                 WHERE o.source_observed_at<=b.picked_at
                   AND o.as_of<=b.picked_at
                   AND o.ingested_at<=b.picked_at
               ) AS pit_quotes,
               COUNT(*) FILTER (
                 WHERE o.source_observed_at<=b.picked_at
                   AND o.as_of<=b.picked_at
                   AND o.ingested_at<=b.picked_at
                   AND o.source_observed_at>=b.picked_at-interval '6 hours'
               ) AS recent_pit_quotes,
               COUNT(*) FILTER (
                 WHERE o.source_observed_at<=b.picked_at
                   AND o.as_of<=b.picked_at
                   AND o.ingested_at<=b.picked_at
                   AND o.source_observed_at>=b.picked_at-interval '6 hours'
                   AND o.market_status='OPEN'
               ) AS pit_open_quotes,
               COUNT(*) FILTER (
                 WHERE o.selection_type='PLAYER_1'
                   AND o.source_observed_at<=b.picked_at
                   AND o.as_of<=b.picked_at
                   AND o.ingested_at<=b.picked_at
                   AND o.source_observed_at>=b.picked_at-interval '6 hours'
                   AND o.market_status='OPEN'
               ) AS pit_p1_quotes,
               COUNT(*) FILTER (
                 WHERE o.selection_type='PLAYER_2'
                   AND o.source_observed_at<=b.picked_at
                   AND o.as_of<=b.picked_at
                   AND o.ingested_at<=b.picked_at
                   AND o.source_observed_at>=b.picked_at-interval '6 hours'
                   AND o.market_status='OPEN'
               ) AS pit_p2_quotes,
               MAX(o.source_observed_at) AS latest_observation,
               MIN(o.ingested_at) AS first_ingestion,
               COUNT(DISTINCT o.bookmaker_code) FILTER (
                 WHERE o.source_observed_at<=b.picked_at
                   AND o.as_of<=b.picked_at
                   AND o.ingested_at<=b.picked_at
                   AND o.market_status='OPEN'
               ) AS pit_bookmakers
        FROM odds_snapshots o
        WHERE o.match_id=b.match_id
          AND o.market_type='MATCH_WINNER'
          AND o.selection_type IN ('PLAYER_1','PLAYER_2')
      ) o ON TRUE
      ORDER BY b.match_id
    """


def _reason(*,has_pit_fixture:bool,order_conflict:bool,
            paired:bool,ever:int,pit:int,recent:int,
            p1:int,p2:int)->str:
    if not has_pit_fixture or order_conflict:
        return "UNSAFE_OR_MISSING_ORIGINAL_PIT_FIXTURE_OR_PLAYER_ORDER"
    if paired:
        return "QUALIFIED_SAME_ISSUE_TWO_SIDED_BOOKMAKER_QUOTE"
    if ever==0:
        return "NO_NORMALIZED_MATCH_WINNER_ODDS_STORED"
    if pit==0:
        return "QUOTES_STORED_BUT_NOT_AVAILABLE_AT_ORIGINAL_ISSUE"
    if recent==0:
        return "AVAILABLE_QUOTES_OLDER_THAN_SIX_HOURS"
    if p1==0 or p2==0:
        return "FRESH_QUOTES_MISSING_OPEN_PLAYER_SIDE"
    return "BOTH_SIDES_EXIST_BUT_UNMATCHED_BOOK_OR_TIME_OR_STATUS"


def summarize(rows:list[tuple], *, as_of:datetime)->dict[str,Any]:
    if as_of is None or as_of.tzinfo is None:
        raise ValueError("report cutoff must be UTC-aware")
    as_of=as_of.astimezone(UTC)
    seen=set()
    by_tour=defaultdict(Counter)
    funnel=Counter()
    reasons=Counter()
    examples=[]
    digest=hashlib.sha256()
    for raw in rows:
        (mid,issued,start,tour,conflict,book_source,bookmaker,odds1,odds2,
         id_ever,id_pit,id_late,ever,pit,recent,opened,p1,p2,
         last_quote,first_ingest,book_count)=raw
        mid=int(mid)
        if mid in seen:
            raise ValueError("duplicate immutable Model Pick match")
        seen.add(mid)
        if issued is None or issued.tzinfo is None or issued.astimezone(UTC)>as_of:
            raise ValueError("future/ambiguous Model Pick issue")
        if min(int(x or 0) for x in
               (id_ever,id_pit,id_late,ever,pit,recent,opened,p1,p2,book_count))<0:
            raise ValueError("negative source evidence")
        if int(pit or 0)>int(ever or 0) or int(recent or 0)>int(pit or 0):
            raise ValueError("impossible quote chronology")
        if int(id_pit or 0)>int(id_ever or 0):
            raise ValueError("impossible mapping chronology")
        safely_pre_match=bool(
            start is not None and start.tzinfo is not None
            and issued<=start-timedelta(minutes=30)
        )
        paired=(book_source is not None and bookmaker is not None
                and odds1 is not None and odds2 is not None)
        if paired and (int(p1 or 0)==0 or int(p2 or 0)==0):
            raise ValueError("paired quote lacks original open sides")
        reason=_reason(
            has_pit_fixture=safely_pre_match,
            order_conflict=bool(conflict),paired=paired,
            ever=int(ever or 0),pit=int(pit or 0),
            recent=int(recent or 0),p1=int(p1 or 0),p2=int(p2 or 0),
        )
        c=by_tour[str(tour or "UNKNOWN")]
        funnel["original_aligned_picks"]+=1
        c["original_aligned_picks"]+=1
        reasons[reason]+=1
        c["reason_"+reason]+=1
        for key,yes in (
            ("odds_papi_match_id_ever",int(id_ever or 0)>0),
            ("odds_papi_match_id_by_pick",int(id_pit or 0)>0),
            ("odds_papi_mapping_added_after_pick",int(id_late or 0)>0),
            ("match_winner_any_stored_quotes",int(ever or 0)>0),
            ("match_winner_pit_quotes",int(pit or 0)>0),
            ("fresh_pit_quotes_6h",int(recent or 0)>0),
            ("both_open_player_sides_in_same_fixture",int(p1 or 0)>0 and int(p2 or 0)>0),
            ("strict_synchronized_pair",paired),
            ("same_issue_safe_fixture",safely_pre_match and not bool(conflict)),
        ):
            if yes:
                funnel[key]+=1
                c[key]+=1
        if len(examples)<MAX_EXAMPLES and reason!="QUALIFIED_SAME_ISSUE_TWO_SIDED_BOOKMAKER_QUOTE":
            examples.append({
                "match_id":mid,"tour":str(tour or "UNKNOWN"),
                "issue_at":issued.isoformat(),"classification":reason,
                "odds_papi_ever":int(id_ever or 0),
                "odds_papi_at_issue":int(id_pit or 0),
                "any_match_winner_quotes":int(ever or 0),
                "original_issue_quotes":int(pit or 0),
                "fresh_original_issue_quotes":int(recent or 0),
                "fresh_open_p1":int(p1 or 0),"fresh_open_p2":int(p2 or 0),
            })
        digest.update((
            f"{mid}|{issued.astimezone(UTC).isoformat()}|{tour}|{reason}|"
            f"{int(id_ever or 0)}|{int(id_pit or 0)}|{int(ever or 0)}|"
            f"{int(pit or 0)}|{int(recent or 0)}|{int(p1 or 0)}|{int(p2 or 0)}\n"
        ).encode())
    return {
        "version":VERSION,"as_of_utc":as_of.isoformat(),
        "dataset_sha256":digest.hexdigest(),
        "funnel_not_a_strictly_nested_chain":dict(sorted(funnel.items())),
        "mutually_exclusive_first_issue_quote_blockers":dict(sorted(reasons.items())),
        "by_tour":{k:dict(sorted(v.items())) for k,v in sorted(by_tour.items())},
        "first_50_unpaired_examples_no_provider_secrets":examples,
        "database_writes":False,"paid_api_calls":0,"subscriptions_purchased":0,
        "market_quotes_retrospectively_repaired":False,
        "note":[
            "Mapping ever present versus created by publication is separate from bookmaker odds seen by the original issue; no source timestamp can be inferred solely from match_source_ids.",
            "A provider match mapping alone is insufficient to establish that a bookmaker quote was captured, synchronized and ingested before the Model Pick.",
            "When both open sides exist they can still be different bookmakers or timestamps; strict benchmark requires same bookmaker and <5 minutes span, with 6h freshness.",
            "Opening/closing historical odds fetched today must not be presented as originally ingested at a past issue cutoff.",
            "All populations are original aligned Model Picks, NOT a complete external official ATP/Challenger fixture slate; market coverage conditional on previously published picks only.",
            "This is diagnostic, not evidence bookmakers won against the AI on new identical matches.",
        ],
    }


def run(conn:psycopg.Connection, *,as_of:datetime)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    return summarize(conn.execute(query(),{"as_of":as_of}).fetchall(),as_of=as_of)


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-utc",required=True)
    args=parser.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        parser.error("must use UTC ISO-8601 Z")
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    stamp=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00"))
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        out=run(conn,as_of=stamp)
    print("TENNIS_ODDSPAPI_FIRST_ISSUE_GAP "+json.dumps(out,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
