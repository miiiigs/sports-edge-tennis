"""Read-only ATP/Challenger zero-pick weekend and exact five-pending audit.

No prediction, result, link, rank, odds, provider identity or settlement writes.
At most TWO bounded API-Tennis get_fixtures calls, no retry. Historical
re-observation on Nov19 is not proof of PIT availability on Nov14-16.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from sports_edge_tennis.live.credentials import require_api_tennis_key
from sports_edge_tennis.live.history_refresh import fetch_api_tennis_fixture_window
from sports_edge_tennis.live.transport import TransportError

PHT = ZoneInfo("Asia/Manila")
DATES = (date(2026,11,14),date(2026,11,15),date(2026,11,16))
PENDING_IDS = (130903,131712,133361,133347,133978)
PROVIDER_WINDOWS = ((date(2026,9,25),date(2026,10,7)),
                    (date(2026,11,7),date(2026,11,19)))
MAX_PROVIDER_CALLS=2

_CANONICAL_SQL = """
WITH first_seen AS (
 SELECT DISTINCT ON (match_id)
   match_id, match_start_at, tour_scope::text AS tour_scope,
   qualifying_flag, match_status::text AS match_status,
   as_of, source_observed_at
 FROM match_versions
 ORDER BY match_id, as_of ASC, source_observed_at ASC, match_version_id ASC
)
SELECT
 (fv.match_start_at AT TIME ZONE 'Asia/Manila')::date AS match_day,
 fv.tour_scope, fv.qualifying_flag,
 count(*) AS canonical_count,
 count(*) FILTER (WHERE fv.as_of <= fv.match_start_at-interval '30 minutes'
                  AND fv.source_observed_at<=fv.match_start_at-interval '30 minutes')
     AS observed_30min_prior,
 count(*) FILTER (WHERE EXISTS(
     SELECT 1 FROM match_source_ids si
     WHERE si.match_id=fv.match_id AND si.source_system='API_TENNIS'
       AND si.source_match_id IS NOT NULL)) AS api_linked,
 count(*) FILTER (WHERE EXISTS(
     SELECT 1 FROM predictions pp
     WHERE pp.match_id=fv.match_id AND pp.experiment_id IS NULL
       AND pp.prediction_locked_at<=fv.match_start_at-interval '30 minutes'))
     AS historical_prediction_rows_before_start,
 count(*) FILTER (WHERE EXISTS(
     SELECT 1 FROM match_result_observations ro
     WHERE ro.match_id=fv.match_id AND ro.outcome_status='COMPLETED'))
     AS ever_completed_result
FROM first_seen fv
WHERE (fv.match_start_at AT TIME ZONE 'Asia/Manila')::date
  BETWEEN %(start_day)s::date AND %(end_day)s::date
  AND fv.tour_scope IN ('ATP_MAIN','CHALLENGER')
GROUP BY 1,2,3 ORDER BY 1,2,3
"""
_PREDICTION_RUN_SQL = """
SELECT (pr.started_at AT TIME ZONE 'Asia/Manila')::date AS day,
       count(*) AS runs,
       count(*) FILTER (WHERE pr.run_status='COMPLETED') AS completed,
       count(*) FILTER (WHERE pr.run_status='FAILED') AS failed
FROM prediction_runs pr
WHERE pr.started_at >= %(start_at)s AND pr.started_at < %(end_at)s
GROUP BY 1 ORDER BY 1
"""
_INGEST_SQL = """
SELECT (started_at AT TIME ZONE 'Asia/Manila')::date AS day,
       source_system,source_dataset,status::text,
       count(*) AS runs,coalesce(sum(rows_seen),0) AS rows_seen,
       coalesce(sum(rows_inserted),0) AS rows_inserted
FROM ingest_runs
WHERE started_at >= %(start_at)s AND started_at < %(end_at)s
GROUP BY 1,2,3,4 ORDER BY 1,2,3,4
"""
_PENDING_SQL = """
WITH latest_mv AS (
 SELECT DISTINCT ON (match_id) match_id, match_start_at,
   player_1_id, player_2_id, match_status::text, as_of
 FROM match_versions WHERE match_id = ANY(%(match_ids)s)
 ORDER BY match_id,as_of DESC,source_observed_at DESC,match_version_id DESC
)
SELECT mv.match_id,mv.match_start_at,mv.match_status,si.source_match_id,
       mv.player_1_id,mv.player_2_id,
       (SELECT count(*) FROM match_result_observations r WHERE r.match_id=mv.match_id)
FROM latest_mv mv
LEFT JOIN LATERAL (
 SELECT source_match_id FROM match_source_ids si
 WHERE si.match_id=mv.match_id AND si.source_system='API_TENNIS'
 AND si.source_match_id IS NOT NULL
 ORDER BY si.match_source_id DESC LIMIT 1
) si ON TRUE
ORDER BY mv.match_id
"""


def _emit(label:str, **kwargs:Any)->None:
    print(label+" "+json.dumps(kwargs,default=str,sort_keys=True,separators=(",",":")),flush=True)


def _tier(raw:dict[str,Any])->str|None:
    txt=" ".join(str(raw.get("event_type_type") or "").casefold().split())
    if "men" not in txt or "singles" not in txt or "doubles" in txt:
        return None
    if "challenger" in txt:
        return "CHALLENGER"
    if txt.startswith("atp ") and "team" not in txt:
        return "ATP_MAIN"
    return None


def _event_day(raw:dict[str,Any])->date|None:
    v=str(raw.get("event_date") or "").strip()
    try:
        day=date.fromisoformat(v)
    except ValueError:
        return None
    tm=str(raw.get("event_time") or "").strip()
    try:
        clock=time.fromisoformat(tm)
    except ValueError:
        clock=time(12,0)
    return datetime.combine(day,clock,tzinfo=UTC).astimezone(PHT).date()


def raw_source_counts(payload:list[dict[str,Any]])->tuple[dict[str,Any],dict[str,dict[str,Any]]]:
    summaries=defaultdict(Counter)
    by_key:dict[str,dict[str,Any]]={}
    for row in payload:
        if not isinstance(row,dict):
            continue
        key=str(row.get("event_key") or "").strip()
        if key:
            by_key.setdefault(key,row)
        tier=_tier(row)
        if tier is None:
            continue
        day=_event_day(row)
        if day not in DATES:
            continue
        summaries[day.isoformat()][tier]+=1
        summaries[day.isoformat()][tier+"_terminal_status"]+=int(
            " ".join(str(row.get("event_status") or "").lower().split())
            in ("finished","completed","retired","walkover","walk over","cancelled","canceled")
        )
    return {day.isoformat():dict(summaries[day.isoformat()]) for day in DATES},by_key


def canonical_summary(conn:psycopg.Connection)->dict[str,Any]:
    args={"start_day":DATES[0],"end_day":DATES[-1],
          "start_at":datetime(2026,11,13,16,0,tzinfo=UTC),
          "end_at":datetime(2026,11,16,16,0,tzinfo=UTC)}
    match_rows=conn.execute(_CANONICAL_SQL,args).fetchall()
    run_rows=conn.execute(_PREDICTION_RUN_SQL,args).fetchall()
    ingest_rows=conn.execute(_INGEST_SQL,args).fetchall()
    day_list={}
    for day in DATES:
        key=day.isoformat()
        day_list[key]={
            "canonical_matches":sum(int(r[3]) for r in match_rows if r[0]==day),
            "pre_match_observed":sum(int(r[4]) for r in match_rows if r[0]==day),
            "api_linked":sum(int(r[5]) for r in match_rows if r[0]==day),
            "prediction_rows_pre_match":sum(int(r[6]) for r in match_rows if r[0]==day),
            "ever_completed_result":sum(int(r[7]) for r in match_rows if r[0]==day),
            "by_tier_draw":[
                {"tour":r[1],"qualifying":r[2],"matches":int(r[3]),
                 "pre_match_observed":int(r[4]),"predicted_before_start":int(r[6])}
                for r in match_rows if r[0]==day
            ],
            "prediction_runs_created_that_day":next((int(r[1]) for r in run_rows if r[0]==day),0),
            "prediction_runs_completed":next((int(r[2]) for r in run_rows if r[0]==day),0),
            "prediction_runs_failed":next((int(r[3]) for r in run_rows if r[0]==day),0),
            "ingest_jobs":sum(int(r[4]) for r in ingest_rows if r[0]==day),
            "ingest_rows_seen":sum(int(r[5]) for r in ingest_rows if r[0]==day),
        }
    return day_list


def pending_summary(conn:psycopg.Connection, provider:dict[str,dict[str,Any]])->list[dict[str,Any]]:
    rows=conn.execute(_PENDING_SQL,{"match_ids":list(PENDING_IDS)}).fetchall()
    result=[]
    for mid,start,canonical_status,event_key,player1,player2,n_results in rows:
        raw=provider.get(str(event_key)) if event_key else None
        result.append({
            "match_id":mid,"provider_event_key":event_key,
            "canonical_latest_status":canonical_status,
            "latest_start":start,
            "stored_results":int(n_results),
            "provider_returned":raw is not None,
            "provider_raw_status":str(raw.get("event_status") or "")[:85] if raw else None,
            "provider_raw_winner":str(raw.get("event_winner") or "")[:45] if raw else None,
            "provider_event_date":str(raw.get("event_date") or "") if raw else None,
            # Deliberately report raw source IDs only. Exact player+edition
            # bridge must be independently established before any write.
            "provider_raw_player1_key":str(raw.get("first_player_key") or "") if raw else None,
            "provider_raw_player2_key":str(raw.get("second_player_key") or "") if raw else None,
            "canonical_player1":player1,"canonical_player2":player2,
            "decision":"REVIEW_SOURCE_ID_NO_AUTOVOID_OR_REPAIR",
        })
    return result


def main()->int:
    with psycopg.connect(os.environ["TENNIS_DATABASE_URL"],connect_timeout=12,autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only=on")
        canonical=canonical_summary(conn)
        _emit("WEEKEND_CANONICAL_AUDIT",as_of=datetime.now(UTC).isoformat(),days=canonical,
              database_writes=0,odds_requests=0)
        key=None
        try:
            key=require_api_tennis_key()
        except Exception:
            _emit("WEEKEND_PROVIDER_UNAVAILABLE",reason="API_TENNIS_KEY_NOT_CONNECTED")
        payload=[]
        if key:
            for low,high in PROVIDER_WINDOWS:
                if (high-low).days+1>31:
                    raise AssertionError("unsafe provider window")
                try:
                    received,rows=fetch_api_tennis_fixture_window(key,low,high,timeout=45)
                    payload.extend(rows)
                    _emit("WEEKEND_PROVIDER_WINDOW",start=low,end=high,
                          raw_rows=len(rows),observed_at=received.isoformat())
                except TransportError as exc:
                    _emit("WEEKEND_PROVIDER_FAILED",start=low,end=high,category=exc.kind)
        summary,by_key=raw_source_counts(payload)
        _emit("WEEKEND_PROVIDER_AFTER_THE_FACT",days=summary,
              provider_calls=len(PROVIDER_WINDOWS) if key else 0,
              note="Later provider history does not prove fixture observed pre-match")
        _emit("PENDING_EXACT_SOURCE_AUDIT",items=pending_summary(conn,by_key),
              no_auto_voids=True,database_writes=0,odds_requests=0)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
