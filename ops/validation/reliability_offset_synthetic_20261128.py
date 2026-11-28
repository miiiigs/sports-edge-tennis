"""Synthetic-only fixed-log-odds reliability comparator (separate V2 namespace).

The generator is deliberately explicit, with an injected signal and a null
control. Never load historical records or change any frozen production policy.
Scores concern correctness of the unchanged A1 selected side, not a new winner.
"""
from __future__ import annotations

import json
import math

ARMS = {"R0": (), "R1": (0,), "R2": (0,1,2), "R3": (0,1,2,3)}


def sigmoid(z):
    return 1/(1+math.exp(-z))


def logit(p):
    return math.log(p/(1-p))


def population(signal, seed=20261128, n=6000):
    state = seed & 0xffffffff
    def rand():
        nonlocal state
        state = (1664525*state+1013904223)&0xffffffff
        return state/4294967296
    rows=[]
    for i in range(n):
        p=.5+.45*rand()
        u,m,s,a=(int(rand()<v) for v in (.25,.18,.12,.22))
        z=logit(p)+(-.8*u-.65*m-.55*s-.6*a if signal else 0)
        rows.append({"id":i,"p":p,"x":[u,m,s,a],"y":int(rand()<sigmoid(z))})
    return rows


def design(row, idx, offset):
    return [1., *([] if offset else [logit(row["p"])]),
            *(row["x"][k] for k in idx)]


def fit(rows,idx,offset):
    width=len(idx)+(1 if offset else 2)
    w=[0.]*width
    # Fixed training schedule; no tuning or label use on calibration/test.
    for _ in range(240):
        g=[0.]*width
        for r in rows:
            x=design(r,idx,offset)
            z=(logit(r["p"]) if offset else 0)+sum(v*b for v,b in zip(x,w))
            e=sigmoid(z)-r["y"]
            for k,v in enumerate(x):
                g[k]+=e*v
        for k in range(width):
            w[k]-=.35*(g[k]/len(rows)+(4*w[k]/len(rows) if k else 0))
    return w


def scores(rows,idx,w,offset):
    return [{**r,"q":r["p"] if w is None else sigmoid(
        (logit(r["p"]) if offset else 0)+sum(v*b for v,b in zip(design(r,idx,offset),w)))}
        for r in rows]


def metrics(rows,threshold):
    n=len(rows)
    ranked=sorted(rows,key=lambda r:(-r["q"],r["id"]))[:n*45//100]
    fixed=[r for r in rows if r["q"]>=threshold]
    return {"brier":sum((r["q"]-r["y"])**2 for r in rows)/n,
        "log_loss":sum(-(r["y"]*math.log(r["q"])+(1-r["y"])*math.log(1-r["q"])) for r in rows)/n,
        "rank_n":len(ranked),"rank_w":sum(r["y"] for r in ranked),
        "fixed_n":len(fixed),"fixed_w":sum(r["y"] for r in fixed),"fixed_threshold":threshold}


def experiment():
    result={}
    for signal in (False,True):
        rows=population(signal)
        train,cal,test=rows[:3600],rows[3600:4800],rows[4800:]
        arms={}
        for name,idx in ARMS.items():
            for offset in ([True] if name=="R0" else [True,False]):
                w=None if name=="R0" else fit(train,idx,offset)
                cal_scores=scores(cal,idx,w,offset)
                threshold=sorted(cal_scores,key=lambda r:(-r["q"],r["id"]))[539]["q"]
                key=name if name=="R0" else name+("_OFFSET" if offset else "_FREE")
                arms[key]={**metrics(scores(test,idx,w,offset),threshold),"weights":w}
        result["injected_signal" if signal else "null_signal"]=arms
    return result


if __name__=="__main__":
    print(json.dumps(experiment(),indent=2,sort_keys=True))
