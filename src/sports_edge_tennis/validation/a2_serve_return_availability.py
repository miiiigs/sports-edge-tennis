"""Read-only two-sided serve/return data availability audit for A2 research.

Brian/Gollub opponent-strength adaptation needs *both* players' historical
service and return sample sizes. This report measures recorded source coverage,
not whether that row was actually known before every earlier prediction.
Never imputes missing point counts to zero, nor licenses third-party datasets.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from typing import Any
import psycopg

VERSION="A2_TWO_SIDED_SERVE_RETURN_AVAILABILITY_V1"


def source_query()->str:
    return """
        WITH mv AS (
          SELECT DISTINCT ON (match_id)
             match_id,player_1_id,player_2_id,
             tour_scope::text AS tour_scope,
             COALESCE(surface::text,'UNKNOWN') AS surface,
             match_start_at
          FROM match_versions
          WHERE as_of<=%(as_of)s
            AND source_observed_at<=%(as_of)s
            AND ingested_at<=%(as_of)s
          ORDER BY match_id,as_of DESC,source_observed_at DESC,
                   match_version_id DESC
        ),
        stats AS (
          SELECT DISTINCT ON (match_id,player_id)
            match_id,player_id,first_serves_in,
            first_serve_points_won,second_serve_points_won,
            service_points_played,return_points_won,
            break_points_saved,break_points_faced
          FROM match_stat_observations
          WHERE observation_kind='NORMALIZED_SOURCE'
            AND as_of<=%(as_of)s
            AND source_observed_at<=%(as_of)s
            AND ingested_at<=%(as_of)s
          ORDER BY match_id,player_id,as_of DESC,
                   source_observed_at DESC,match_stat_observation_id DESC
        ),
        pairs AS (
          SELECT mv.tour_scope,mv.surface,mv.match_id,
             (s1.player_id IS NOT NULL AND s2.player_id IS NOT NULL)
                 AS two_sided,
             (
               s1.first_serve_points_won IS NOT NULL
               AND s1.second_serve_points_won IS NOT NULL
               AND s1.service_points_played>0
               AND s1.return_points_won IS NOT NULL
               AND s2.first_serve_points_won IS NOT NULL
               AND s2.second_serve_points_won IS NOT NULL
               AND s2.service_points_played>0
               AND s2.return_points_won IS NOT NULL
             ) AS essential_complete,
             (
               s1.first_serves_in IS NOT NULL
               AND s2.first_serves_in IS NOT NULL
               AND s1.break_points_saved IS NOT NULL
               AND s2.break_points_saved IS NOT NULL
               AND s1.break_points_faced IS NOT NULL
               AND s2.break_points_faced IS NOT NULL
             ) AS extended_complete
          FROM mv
          LEFT JOIN stats s1 ON s1.match_id=mv.match_id
                            AND s1.player_id=mv.player_1_id
          LEFT JOIN stats s2 ON s2.match_id=mv.match_id
                            AND s2.player_id=mv.player_2_id
        )
        SELECT tour_scope,surface,count(*) AS recorded_matches,
               count(*) FILTER (WHERE two_sided) AS two_sided,
               count(*) FILTER (WHERE essential_complete) AS essential_complete,
               count(*) FILTER (WHERE essential_complete AND extended_complete)
                     AS extended_complete
        FROM pairs
        GROUP BY tour_scope,surface
        ORDER BY tour_scope,surface
    """


def source_inventory_sql()->str:
    return """
        SELECT source_system,observation_kind::text,
               count(*) AS rows_recorded,
               count(DISTINCT match_id) AS distinct_matches
        FROM match_stat_observations
        WHERE as_of<=%(as_of)s
          AND source_observed_at<=%(as_of)s
          AND ingested_at<=%(as_of)s
        GROUP BY source_system,observation_kind::text
        ORDER BY count(*) DESC,source_system,observation_kind::text
    """


def report_coverage(rows:list[tuple], source_rows:list[tuple],
                    *, as_of:datetime)->dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must have timezone")
    group=[]
    totals={"recorded_matches":0,"two_sided":0,
            "essential_complete":0,"extended_complete":0}
    by_tour={}
    for tour,surface,raw,paired,essential,extended in rows:
        count={"recorded_matches":int(raw),"two_sided":int(paired),
               "essential_complete":int(essential),
               "extended_complete":int(extended)}
        if not (0<=count["extended_complete"]<=count["essential_complete"]
                <=count["two_sided"]<=count["recorded_matches"]):
            raise ValueError("incoherent match-stat source completeness counts")
        group.append({"tour":str(tour),"surface":str(surface),**count,
                      "essential_coverage_pct":
                      100*count["essential_complete"]/count["recorded_matches"]
                      if count["recorded_matches"] else None})
        for key in totals:
            totals[key]+=count[key]
        sub=by_tour.setdefault(str(tour),{k:0 for k in totals})
        for key in sub:
            sub[key]+=count[key]
    for sub in by_tour.values():
        sub["essential_coverage_pct"]=(
            100*sub["essential_complete"]/sub["recorded_matches"]
            if sub["recorded_matches"] else None)
    inv=[{
        "source_system":str(code),"observation_kind":str(kind),
        "rows":int(rowcount),"distinct_matches":int(unique)
    } for code,kind,rowcount,unique in source_rows]
    return {
        "status":"HISTORICAL_STAT_SOURCE_AVAILABILITY_ONLY",
        "version":VERSION,"as_of_utc":as_of.astimezone(UTC).isoformat(),
        "totals":totals,"by_tour":by_tour,
        "tour_surface":group,"source_inventory":inv,
        "source_licenses_verified":False,
        "PIT_for_each_original_prediction_verified":False,
        "missing_values_imputed":False,
        "production_model_changed":False,"db_writes":False,
        "interpretation":(
            "Two-sided normalized historical serve/return point coverage is "
            "a necessary but insufficient precondition for adapting Gollub's "
            "opponent-adjusted A2. Availability at an original prediction's "
            "as-of time and rights to reuse source data must be proven separately."
        ),
    }


def run(conn:psycopg.Connection, *, as_of:datetime)->dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("timezone aware as_of required")
    conn.execute("SET default_transaction_read_only=on")
    params={"as_of":as_of}
    rows=conn.execute(source_query(),params).fetchall()
    sources=conn.execute(source_inventory_sql(),params).fetchall()
    return report_coverage(rows,sources,as_of=as_of)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of-utc",required=True)
    args=p.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        p.error("as-of-utc must include Z")
    if not os.environ.get("TENNIS_DATABASE_URL"):
        p.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(os.environ["TENNIS_DATABASE_URL"],
                         autocommit=True,connect_timeout=10) as conn:
        r=run(conn,as_of=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00")))
    print("A2_SOURCE_AVAILABILITY "+json.dumps(r,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
