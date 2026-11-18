"""Export original pre-match ATP/Challenger picks for licensed offline comparison.

Only first-issued non-experiment aligned Model Picks; one query and one
read-only PostgreSQL connection. Exports numeric canonical identities, no
player names, bookmaker quotes, emails or user data. Never posts to vendors.
Result observations are used only for *evaluation*, never forecast inputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)
from sports_edge_tennis.validation.competitor_forward_audit import (
    compare, utc,
)

FIXTURE_FIELDS=("match_id","tour","draw","player1_id","player2_id",
                "tournament_id","frozen_start_utc","result_status","winner_side")
FORECAST_FIELDS=("source","match_id","player1_id","player2_id","p_player1",
                 "published_at_utc","captured_at_utc","rights","model_version")
CUTOFF_MINUTES=30


def source_sql() -> str:
    return f"""
    WITH {aligned_production_model_picks_cte()}
    SELECT p.match_id, p.aligned_pick_type, p.selected_side, p.aligned_probability,
           pr.prediction_finalized_at,
           mv.match_start_at, mv.tour_scope::text AS tour, mv.qualifying_flag,
           mv.player_1_id, mv.player_2_id,
           te.tournament_id, mpv.policy_key, mpv.policy_version,
           result.outcome_status, result.winner_side
    FROM aligned_model_picks p
    JOIN manager_consensus_results mcr
      ON mcr.manager_consensus_result_id = p.manager_consensus_result_id
    JOIN manager_policy_versions mpv
      ON mpv.manager_policy_version_id = mcr.manager_policy_version_id
    JOIN prediction_runs pr ON pr.prediction_run_id = p.prediction_run_id
    JOIN matches m ON m.match_id=p.match_id
    JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
    LEFT JOIN LATERAL (
        SELECT mv0.match_start_at,mv0.tour_scope,mv0.qualifying_flag,
               mv0.player_1_id,mv0.player_2_id
        FROM match_versions mv0
        WHERE mv0.match_id=p.match_id
          AND mv0.as_of <= p.picked_at
          AND mv0.source_observed_at <= p.picked_at
        ORDER BY mv0.as_of DESC,mv0.source_observed_at DESC,
                 mv0.match_version_id DESC LIMIT 1
    ) mv ON TRUE
    LEFT JOIN LATERAL (
        SELECT mro.outcome_status::text AS outcome_status, mro.winner_side
        FROM match_result_observations mro
        WHERE mro.match_id=p.match_id
          AND mro.as_of <= %(as_of)s
          AND mro.source_observed_at <= %(as_of)s
          AND mro.ingested_at <= %(as_of)s
        ORDER BY mro.as_of DESC,mro.source_observed_at DESC,
                 mro.match_result_observation_id DESC LIMIT 1
    ) result ON TRUE
    WHERE pr.prediction_finalized_at <= %(as_of)s
      AND mv.match_start_at >= %(start_at)s
      AND mv.match_start_at < %(end_at)s
      AND mv.tour_scope IN ('ATP_MAIN','CHALLENGER')
    ORDER BY mv.match_start_at,p.match_id
    """


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("source timestamp lacks timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00","Z")


def normalize(rows:list[tuple],*,cutoff_minutes:int=CUTOFF_MINUTES
              ) -> tuple[list[dict[str,str]],list[dict[str,str]],dict[str,int]]:
    fixtures:list[dict[str,str]]=[]
    forecasts:list[dict[str,str]]=[]
    rejects:dict[str,int]={}
    unique:set[str]=set()

    def reject(reason:str) -> None:
        rejects[reason]=rejects.get(reason,0)+1

    for (mid,pick_type,side,prob,finalized,start,tour,qual,p1,p2,tournament,
         policy_key,policy_version,result,winner) in rows:
        match_id=str(mid)
        if match_id in unique:
            raise ValueError("duplicate canonical first-issued match")
        unique.add(match_id)
        if not isinstance(start,datetime) or not isinstance(finalized,datetime):
            reject("MISSING_PIT_START_OR_FINALIZATION")
            continue
        if not p1 or not p2 or not tournament:
            reject("MISSING_POINT_IN_TIME_CANONICAL_ID")
            continue
        if side not in (1,2) or prob is None:
            reject("UNSCORABLE_ORIGINAL_PROBABILITY")
            continue
        p=float(prob)
        if not 0<p<1 or not policy_key or not policy_version:
            reject("INVALID_PROBABILITY_OR_POLICY")
            continue
        if tour not in ("ATP_MAIN","CHALLENGER") or qual not in (True,False):
            reject("OUT_OF_SCOPE_OR_DRAW_UNKNOWN")
            continue
        deadline=start-timedelta(minutes=cutoff_minutes)
        if finalized > deadline or finalized.tzinfo is None or start.tzinfo is None:
            reject("NO_AUTHENTIC_30MIN_PREMATCH_ISSUANCE")
            continue
        if result not in ("COMPLETED","RETIRED","WALKOVER","CANCELLED",None):
            reject("UNRECOGNIZED_AUTHORITATIVE_RESULT")
            continue
        if result=="COMPLETED" and winner not in (1,2):
            reject("COMPLETED_RESULT_WITHOUT_VERIFIED_WINNER")
            continue
        p1id,p2id=str(p1),str(p2)
        fixtures.append({
            "match_id":match_id,
            "tour":"ATP_TOUR" if tour=="ATP_MAIN" else "CHALLENGER",
            "draw":"QUALIFYING" if qual else "MAIN_DRAW",
            "player1_id":p1id,"player2_id":p2id,
            "tournament_id":str(tournament),
            "frozen_start_utc":_iso(start),
            "result_status":result or "PENDING",
            "winner_side":str(winner) if result=="COMPLETED" else "",
        })
        forecasts.append({
            "source":"sports_edge_original","match_id":match_id,
            "player1_id":p1id,"player2_id":p2id,
            "p_player1":format(p if side==1 else 1-p,".15g"),
            "published_at_utc":_iso(finalized),
            "captured_at_utc":_iso(finalized),
            "rights":"internal",
            "model_version":f"{policy_key}:{policy_version}:{pick_type}",
        })
    return fixtures,forecasts,rejects


def run(conn:psycopg.Connection, *,as_of:datetime,start_at:datetime,
        end_at:datetime,cutoff_minutes:int=CUTOFF_MINUTES)->dict[str,Any]:
    if as_of.tzinfo is None or start_at.tzinfo is None or end_at.tzinfo is None:
        raise ValueError("all time boundaries must be timezone-aware")
    if start_at>=end_at or end_at-start_at>timedelta(days=120):
        raise ValueError("max 120 calendar-day export window")
    if cutoff_minutes!=CUTOFF_MINUTES:
        raise ValueError("benchmark cutoff is frozen at 30 minutes")
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(source_sql(),{
        "as_of":as_of,"start_at":start_at,"end_at":end_at,
    }).fetchall()
    fixtures,forecasts,rejects=normalize(rows,cutoff_minutes=cutoff_minutes)
    report=compare(fixtures,forecasts,cutoff_minutes=cutoff_minutes)
    report["export_stats"]={
        "query_original_rows":len(rows),"exported_first_issued":len(fixtures),
        "rejection_counts":rejects,
        "source_as_of":_iso(as_of),
        "range_start":_iso(start_at),"range_end_exclusive":_iso(end_at),
        "publication_rule":"prediction_runs.prediction_finalized_at, not retrospective match result timestamp",
        "source":"READ_ONLY_CANONICAL_ATP_CH_ORIGINAL",
        "external_vendor_matches":0,
        "interpretation":"Original baseline only; no competitor head-to-head until authorized external capture files are matched",
    }
    return {"fixtures":fixtures,"forecasts":forecasts,"report":report}


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-utc",required=True)
    parser.add_argument("--end-utc-exclusive",required=True)
    parser.add_argument("--as-of-utc",required=True)
    parser.add_argument("--output-dir",required=True,type=Path)
    args=parser.parse_args(argv)
    if not args.output_dir.is_dir():
        parser.error("output directory must exist and be access-controlled")
    files=[args.output_dir/name for name in (
        "sports_edge_fixtures.csv","sports_edge_first_issued.csv",
        "sports_edge_baseline_metrics.json")]
    if any(f.exists() for f in files):
        parser.error("refusing to replace existing source evidence")
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        output=run(conn,as_of=utc(args.as_of_utc),
                   start_at=utc(args.start_utc),
                   end_at=utc(args.end_utc_exclusive))
    # Force private local CSV permissions even if the runtime umask is permissive.
    os.umask(0o077)
    for path,fields,records in [
        (files[0],FIXTURE_FIELDS,output["fixtures"]),
        (files[1],FORECAST_FIELDS,output["forecasts"]),
    ]:
        with path.open("x",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
    files[2].write_text(json.dumps(output["report"],sort_keys=True,indent=2)+"\n",encoding="utf-8")
    # Safe summary: file hashes, aggregate counts only. No credentials or IDs.
    print(json.dumps({
        "source_as_of":output["report"]["export_stats"]["source_as_of"],
        "row_count":len(output["fixtures"]),
        "file_sha256":{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
        "no_database_writes":True,"no_odds_calls":True,
    },sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
