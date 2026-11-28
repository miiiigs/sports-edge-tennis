"""Reproduce the no-fit corrected-A1 reliability transfer from frozen exports."""
import csv
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2] / "docs/research/evidence"


def read(name):
    with (ROOT/name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load():
    full = {r["match_id"]:r for r in read("a1_selective_population_20261126.csv")}
    rows = []
    for r in read("a1_reliability_scores_20261127.csv"):
        y = full[r["match_id"]]
        if r["prediction_id"] != y["prediction_id"] or datetime.fromisoformat(y["as_of"]) >= datetime.fromisoformat(y["match_start_at"]):
            raise ValueError("join or cutoff mismatch")
        risk = float(r["risk_points"])
        p0,p1 = float(y["p1_original"]),float(y["p1_corrected"])
        if abs(max(.5,max(p0,1-p0)-.005*risk)-float(r["selection_score"])) > 1e-12:
            raise ValueError("original reliability score differs")
        rows.append({"id":int(y["prediction_id"]),"match_id":int(y["match_id"]),
                     "date":y["date"],"tour":y["tour"],"confidence":float(y["confidence_corrected"]),
                     "score":max(.5,max(p1,1-p1)-.005*risk),"correct":int(y["correct_corrected"])})
    if len(rows)!=3202 or len({r["id"] for r in rows})!=len(rows):
        raise ValueError("population mismatch")
    return rows


def rank(rows,key):
    return sorted(rows,key=lambda r:(-r[key],r["id"],r["match_id"]))


def stats(rows):
    w=sum(r["correct"] for r in rows)
    return {"n":len(rows),"w":w,"accuracy":w/len(rows) if rows else None}


def compare(a,b):
    ai,bi=({r["id"] for r in x} for x in (a,b))
    removed=[r for r in a if r["id"] not in bi]
    added=[r for r in b if r["id"] not in ai]
    return {"baseline":stats(a),"candidate":stats(b),"retained":len(a)-len(removed),
            "removed":stats(removed),"added":stats(added),"net_wins":stats(b)["w"]-stats(a)["w"]}


def evaluate(rows):
    dev=[r for r in rows if r["date"]<"2026-11-01"]
    nov=[r for r in rows if r["date"]>="2026-11-01"]
    points,nov_points,thresholds={},{},{}
    for percent in (40,45,50):
        key=f"{percent/100:.2f}"
        n,nd,nn=(len(x)*percent//100 for x in (rows,dev,nov))
        points[key]=compare(rank(rows,"confidence")[:n],rank(rows,"score")[:n])
        nov_points[key]=compare(rank(nov,"confidence")[:nn],rank(nov,"score")[:nn])
        t0=rank(dev,"confidence")[nd-1]["confidence"]
        t1=rank(dev,"score")[nd-1]["score"]
        a=[r for r in nov if r["confidence"]>=t0-1e-12]
        b=[r for r in nov if r["score"]>=t1-1e-12]
        thresholds[key]={"baseline_threshold":t0,"reliability_threshold":t1,
            "dev_n":len(dev),"nov_n":len(nov),"comparison":compare(a,b),
            "baseline_coverage":len(a)/len(nov),"reliability_coverage":len(b)/len(nov)}
    return {"equal_coverage":points,"november_equal_coverage":nov_points,
            "development_threshold_november":thresholds}


if __name__=="__main__":
    print(json.dumps(evaluate(load()),indent=2,sort_keys=True))
