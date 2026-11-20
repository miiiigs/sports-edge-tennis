"""Read-only leakage negative-control audit over genuine first-issued ATP/Ch A1/A2.

Controls: paired forecast chronology and uniqueness are inherited from the
existing first-coissued source; shuffle only *outcome labels*, never inputs,
with a fixed seed, preserving the observed P1 winner share within each tour.
A failed random-label control is a diagnostic and requires source inspection.
This is not sufficient alone to certify feature-level PIT safety.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import mean

import psycopg

from sports_edge_tennis.validation.a1_a2_blend_shadow import (
    Pair, normalize, source_sql,
)

AUDIT_VERSION = "TENNNIS_PIT_LABEL_SHUFFLE_NEGATIVE_CONTROL_V1"
DEFAULT_SEED = 20261120
DEFAULT_PERMUTATIONS = 1000


def _hit(rows: list[tuple[float, int]]) -> float | None:
    selected = [(p, label) for p, label in rows if abs(p - .5) > 1e-12]
    if not selected:
        return None
    return sum(int((p > .5) == bool(label)) for p, label in selected) / len(selected)


def audit(pairs: list[Pair], *, seed: int = DEFAULT_SEED,
          permutations: int = DEFAULT_PERMUTATIONS) -> dict:
    if permutations < 100 or permutations > 10000:
        raise ValueError("permutations must be 100..10000")
    if len({r.match_id for r in pairs}) != len(pairs):
        raise ValueError("duplicate canonical fixtures: audit blocked")
    graded = [r for r in pairs if r.winner_p1 in (0, 1)]
    digest = hashlib.sha256()
    for r in sorted(pairs, key=lambda p:p.match_id):
        digest.update((
            f"{r.match_id}|{r.issued_at.isoformat()}|"
            f"{r.start_at.isoformat()}|{r.tour}|{r.surface}|"
            f"{r.a1_p1:.12f}|{r.a2_p1:.12f}|{r.winner_p1}\n"
        ).encode("utf-8"))
    result = {
        "audit_version": AUDIT_VERSION,
        "seed": seed,
        "permutations": permutations,
        "sample_digest_sha256": digest.hexdigest(),
        "unique_prematch_pairs":len(pairs),
        "graded_pairs":len(graded),
        "ungraded_or_void":len(pairs)-len(graded),
        "track":"read_only_negative_control",
        "production_rules_modified":False,
        "db_writes":False,
    }
    if len(graded) < 40:
        return {**result,"status":"INSUFFICIENT_GRADED_EXAMPLES"}

    per_tour: dict[str, list[Pair]] = defaultdict(list)
    for r in graded:
        per_tour[r.tour].append(r)
    observed = {}
    rng = random.Random(seed)
    permuted_hits: dict[str,list[float]] = {
        "a1_surface_elo":[], "a2_markov":[], "equal_log_odds_blend":[]
    }
    def _sigmoid(t:float)->float:
        return 1/(1+math.exp(-t)) if t>=0 else math.exp(t)/(1+math.exp(t))
    def _blend(row: Pair)->float:
        def logit(p:float)->float:
            p=min(max(p,1e-8),1-1e-8)
            return math.log(p/(1-p))
        return _sigmoid((logit(row.a1_p1)+logit(row.a2_p1))/2)
    preds={
        "a1_surface_elo":lambda r:r.a1_p1,
        "a2_markov":lambda r:r.a2_p1,
        "equal_log_odds_blend":_blend,
    }
    for name,fn in preds.items():
        observed[name]=_hit([(fn(r),int(r.winner_p1)) for r in graded])

    # Permute labels only within a competition scope. This keeps player-slot
    # base-rate differences and ATP/Challenger mix intact, breaking the link
    # between a given match's model features and its future result.
    for _ in range(permutations):
        label_by_match={}
        for tour in sorted(per_tour):
            members=per_tour[tour]
            ys=[int(row.winner_p1) for row in members]
            rng.shuffle(ys)
            label_by_match.update(zip((row.match_id for row in members),ys))
        for name,fn in preds.items():
            score=_hit([(fn(r),label_by_match[r.match_id]) for r in graded])
            if score is not None:
                permuted_hits[name].append(score)
    comparison={}
    for name in preds:
        null=sorted(permuted_hits[name])
        if not null:
            comparison[name]={"status":"NO_VALID_NULL_DRAWS"}
            continue
        actual=observed[name]
        comparison[name]={
            "observed_winner_accuracy":actual,
            "shuffled_label_mean_accuracy":mean(null),
            "shuffled_label_95pct_interval":[
                null[int((len(null)-1)*.025)],null[int((len(null)-1)*.975)]
            ],
            "observed_minus_shuffled_mean":actual-mean(null) if actual is not None else None,
            "empirical_one_sided_p_value":(
                (1+sum(s>=actual for s in null))/(len(null)+1)
                if actual is not None else None
            ),
        }
    tour_stats={}
    for tour,rr in sorted(per_tour.items()):
        tour_stats[tour]={
            "graded":len(rr),
            "winner_is_player_1_rate":mean(int(r.winner_p1) for r in rr),
            "a1_selects_player_1_rate":mean(int(r.a1_p1>.5) for r in rr),
            "a2_selects_player_1_rate":mean(int(r.a2_p1>.5) for r in rr),
        }
    return {
        **result,
        "status":"NEGATIVE_CONTROL_EVALUATED_NOT_FULL_LEAKAGE_CERTIFICATION",
        "per_tour_player_order_diagnostics":tour_stats,
        "models":comparison,
        "interpretation": (
            "Shuffle within tour retains Player-1 win prevalence while destroying"
            " predictive feature/label linkage. If original and permuted accuracy"
            " are similar, inspect data leakage, input orientation and model signal."
            " Even a small p-value does not rule out future feature leakage;"
            " swapping players in the full agents and reviewing PIT feature joins"
            " remain mandatory."
        ),
    }


def run(conn:psycopg.Connection, *, as_of:datetime,
        permutations:int=DEFAULT_PERMUTATIONS, seed:int=DEFAULT_SEED)->dict:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(source_sql(),{"as_of":as_of}).fetchall()
    pairs,rejected=normalize(rows,as_of=as_of)
    result=audit(pairs,permutations=permutations,seed=seed)
    return {
        **result,"as_of_utc":as_of.astimezone(UTC).isoformat(),
        "source_rows":len(rows),"rejections":rejected,
    }


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-utc",required=True)
    parser.add_argument("--permutations",type=int,default=DEFAULT_PERMUTATIONS)
    parser.add_argument("--seed",type=int,default=DEFAULT_SEED)
    args=parser.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        parser.error("as-of requires UTC Z")
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        res=run(
            conn,as_of=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00")),
            permutations=args.permutations,seed=args.seed,
        )
    print("TENNIS_LEAKAGE_NEGATIVE_CONTROL "+json.dumps(res,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
