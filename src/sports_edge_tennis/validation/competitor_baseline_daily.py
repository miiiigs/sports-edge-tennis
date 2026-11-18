"""Read-only daily W/L/V/P from the immutable ATP/Ch competitor baseline export.

Consumes the *same* frozen first-issued fixture and prediction CSVs as the
proper-score benchmark. Reports daily and tour/model-mode subtotals with
conservation checks; does not touch production DB or vendor networks.
"""
from __future__ import annotations
import argparse
import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from sports_edge_tennis.validation.competitor_forward_audit import utc

PHT = ZoneInfo("Asia/Manila")
KNOWN_MODES = frozenset({"MANAGER_CONSENSUS", "A1_A2_WIDE_SPREAD"})
VOID_STATES = frozenset({"WALKOVER", "RETIRED", "CANCELLED", "VOID"})


def _zero() -> dict[str, int]:
    return {"picks":0,"won":0,"lost":0,"void":0,"pending":0}


def _finished(counter:dict[str,int])->dict[str,Any]:
    if counter["picks"]!=counter["won"]+counter["lost"]+counter["void"]+counter["pending"]:
        raise ValueError("results conservation failed")
    total=counter["won"]+counter["lost"]
    return {**counter,"graded":total,
            "settled_accuracy":round(counter["won"]/total,6) if total else None}


def daily_report(
    fixtures:list[dict[str,str]],forecasts:list[dict[str,str]], *,
    start_day:date,end_day:date,as_of_utc:str
)->dict[str,Any]:
    if end_day<start_day or (end_day-start_day).days>119:
        raise ValueError("daily window must be 1-120 days")
    as_of=utc(as_of_utc)
    fxs:dict[str,dict[str,str]]={}
    pcs:dict[str,dict[str,str]]={}
    for row in fixtures:
        mid=row.get("match_id","")
        if not mid or mid in fxs:
            raise ValueError("duplicate or empty fixture id")
        if row.get("tour") not in ("ATP_TOUR","CHALLENGER"):
            raise ValueError("outside ATP/Challenger scope")
        fxs[mid]=row
    for row in forecasts:
        mid=row.get("match_id","")
        if not mid or mid in pcs or mid not in fxs:
            raise ValueError("unknown or duplicate original forecast")
        if row.get("source")!="sports_edge_original":
            raise ValueError("mixed external forecast in original scorecard")
        if (row.get("player1_id")!=fxs[mid].get("player1_id") or
            row.get("player2_id")!=fxs[mid].get("player2_id")):
            raise ValueError("point-in-time player identity drift")
        if utc(row.get("published_at_utc",""))>as_of:
            raise ValueError("future-issued forecast in historical report")
        pcs[mid]=row
    if set(pcs)!=set(fxs):
        raise ValueError("fixture/first-issued prediction identity mismatch")

    totals=_zero()
    by_tour=defaultdict(_zero)
    by_mode=defaultdict(_zero)
    by_day=defaultdict(_zero)
    by_day_group=defaultdict(_zero)
    for mid,fx in fxs.items():
        selected=pcs[mid]
        match_start=utc(fx.get("frozen_start_utc",""))
        if utc(selected.get("captured_at_utc",""))>as_of:
            raise ValueError("capture after scoring cutoff")
        if match_start > as_of and fx.get("result_status")=="COMPLETED":
            raise ValueError("completed result before scheduled match start")
        day=match_start.astimezone(PHT).date()
        if not start_day<=day<=end_day:
            continue
        mode=selected.get("model_version","").rsplit(":",1)[-1]
        if mode not in KNOWN_MODES:
            mode="UNKNOWN_MODEL_MODE"
        tour=fx["tour"]
        draw=fx.get("draw") or "UNKNOWN_DRAW"
        status=fx.get("result_status","PENDING").upper()
        if status=="COMPLETED":
            if fx.get("winner_side") not in ("1","2"):
                raise ValueError("completed match missing verified winner")
            p=float(selected.get("p_player1","nan"))
            if not 0<p<1:
                raise ValueError("invalid original calibrated probability")
            chosen_first=p>=0.5
            outcome="won" if chosen_first==(fx["winner_side"]=="1") else "lost"
        elif status in VOID_STATES:
            outcome="void"
        elif status in ("PENDING","SCHEDULED","POSTPONED"):
            outcome="pending"
        else:
            raise ValueError("unrecognized result status")
        for count in (totals,by_tour[tour],by_mode[(tour,mode)],
                      by_day[day],by_day_group[(day,tour,draw,mode)]):
            count["picks"]+=1
            count[outcome]+=1
    result_days=[]
    today=as_of.astimezone(PHT).date()
    d=start_day
    while d<=end_day:
        entries=[{"tour":tour,"draw":draw,"mode":mode,**_finished(vals)}
                 for (dd,tour,draw,mode),vals in sorted(by_day_group.items()) if dd==d]
        day_totals=_finished(by_day[d])
        result_days.append({
            "day":d.isoformat(),**day_totals,
            "provisional":day_totals["pending"]>0 or d>=today,
            "groups":entries
        })
        d+=timedelta(days=1)
    return {
        "report":"FIRST_ISSUED_ATP_CH_DAILY_RESULTS_V1",
        "as_of":as_of_utc,"timezone":"Asia/Manila",
        "start_day":start_day.isoformat(),"end_day":end_day.isoformat(),
        "total":_finished(totals),
        "tour":[{"tour":tier,**_finished(by_tour[tier])} for tier in ("ATP_TOUR","CHALLENGER")],
        "tour_and_mode":[{"tour":tier,"mode":mode,**_finished(vals)}
                         for (tier,mode),vals in sorted(by_mode.items())],
        "days":result_days,
        "notes":"First-issued 30-minute-cutoff originals only; completed results graded, retired/walkover/cancelled void, unknown pending. No external competitor evaluated.",
    }


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures",required=True,type=Path)
    parser.add_argument("--forecasts",required=True,type=Path)
    parser.add_argument("--start-day",required=True,type=date.fromisoformat)
    parser.add_argument("--end-day",required=True,type=date.fromisoformat)
    parser.add_argument("--as-of-utc",required=True)
    args=parser.parse_args(argv)
    with args.fixtures.open(newline="",encoding="utf-8") as f:
        fixtures=list(csv.DictReader(f))
    with args.forecasts.open(newline="",encoding="utf-8") as f:
        forecasts=list(csv.DictReader(f))
    report=daily_report(fixtures,forecasts,start_day=args.start_day,
                        end_day=args.end_day,as_of_utc=args.as_of_utc)
    print("RESEARCH_DAILY_BASELINE "+json.dumps(report,sort_keys=True,separators=(",",":")),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
