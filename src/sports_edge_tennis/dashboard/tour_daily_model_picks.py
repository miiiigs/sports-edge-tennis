"""Read-only daily ATP/Challenger original aligned Model Picks by first-issued match.

Uses precisely the dashboard's canonical first-qualified production aligned CTE,
with point-in-time tour/draw and immutable selected side. The *latest verified*
terminal outcome is graded separately; retrospective model or pick changes are
forbidden. NO_BET wide-spread tracking is explicitly not Manager consensus.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo
import psycopg
from sports_edge_tennis.dashboard.aligned_model_picks import aligned_production_model_picks_cte

MANILA = ZoneInfo("Asia/Manila")
VALID_PICK_TYPES = frozenset({"MANAGER_CONSENSUS","A1_A2_WIDE_SPREAD"})


def daily_tour_sql() -> str:
    return f"""
    WITH {aligned_production_model_picks_cte()},
    original AS (
      SELECT p.match_id, p.picked_at, p.selected_side, p.aligned_pick_type,
        COALESCE((mv.match_start_at AT TIME ZONE 'Asia/Manila')::date,
                 (p.picked_at AT TIME ZONE 'Asia/Manila')::date) AS day,
        mv.tour_scope::text AS tour_scope, mv.qualifying_flag,
        result.outcome_status, result.winner_side
      FROM aligned_model_picks p
      LEFT JOIN LATERAL (
        SELECT mv0.match_start_at, mv0.tour_scope, mv0.qualifying_flag
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
          AND mro.outcome_status IN ('COMPLETED','RETIRED','WALKOVER','CANCELLED')
          AND mro.as_of <= %(as_of)s
          AND mro.source_observed_at <= %(as_of)s
        ORDER BY mro.as_of DESC,mro.source_observed_at DESC,
                 mro.match_result_observation_id DESC LIMIT 1
      ) result ON TRUE
      WHERE p.picked_at <= %(as_of)s
    )
    SELECT match_id,day,tour_scope,qualifying_flag,aligned_pick_type,
           selected_side,outcome_status,winner_side
    FROM original
    WHERE day BETWEEN %(start_day)s::date AND %(end_day)s::date
    ORDER BY day, match_id
    """


def _counter() -> dict[str,int]:
    return {"picks":0,"won":0,"lost":0,"void":0,"pending":0}


def _classified(row: tuple) -> tuple[str,str,str,str]:
    _,_,tour,qualifying,pick_type,selected,status,winner = row
    kind = "CHALLENGER" if tour=="CHALLENGER" else (
        "ATP_TOUR" if tour=="ATP_MAIN" else "UNCLASSIFIED_TOUR"
    )
    draw = "QUALIFYING" if qualifying is True else (
        "MAIN_DRAW" if qualifying is False else "UNKNOWN_DRAW"
    )
    if pick_type not in VALID_PICK_TYPES:
        raise ValueError("unknown original pick type")
    if selected not in (1,2):
        raise ValueError("invalid original first-issued selected_side")
    if status == "COMPLETED" and winner in (1,2):
        result = "won" if winner==selected else "lost"
    elif status in ("RETIRED","WALKOVER","CANCELLED"):
        result = "void"
    else:
        result = "pending"
    return kind,draw,pick_type,result


def _finalize(counter: dict[str,int]) -> dict[str,Any]:
    graded=counter["won"]+counter["lost"]
    if counter["picks"] != graded+counter["void"]+counter["pending"]:
        raise ValueError("model pick status count not conserved")
    return {
        **counter, "settled_wl":graded,
        "accuracy_ex_void_pending":counter["won"]/graded if graded else None,
    }


def summarize_daily(
    rows: list[tuple], *,
    start_day: date,end_day: date,as_of: datetime,
) -> dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("as_of timezone missing")
    if end_day<start_day or (end_day-start_day).days>119:
        raise ValueError("daily report window maximum 120 days")
    daily=defaultdict(_counter)
    grouped=defaultdict(_counter)
    total=_counter()
    seen=set()
    for row in rows:
        mid,day = int(row[0]),row[1]
        if mid in seen:
            raise ValueError("duplicate first-issued sporting match")
        seen.add(mid)
        if not start_day<=day<=end_day:
            raise ValueError("row outside requested reporting interval")
        tour,draw,pick_type,result=_classified(row)
        for c in (daily[day],grouped[(day,tour,draw,pick_type)],total):
            c["picks"]+=1
            c[result]+=1
    current_day=as_of.astimezone(MANILA).date()
    days=[]
    day=start_day
    while day<=end_day:
        groups=[
            {"tour":tier,"draw":draw,"pick_type":pick_type,**_finalize(counts)}
            for (bucket,tier,draw,pick_type),counts in sorted(grouped.items())
            if bucket==day
        ]
        c=_finalize(daily[day])
        days.append({
            "date":day.isoformat(), **c,"groups":groups,
            "provisional":bool(c["pending"] or day>=current_day),
            "provisional_reason":"PENDING_OR_IN_PROGRESS" if c["pending"] or day>=current_day else None,
        })
        day+=timedelta(days=1)
    return {
        "generated_at":as_of.isoformat(),"timezone":"Asia/Manila",
        "start_date":start_day.isoformat(),"end_date":end_day.isoformat(),
        "first_issued_rule":"one earliest original aligned production Model Pick per canonical sporting match",
        "selection_types":"Manager consensus and A1/A2 wide-spread tracking are kept separate",
        "grading_rule":"completed with verified winner is W/L; retirement, walkover and cancellation VOID; missing source result PENDING",
        "uncertainty_notice":"Today's totals and any date with pending results are provisional; no automatic voids, no odds or ROI claims.",
        "daily":days,"all_dates":_finalize(total),
        "all_dates_provisional":any(d["provisional"] for d in days),
        "unclassified_tour_picks":sum(c["picks"] for (d,t,draw,p),c in grouped.items() if t=="UNCLASSIFIED_TOUR"),
    }


def load_tour_daily(conn:psycopg.Connection, *,
                    start_day:date,end_day:date,
                    as_of:datetime|None=None) -> dict[str,Any]:
    now=as_of or datetime.now(UTC)
    if end_day<start_day or (end_day-start_day).days>119:
        raise ValueError("daily report window maximum 120 days")
    rows=conn.execute(daily_tour_sql(),{
        "as_of":now,"start_day":start_day,"end_day":end_day,
    }).fetchall()
    return summarize_daily(rows,start_day=start_day,end_day=end_day,as_of=now)
