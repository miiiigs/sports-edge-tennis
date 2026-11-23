"""Audit why historical result-time match-version joins cannot be reconstructed.

Read-only: distinguish absent match-version rows, late source/as_of/ingestion,
and genuine 30-minute pre-match version visibility. Never use a retrospectively
loaded fixture version as if it were known at match issue time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

import psycopg

VERSION="HISTORICAL_MATCH_VERSION_VISIBILITY_RECONCILIATION_V1"


def source_sql()->str:
    return """
        WITH historical_results AS (
          SELECT DISTINCT ON(r.match_id)
            r.match_id,r.as_of AS result_as_of,
            r.source_observed_at AS result_source_observed,
            r.ingested_at AS result_ingested
          FROM match_result_observations r
          WHERE r.as_of <= %(as_of)s
            AND r.source_observed_at <= %(as_of)s
            AND r.ingested_at <= %(as_of)s
          ORDER BY r.match_id,r.as_of DESC,r.source_observed_at DESC,
                   r.match_result_observation_id DESC
        ),
        historical AS (
          SELECT te.season_year,hr.*
          FROM historical_results hr
          JOIN matches m ON m.match_id=hr.match_id
          JOIN tournament_editions te
            ON te.tournament_edition_id=m.tournament_edition_id
          WHERE te.season_year BETWEEN %(start_year)s AND %(end_year)s
        ),
        versions AS (
          SELECT v.match_id,count(*) AS version_count,
                 min(v.as_of) AS earliest_version_as_of,
                 min(v.source_observed_at) AS earliest_version_source_observed,
                 min(v.ingested_at) AS earliest_version_ingested,
                 min(GREATEST(v.as_of,v.source_observed_at,v.ingested_at))
                      AS earliest_version_fully_visible,
                 count(*) FILTER(
                   WHERE v.as_of <= v.match_start_at - INTERVAL '30 minutes'
                     AND v.source_observed_at <= v.match_start_at - INTERVAL '30 minutes'
                     AND v.ingested_at <= v.match_start_at - INTERVAL '30 minutes'
                 ) AS preissue_observed_version_rows,
                 min(v.match_start_at) AS earliest_match_start
          FROM match_versions v
          JOIN historical h ON h.match_id=v.match_id
          WHERE v.as_of<=%(as_of)s
            AND v.source_observed_at<=%(as_of)s
            AND v.ingested_at<=%(as_of)s
          GROUP BY v.match_id
        )
        SELECT h.season_year,h.match_id,
               h.result_as_of,h.result_source_observed,h.result_ingested,
               v.version_count,v.earliest_version_as_of,
               v.earliest_version_source_observed,
               v.earliest_version_ingested,
               v.earliest_version_fully_visible,
               v.preissue_observed_version_rows,
               v.earliest_match_start
        FROM historical h
        LEFT JOIN versions v ON v.match_id=h.match_id
        ORDER BY h.season_year,h.match_id
    """


def summarize(rows:list[tuple], *,
              start_year:int=2016,end_year:int=2025)->dict[str,Any]:
    if start_year>end_year or start_year<1900 or end_year>2100:
        raise ValueError("invalid year range")
    totals=Counter()
    by_year=defaultdict(Counter)
    digest=hashlib.sha256()
    samples=[]
    seen=set()
    for (year,match_id,result_asof,result_source,result_ingested,
         version_count,v_asof,v_source,v_ingested,v_visible,
         pit_count,start_at) in rows:
        year=int(year)
        match_id=int(match_id)
        if match_id in seen or not start_year<=year<=end_year:
            raise ValueError("duplicate canonical match or year outside scope")
        seen.add(match_id)
        if any(t is None or t.tzinfo is None for t in
               (result_asof,result_source,result_ingested)):
            raise ValueError("historical result missing timezone-aware timestamps")
        c=by_year[str(year)]
        c["stored_historical_result_matches"]+=1
        category=""
        if version_count is None or int(version_count)==0:
            category="NO_VERSION_STORED_AS_OF_REPORT"
            c[category]+=1
        else:
            c["MATCH_VERSION_STORED_AS_OF_REPORT"]+=1
            count=int(version_count)
            if any(t is None or t.tzinfo is None for t in
                   (v_asof,v_source,v_ingested,v_visible)):
                raise ValueError("stored version missing required chronology")
            if v_asof>result_asof:
                c["EVERY_VERSION_AS_OF_AFTER_RESULT_AS_OF"]+=1
            if v_source>result_asof:
                c["EVERY_VERSION_SOURCE_AFTER_RESULT_AS_OF"]+=1
            if v_ingested>result_asof:
                c["EVERY_VERSION_INGESTED_AFTER_RESULT_AS_OF"]+=1
            if v_visible>result_asof:
                category="FIRST_VERSION_VISIBILITY_AFTER_RESULT_AS_OF"
                c[category]+=1
            else:
                category="AT_LEAST_ONE_VERSION_VISIBLE_AT_RESULT_AS_OF"
                c[category]+=1
            if int(pit_count or 0):
                c["HAS_STRICT_PREMATCH_VERSION"]+=1
            else:
                c["NO_STRICT_PREMATCH_VERSION"]+=1
            if start_at is not None and v_visible>=start_at:
                c["FIRST_VERSION_NOT_VISIBLE_UNTIL_MATCH_STARTED"]+=1
        if len(samples)<12:
            samples.append({
                "season":year,"match_id":match_id,
                "result_as_of":result_asof.isoformat(),
                "earliest_version_as_of":v_asof.isoformat() if v_asof else None,
                "earliest_version_ingested":v_ingested.isoformat() if v_ingested else None,
                "version_count":int(version_count or 0),
                "classification":category,
            })
        digest.update(f"{year}|{match_id}|{category}|{version_count or 0}|{pit_count or 0}\n".encode())
    for v in by_year.values():
        totals.update(v)
    return {
        "version":VERSION,
        "status":"HISTORICAL_VERSION_TIMESTAMP_RECONCILIATION_ONLY",
        "start_year":start_year,"end_year":end_year,
        "dataset_sha256":digest.hexdigest(),
        "totals":dict(sorted(totals.items())),
        "per_year":{k:dict(sorted(v.items())) for k,v in sorted(by_year.items())},
        "samples_first_12_for_replay":samples,
        "db_writes":False,"no_retroactive_predictions":True,
        "historical_75pct_proven":False,
        "interpretation":(
            "Counts historical match-version visibility against result-as_of "
            "and match start. A version loaded after the result cannot prove "
            "what the AI knew before the match. Existence of an archive row "
            "is not immutable pre-match prediction evidence. This does not "
            "measure the accuracy of the claimed 14k predictions or certify "
            "absence of player-order/feature/A4 leakage."
        ),
    }


def run(conn:psycopg.Connection,*,as_of:datetime,
        start_year:int=2016,end_year:int=2025)->dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("as-of must be timezone aware")
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(source_sql(),{
        "as_of":as_of.astimezone(UTC),
        "start_year":start_year,"end_year":end_year
    }).fetchall()
    out=summarize(rows,start_year=start_year,end_year=end_year)
    out["as_of_utc"]=as_of.astimezone(UTC).isoformat()
    return out


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of-utc",required=True)
    p.add_argument("--start-year",type=int,default=2016)
    p.add_argument("--end-year",type=int,default=2025)
    args=p.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        p.error("as-of-utc must end in Z")
    dsn=os.getenv("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        out=run(
            conn,as_of=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00")),
            start_year=args.start_year,end_year=args.end_year,
        )
    print("TENNIS_HISTORICAL_VERSION_VISIBILITY "+json.dumps(out,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
