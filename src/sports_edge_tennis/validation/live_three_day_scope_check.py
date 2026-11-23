"""PIT tour/main-draw audit for the same canonical dashboard three-day IDs.

Scopes by the immutable original match version visible when first qualified.
This does not count an external official fixture universe.
"""
from __future__ import annotations
import argparse
import json
import os
from collections import Counter
from datetime import UTC,date
import psycopg
from sports_edge_tennis.dashboard.model_pick_history import load_aligned_daily_model_picks
from sports_edge_tennis.dashboard.aligned_model_picks import aligned_production_model_picks_cte


def run(conn, *, first_day:date, days:int=3):
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    by_day={}
    rows=[]
    for i in range(days):
        day=date.fromordinal(first_day.toordinal()+i)
        picks=load_aligned_daily_model_picks(conn,decision_date=day,timezone_name="Asia/Manila")
        by_day.update({p.match_id:day.isoformat() for p in picks})
        rows.extend(picks)
    ids=sorted(by_day)
    if not ids:
        return {"by_day":{},"details":[]}
    raw=conn.execute(f"""
        WITH {aligned_production_model_picks_cte()}
        SELECT p.match_id,
               v.tour_scope::text, v.competition_tier::text,
               v.qualifying_flag, v.round_code::text,
               t.canonical_name AS tournament_name
        FROM aligned_model_picks p
        LEFT JOIN LATERAL (
          SELECT mv.tour_scope,mv.competition_tier,mv.qualifying_flag,mv.round_code
          FROM match_versions mv
          WHERE mv.match_id=p.match_id
            AND mv.as_of<=p.picked_at
            AND mv.source_observed_at<=p.picked_at
            AND mv.ingested_at<=p.picked_at
          ORDER BY mv.as_of DESC,mv.source_observed_at DESC,mv.match_version_id DESC LIMIT 1
        ) v ON TRUE
        JOIN matches m ON m.match_id=p.match_id
        JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
        JOIN tournaments t ON t.tournament_id=te.tournament_id
        WHERE p.match_id = ANY(%s)
    """,(ids,)).fetchall()
    if len(raw)!=len(rows):
        raise ValueError("not all original aligned fixtures mapped to a PIT scope")
    per_day={}
    statuses={p.match_id:p.result_status for p in rows}
    for d in sorted(set(by_day.values())):
        cohort=[r for r in raw if by_day[int(r[0])]==d]
        c=Counter((str(r[1]),str(r[2]),str(r[3])) for r in cohort)
        elig=[r for r in cohort if r[1] in ("ATP_MAIN","CHALLENGER")
              and r[3] is False]
        graded=[r for r in elig if statuses[int(r[0])] in ("WON","LOST")]
        w=sum(statuses[int(r[0])]=="WON" for r in graded)
        per_day[d]={"all_dashboard_aligned":len(cohort),
            "confirmed_ATP_Ch_main_draw_issued":len(elig),
            "main_draw_wins":w,"main_draw_losses":len(graded)-w,
            "main_draw_scored_accuracy":w/len(graded) if graded else None,
            "main_draw_pending":sum(statuses[int(r[0])]=="PENDING" for r in elig),
            "main_draw_void":sum(statuses[int(r[0])]=="VOID" for r in elig),
            "tour_tier_qualifier_count":{"|".join(key):val for key,val in sorted(c.items())}}
    details=[{
        "day":by_day[int(r[0])],"match_id":int(r[0]),
        "original_pit_tour":str(r[1]),"tier":str(r[2]),
        "qualifying_flag":r[3],"round":str(r[4]),
        "tournament":str(r[5]),"result":statuses[int(r[0])],
    } for r in sorted(raw)]
    return {"method":"original_aligned_pick_PIT_visibility_and_tour_flag",
            "official_full_fixture_denominator_verified":False,
            "by_day":per_day,"details":details}


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser()
    p.add_argument("--start-manila",required=True)
    p.add_argument("--days",type=int,default=3)
    args=p.parse_args(argv)
    with psycopg.connect(os.environ["TENNIS_DATABASE_URL"],autocommit=False,
                         connect_timeout=12) as conn:
        out=run(conn,first_day=date.fromisoformat(args.start_manila),days=args.days)
    print("TENNIS_LIVE_THREE_DAY_SCOPE "+json.dumps(out,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
