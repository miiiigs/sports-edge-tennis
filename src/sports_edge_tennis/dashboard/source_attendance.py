"""Read-only source/runner attendance behind daily ATP/Ch Model Picks.

A later-completed sporting match is NOT evidence that the model received the
fixture before it started. This monitor contrasts first persisted fixture time
against the 30m pre-match cutoff and counts actual ingestion and prediction
runs *on each Manila date*. It never recreates missing historical picks.
"""
from __future__ import annotations
from datetime import UTC,date,datetime,timedelta
from zoneinfo import ZoneInfo
from typing import Any
import psycopg

PHT=ZoneInfo("Asia/Manila")

_SOURCE_SQL="""
WITH first_seen AS (
 SELECT DISTINCT ON (match_id) match_id,match_start_at,
     tour_scope::text AS tour_scope,qualifying_flag,source_observed_at,as_of
 FROM match_versions ORDER BY match_id,as_of ASC,source_observed_at ASC,match_version_id ASC
),
 fixture_counts AS (
 SELECT (match_start_at AT TIME ZONE 'Asia/Manila')::date AS day,
     count(*) AS source_fixtures,
     count(*) FILTER (WHERE tour_scope='ATP_MAIN') AS atp,
     count(*) FILTER (WHERE tour_scope='CHALLENGER') AS challenger,
     count(*) FILTER (WHERE qualifying_flag) AS qualifying,
     count(*) FILTER (
         WHERE as_of<=match_start_at-interval '30 minutes'
           AND source_observed_at<=match_start_at-interval '30 minutes'
     ) AS observed_by_30m
 FROM first_seen
 WHERE tour_scope IN ('ATP_MAIN','CHALLENGER')
   AND (match_start_at AT TIME ZONE 'Asia/Manila')::date
       BETWEEN %(first)s::date AND %(last)s::date
 GROUP BY 1
),
 run_counts AS (
 SELECT (started_at AT TIME ZONE 'Asia/Manila')::date AS day,
     count(*) AS attempted,
     count(*) FILTER (WHERE run_status='COMPLETED') AS completed,
     count(*) FILTER (WHERE run_status='FAILED') AS failed
 FROM prediction_runs
 WHERE started_at >= %(utc_start)s AND started_at < %(utc_end)s
 GROUP BY 1
),
 ingest_counts AS (
 SELECT (started_at AT TIME ZONE 'Asia/Manila')::date AS day,
    count(*) AS attempted,
    count(*) FILTER (WHERE status='COMPLETED') AS completed
 FROM ingest_runs
 WHERE started_at >= %(utc_start)s AND started_at < %(utc_end)s
 GROUP BY 1
)
SELECT g.day::date,coalesce(f.source_fixtures,0),coalesce(f.atp,0),
       coalesce(f.challenger,0),coalesce(f.qualifying,0),
       coalesce(f.observed_by_30m,0),coalesce(r.attempted,0),
       coalesce(r.completed,0),coalesce(r.failed,0),
       coalesce(i.attempted,0),coalesce(i.completed,0)
FROM generate_series(%(first)s::date,%(last)s::date,interval '1 day') g(day)
LEFT JOIN fixture_counts f ON f.day=g.day::date
LEFT JOIN run_counts r ON r.day=g.day::date
LEFT JOIN ingest_counts i ON i.day=g.day::date
ORDER BY g.day
"""


def reason(
    *,
    source_fixtures:int, observed_by_30m:int,
    prediction_runs:int, ingest_runs:int,
    day:date, now_day:date,
) -> str:
    if day>=now_day:
        return "DATE_IN_PROGRESS"
    if source_fixtures==0:
        return "NO_CANONICAL_FIXTURES_NOT_PROOF_OF_EMPTY_TENNIS"
    if observed_by_30m==0 and prediction_runs==0 and ingest_runs==0:
        return "HISTORICAL_SOURCE_AND_RUN_GAP"
    if observed_by_30m==0:
        return "NO_VERIFIED_PREMATCH_SOURCE"
    if prediction_runs==0:
        return "NO_PREDICTION_RUN_RECORDED"
    return "SOURCE_AND_RUN_ATTENDANCE_PRESENT_NOT_PICK_ELIGIBILITY"


def summarize(rows:list[tuple],*,first:date,last:date,as_of:datetime)->dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone aware")
    if last<first or (last-first).days>59:
        raise ValueError("maximum 60 days")
    d=first
    days=[]
    current_day=as_of.astimezone(PHT).date()
    seen=set()
    for row in rows:
        day,source,atp,ch,qual,pit,runs,complete,failed,ingest,ingest_complete=row
        if day in seen or not first<=day<=last:
            raise ValueError("duplicate/out-of-range date")
        seen.add(day)
        days.append({
            "date":day.isoformat(),"canonical_fixtures_now":int(source),
            "atp_fixtures":int(atp),"challenger_fixtures":int(ch),
            "qualifying_fixtures":int(qual),
            "source_observed_before_30min":int(pit),
            "prediction_runs":int(runs),"completed_prediction_runs":int(complete),
            "failed_prediction_runs":int(failed),
            "ingestion_jobs":int(ingest),"completed_ingestion_jobs":int(ingest_complete),
            "source_attendance":reason(source_fixtures=int(source),observed_by_30m=int(pit),
                                       prediction_runs=int(runs),ingest_runs=int(ingest),
                                       day=day,now_day=current_day),
        })
    while d<=last:
        if d not in seen:
            raise ValueError("missing date in source attendance query")
        d+=timedelta(days=1)
    return {
        "generated_at":as_of.isoformat(),"timezone":"Asia/Manila",
        "first_date":first.isoformat(),"last_date":last.isoformat(),"days":days,
        "weekend_gap_dates":["2026-11-14","2026-11-15","2026-11-16"],
        "interpretation":"Historical canonical fixtures can be backfilled. Source attendance requires earliest persisted as-of <=30m before match start. No missing pre-match picks may be fabricated.",
        "warning":"Source attendance is descriptive, not a determination that every fixture qualified for a model prediction. Provider history observed later cannot prove contemporaneous availability.",
        "database_writes":0,
    }


def load_source_attendance(conn:psycopg.Connection,*,first:date,last:date,
                           as_of:datetime|None=None)->dict[str,Any]:
    now=as_of or datetime.now(UTC)
    if last<first or (last-first).days>59:
        raise ValueError("maximum 60 days")
    start=datetime.combine(first,datetime.min.time(),tzinfo=PHT).astimezone(UTC)
    end=datetime.combine(last+timedelta(days=1),datetime.min.time(),tzinfo=PHT).astimezone(UTC)
    rows=conn.execute(_SOURCE_SQL,{
        "first":first,"last":last,"utc_start":start,"utc_end":end
    }).fetchall()
    return summarize(rows,first=first,last=last,as_of=now)
