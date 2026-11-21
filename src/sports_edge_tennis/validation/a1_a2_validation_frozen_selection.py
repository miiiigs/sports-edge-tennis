"""Validation-frozen selective A1/A2 research on original first co-issued PIT forecasts.

Cutoffs are determined by the observed confidence distribution in an earlier
chronological validation window only. Later forecast labels and confidence
distributions cannot alter a cutoff. This is STILL exploratory: historical
holdout windows were previously examined, candidate approaches are known, and
the denominator covers only co-issued A1/A2 forecasts, not all eligible
ATP/Challenger fixtures or production-published Model Picks.

No bookmaker inputs, vendor data, new model training, writes or live effects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.validation.a1_a2_blend_shadow import (
    PREMATCH_MINUTES, Pair, _cluster_ci, _stats, candidate_probabilities, normalize, source_sql,
)
from sports_edge_tennis.validation.a1_a2_weight_selection_shadow import ObservedPair

VERSION = "VALIDATION_FROZEN_A1A2_RISK_COVERAGE_SHADOW_V1"
FRACTIONS = (.25, .40, .50, .75, 1.00)
MODELS = ("a1_surface_elo", "a2_serve_return_markov", "equal_log_odds_blend")
MIN_VALIDATION_SETTLED = 80
MIN_HOLDOUT_SETTLED = 100


def _predict(row: Pair, model: str) -> float:
    p = candidate_probabilities(row)[model]
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("nonfinite or invalid original forecast probability")
    return p


def _confidence(row: Pair, model: str) -> float | None:
    p = _predict(row, model)
    return max(p, 1.0-p) if abs(p-0.5)>1e-12 else None


def _freeze_cutoff(rows: list[ObservedPair], model: str, fraction: float) -> float | None:
    if not 0 < fraction <= 1:
        raise ValueError("predeclared fraction must lie within (0,1]")
    ordered = sorted(
        (v for o in rows if (v := _confidence(o.pair, model)) is not None),
        reverse=True,
    )
    if not ordered:
        return None
    return ordered[min(len(ordered)-1,math.ceil(fraction*len(ordered))-1)]


def _score_selected(
    rows: list[ObservedPair], model: str, cutoff: float | None
) -> tuple[dict[str,Any], list[ObservedPair]]:
    if cutoff is None:
        selected = []
    else:
        selected = [
            o for o in rows
            if (v := _confidence(o.pair, model)) is not None
            and v >= cutoff - 1e-12
        ]
    scored = [o for o in selected if o.pair.winner_p1 in (0,1)]
    p_y = [(_predict(o.pair,model),int(o.pair.winner_p1)) for o in scored]
    s = _stats(p_y)
    winners = sum(
        (p>0.5) == bool(y) for p,y in p_y
    )
    return {
        "cutoff_frozen_from_validation":cutoff,
        "issued_selected":len(selected),
        "settled_selected":len(scored),
        "ungraded_selected":len(selected)-len(scored),
        "wins":winners,
        "losses":len(scored)-winners,
        "issued_paired_cohort_coverage":len(selected)/len(rows) if rows else None,
        "full_eligible_fixture_coverage":None,
        "average_selected_confidence":(
            sum(max(p,1-p) for p,_ in p_y)/len(p_y) if p_y else None
        ),
        **s,
    }, selected


def _paired_selected(a:list[ObservedPair],b:list[ObservedPair])->dict[str,Any]:
    a_map={r.pair.match_id:r.pair for r in a if r.pair.winner_p1 in (0,1)}
    b_map={r.pair.match_id:r.pair for r in b if r.pair.winner_p1 in (0,1)}
    mids=sorted(set(a_map)&set(b_map))
    diffs=[]
    a_win=b_win=0
    for mid in mids:
        r=a_map[mid]
        if r.winner_p1 != b_map[mid].winner_p1:
            raise ValueError("winner conflict in same canonical selected fixture")
        y=int(r.winner_p1)
        p1=_predict(r,"a1_surface_elo")
        pb=_predict(r,"equal_log_odds_blend")
        a_win+=int((p1>.5)==bool(y))
        b_win+=int((pb>.5)==bool(y))
        diffs.append((r.cluster_id,(p1-y)**2-(pb-y)**2))
    return {
        "same_selected_settled_matches":len(mids),
        "a1_wins":a_win,
        "blend_wins":b_win,
        "a1_minus_blend_brier_improvement":(
            sum(v for _,v in diffs)/len(diffs) if diffs else None
        ),
        "brier_cluster_ci_95pct":_cluster_ci(diffs),
        "not_prospective":True,
    }


def evaluate(observations:list[ObservedPair],*,as_of:datetime)->dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("explicit as-of timezone required")
    if len({o.pair.match_id for o in observations})!=len(observations):
        raise ValueError("duplicate canonical match in original cohort")
    for obs in observations:
        row=obs.pair
        if row.issued_at.tzinfo is None or row.start_at.tzinfo is None:
            raise ValueError("naive first issued or match timestamp")
        if (row.issued_at > as_of
            or row.issued_at > row.start_at-timedelta(minutes=PREMATCH_MINUTES)):
            raise ValueError("original prediction too late for frozen PIT forecast")
        if obs.result_known_at is not None:
            if (obs.result_known_at.tzinfo is None
                or obs.result_known_at > as_of
                or obs.result_known_at < row.start_at):
                raise ValueError("unsafe result visibility timestamp")
        if row.winner_p1 not in (None,0,1):
            raise ValueError("invalid winner label")
        if row.winner_p1 in (0,1) and obs.result_known_at is None:
            raise ValueError("settled prediction requires known-at timestamp")
        for model in MODELS:
            _predict(row,model)

    dates=sorted({o.pair.start_at.astimezone(UTC).date() for o in observations})
    sha=hashlib.sha256()
    for o in sorted(observations,key=lambda a:a.pair.match_id):
        r=o.pair
        sha.update((
            f"{r.match_id}|{r.issued_at.isoformat()}|{r.start_at.isoformat()}|"
            f"{r.a1_p1:.12f}|{r.a2_p1:.12f}|{r.winner_p1}|"
            f"{o.result_known_at.isoformat() if o.result_known_at else '-'}\n"
        ).encode())
    base={
        "version":VERSION,
        "status":"INSUFFICIENT_CHRONOLOGICAL_MATCH_DATES",
        "research_only":True,
        "production_changed":False,
        "db_writes":False,
        "untouched_independent_holdout":False,
        "thresholds_derived_without_future_holdout_data":False,
        "full_eligible_match_coverage_verified":False,
        "dataset_sha256":sha.hexdigest(),
        "first_coissued_paired_originals":len(observations),
        "as_of_utc":as_of.astimezone(UTC).isoformat(),
        "fractions":list(FRACTIONS),
    }
    if len(dates)<16:
        return base
    val_start=dates[int(.50*len(dates))]
    hold_start=dates[int(.75*len(dates))]
    hold_boundary=datetime.combine(hold_start,datetime.min.time(),tzinfo=UTC)
    val=[
        o for o in observations
        if (val_start <= o.pair.start_at.astimezone(UTC).date()<hold_start)
        and o.pair.winner_p1 in (0,1)
        and o.result_known_at is not None
        and o.result_known_at < hold_boundary
    ]
    hold=[
        o for o in observations
        if o.pair.start_at.astimezone(UTC).date()>=hold_start
    ]
    hold_scored=[
        o for o in hold
        if o.pair.winner_p1 in (0,1)
        and o.result_known_at is not None
        and o.result_known_at<=as_of
    ]
    base.update({
        "validation_start_date":val_start.isoformat(),
        "holdout_start_date":hold_start.isoformat(),
        "validation_outcomes_known_before_holdout":len(val),
        "holdout_issued":len(hold),
        "holdout_settled":len(hold_scored),
        "holdout_ungraded":len(hold)-len(hold_scored),
        "validation_result_fence_utc":hold_boundary.isoformat(),
    })
    if len(val)<MIN_VALIDATION_SETTLED or len(hold_scored)<MIN_HOLDOUT_SETTLED:
        return {**base,"status":"INSUFFICIENT_STRICT_VALIDATION_OR_HOLDOUT"}
    curves={}
    for fraction in FRACTIONS:
        metric={}
        selection={}
        for model in MODELS:
            cutoff=_freeze_cutoff(val,model,fraction)
            counts, selected=_score_selected(hold,model,cutoff)
            metric[model]=counts
            selection[model]=selected
        curves[str(fraction)]={
            "models":metric,
            "a1_vs_blend_identical_selected_matches":_paired_selected(
                selection["a1_surface_elo"],selection["equal_log_odds_blend"]
            ),
        }
    return {
        **base,
        "status":"VALIDATION_FROZEN_SHADOW_HISTORICAL_HOLDOUT",
        "thresholds_derived_without_future_holdout_data":True,
        "holdout_full_models":{
            m:_stats([(_predict(o.pair,m),int(o.pair.winner_p1)) for o in hold_scored])
            for m in MODELS
        },
        "per_tour_settled":{
            tour:sum(o.pair.tour==tour for o in hold_scored)
            for tour in ("ATP_TOUR","CHALLENGER")
        },
        "validation_frozen_curves":curves,
        "release_eligible":False,
        "interpretation":(
            "Validation cutoffs use earlier original forecasts whose results "
            "were observed before the holdout boundary. Later forecasts are "
            "screened without using their outcomes or their confidence "
            "distribution to select thresholds. Nevertheless this historical "
            "holdout was previously inspected in related A1/A2 experiments, "
            "and coverage is within co-issued A1/A2 forecasts only. No "
            "generalization to all eligible fixtures, live Manager, bookmaker "
            "odds or an untouched prospective holdout."
        ),
    }


def run(conn:psycopg.Connection,*,as_of:datetime)->dict[str,Any]:
    conn.execute("SET default_transaction_read_only=on")
    raw=conn.execute(source_sql(),{"as_of":as_of}).fetchall()
    pairs,rejections=normalize(raw,as_of=as_of)
    known={}
    for row in raw:
        mid=int(row[0])
        if mid in known:
            raise ValueError("duplicate source canonical match")
        known[mid]=row[14] if row[12]=="COMPLETED" else None
    report=evaluate([
        ObservedPair(pair=p,result_known_at=known[p.match_id]) for p in pairs
    ],as_of=as_of)
    return {**report,"source_rows":len(raw),"source_rejections":rejections}


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-utc",required=True)
    args=parser.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        parser.error("as-of-utc must be explicit UTC with Z suffix")
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        result=run(
            conn,as_of=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00"))
        )
    print("A1_A2_VALIDATION_FROZEN_SELECTION "+json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
