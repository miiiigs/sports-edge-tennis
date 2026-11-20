"""Exploratory chronological A1/A2 log-odds weight selection on original PIT pairs.

All candidate weights are frozen; selection uses only prior validation outcomes
that were already recorded before the holdout period. The later historical
holdout has been studied previously, so this is NOT independent external or
prospective proof. No commercial competitor outputs; no prediction writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from sports_edge_tennis.validation.a1_a2_blend_shadow import (
    Pair, _cluster_ci, _logit, _sigmoid, _stats, normalize, source_sql
)

VERSION = "PIT_A1_A2_CHRONOLOGICAL_WEIGHT_SEARCH_V1"
# Original equal blend is first for tie-breaking. No post-hoc search additions.
WEIGHTS = (0.50, 0.75, 0.25, 1.00, 0.00)
PREVIOUSLY_INSPECTED_HISTORICAL_WINDOW = True


@dataclass(frozen=True)
class ObservedPair:
    pair: Pair
    result_known_at: datetime | None


def _weighted(row: Pair, a1_share: float) -> float:
    if a1_share not in WEIGHTS:
        raise ValueError("weight outside preregistered search")
    return _sigmoid(
        a1_share * _logit(row.a1_p1) +
        (1.0-a1_share) * _logit(row.a2_p1)
    )


def _score(rows: list[ObservedPair], weight: float) -> dict[str, Any]:
    return _stats([
        (_weighted(item.pair, weight), int(item.pair.winner_p1))
        for item in rows if item.pair.winner_p1 is not None
    ])


def _selective(rows: list[ObservedPair], weight: float) -> dict[str, Any]:
    all_scored = [item for item in rows if item.pair.winner_p1 is not None]
    result={}
    for conf in (0.60, 0.65, 0.70):
        picks=[
            item for item in all_scored
            if max(_weighted(item.pair,weight),
                   1.0-_weighted(item.pair,weight)) >= conf
            and abs(_weighted(item.pair,weight)-0.5)>1e-12
        ]
        n=len(picks)
        win=sum(int((_weighted(item.pair,weight)>0.5) ==
                    bool(item.pair.winner_p1)) for item in picks)
        result[str(conf)]={
            "selections":n,"wins":win,"losses":n-win,
            "coverage":n/len(all_scored) if all_scored else None,
            "accuracy":win/n if n else None,
            "min_40pct_coverage":bool(all_scored and n/len(all_scored)>=.40),
        }
    return result


def evaluate(observations: list[ObservedPair], *, as_of: datetime) -> dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("as-of time must be aware")
    pairs=[o.pair for o in observations]
    if len({r.match_id for r in pairs}) != len(pairs):
        raise ValueError("duplicate match in first co-issued population")
    dates=sorted({p.start_at.astimezone(UTC).date() for p in pairs})
    h=hashlib.sha256()
    for o in sorted(observations,key=lambda a:a.pair.match_id):
        p=o.pair
        h.update((
            str(p.match_id)+"|"+p.issued_at.isoformat()+"|"+
            p.start_at.isoformat()+"|"+format(p.a1_p1,".12f")+"|"+
            format(p.a2_p1,".12f")+"|"+str(p.winner_p1)+"|"+
            (o.result_known_at.isoformat() if o.result_known_at else "-")+
            "\n"
        ).encode())
    base={
        "version":VERSION,"research_only":True,"db_writes":False,
        "live_model_changed":False,"winner_selection_changed":False,
        "competitor_data_used":False,
        "historical_holdout_previously_inspected":PREVIOUSLY_INSPECTED_HISTORICAL_WINDOW,
        "as_of_utc":as_of.astimezone(UTC).isoformat(),
        "candidate_weights":list(WEIGHTS),
        "first_coissued_pairs":len(pairs),"distinct_dates":len(dates),
        "dataset_sha256":h.hexdigest(),
    }
    if len(dates)<10:
        return {**base,"status":"INSUFFICIENT_DATES"}
    val_start=dates[int(.6*len(dates))]
    hold_start=dates[int(.8*len(dates))]
    boundary=datetime.combine(hold_start,datetime.min.time(),tzinfo=UTC)
    val=[
        o for o in observations if o.pair.winner_p1 is not None
        and val_start<=o.pair.start_at.astimezone(UTC).date()<hold_start
        and o.result_known_at is not None and o.result_known_at<boundary
    ]
    hold=[
        o for o in observations if o.pair.winner_p1 is not None
        and o.pair.start_at.astimezone(UTC).date()>=hold_start
        and o.result_known_at is not None and o.result_known_at<=as_of
    ]
    base["splits"]={
        "validation_start":val_start.isoformat(),
        "holdout_start":hold_start.isoformat(),
        "validation_pre_holdout_settled":len(val),
        "holdout_settled":len(hold),
        "holdout_unique_dates":len({o.pair.start_at.date() for o in hold})
    }
    if len(val)<60 or len(hold)<100:
        return {**base,"status":"INSUFFICIENT_STRICT_VALIDATION_OR_HOLDOUT"}
    validation={str(w):_score(val,w) for w in WEIGHTS}
    # Brier is proper and preferred over the unstable optimization of hit-rate.
    chosen=min(WEIGHTS,key=lambda w:(
        validation[str(w)]["brier"],
        validation[str(w)]["log_loss"],
        WEIGHTS.index(w),
    ))
    holdout={str(w):_score(hold,w) for w in WEIGHTS}
    baseline=holdout[str(.50)]
    selected=holdout[str(chosen)]
    diffs=[]
    for o in hold:
        y=int(o.pair.winner_p1)
        standard=_weighted(o.pair,.50)
        alt=_weighted(o.pair,chosen)
        diffs.append((o.pair.cluster_id,(standard-y)**2-(alt-y)**2))
    by_tour={
        tour:{
            "n":sum(o.pair.tour==tour for o in hold),
            "fixed_half":_score([o for o in hold if o.pair.tour==tour],.50),
            "chosen":_score([o for o in hold if o.pair.tour==tour],chosen),
        } for tour in ("ATP_TOUR","CHALLENGER")
    }
    return {
        **base,"status":"EXPLORATORY_HISTORICAL_HOLDOUT_SCORED",
        "validation":validation,
        "chosen_weight_a1":chosen,
        "holdout_models":holdout,
        "selected_vs_fixed50": {
            "brier_improvement":baseline["brier"]-selected["brier"],
            "log_loss_improvement":baseline["log_loss"]-selected["log_loss"],
            "winner_accuracy_difference":(
                selected["winner_accuracy"]-baseline["winner_accuracy"]
                if selected["winner_accuracy"] is not None
                and baseline["winner_accuracy"] is not None else None
            ),
            "winner_coverage_difference":(
                selected["winner_selection_coverage"]-
                baseline["winner_selection_coverage"]
            ),
            "brier_cluster_ci_95pct":_cluster_ci(diffs),
        },
        "holdout_selective_fixed_half":_selective(hold,.50),
        "holdout_selective_chosen":_selective(hold,chosen),
        "per_tour":by_tour,"release_eligible":False,
        "interpretation":(
            "Weights selected only using already settled historical validation "
            "before holdout boundary. The historical holdout was previously "
            "examined for other A1/A2 model research, so results are "
            "exploratory, not an independent preregistered validation. "
            "Report full base accuracy, coverage, proper scores and missed "
            "winners; do not deploy based on post-hoc model search."
        ),
    }


def run(conn:psycopg.Connection, *, as_of:datetime)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    raw=conn.execute(source_sql(),{"as_of":as_of}).fetchall()
    pairs,rejections=normalize(raw,as_of=as_of)
    known={}
    for row in raw:
        mid=int(row[0])
        # Last column from original co-issued PIT query is the first
        # authoritative terminal-result visibility timestamp.
        if mid in known:
            raise ValueError("duplicate canonical match in source")
        known[mid]=row[14] if row[12]=="COMPLETED" else None
    observed=[ObservedPair(pair=p,result_known_at=known[p.match_id]) for p in pairs]
    return {
        **evaluate(observed,as_of=as_of),
        "source_rows":len(raw),"rejections":rejections,
    }


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-utc",required=True)
    args=parser.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        parser.error("--as-of-utc must be explicit UTC Z")
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    as_of=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00"))
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        report=run(conn,as_of=as_of)
    print("A1_A2_WEIGHT_SEARCH "+json.dumps(report,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
