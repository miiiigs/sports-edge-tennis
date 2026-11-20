"""Read-only Elo+Markov blend shadow benchmark on authentic paired live forecasts.

Inspired by general probabilistic tennis modelling, NOT a reproduction of any
commercial competitor. Uses only our own first co-issued production A1/A2
probabilities and already-recorded point-in-time identity and result evidence.
Never modifies the Manager, market, settlement, live picks, or database.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.performance.calibration import brier, log_loss

PREMATCH_MINUTES = 30
VERSION = "A1_A2_PAIRED_ELO_MARKOV_BLEND_SHADOW_V1"


@dataclass(frozen=True)
class Pair:
    match_id: int
    issued_at: datetime
    start_at: datetime
    tour: str
    surface: str
    cluster_id: str
    a1_p1: float
    a2_p1: float
    winner_p1: int | None


def source_sql() -> str:
    return """
        WITH first_pair AS (
          SELECT DISTINCT ON (a1.match_id)
            a1.match_id, a1.as_of AS a1_as_of, a2.as_of AS a2_as_of,
            pr.prediction_finalized_at AS issued_at,
            a1.selection_side AS a1_side, a1.win_probability AS a1_probability,
            a2.selection_side AS a2_side, a2.win_probability AS a2_probability
          FROM predictions a1
          JOIN predictions a2
            ON a2.prediction_run_id = a1.prediction_run_id
           AND a2.match_id = a1.match_id
          JOIN prediction_runs pr ON pr.prediction_run_id = a1.prediction_run_id
          WHERE a1.agent_code = 'AGENT_1_SURFACE_ELO'
            AND a2.agent_code = 'AGENT_2_HIERARCHICAL_MARKOV'
            AND a1.prediction_status = 'PREDICTED'
            AND a2.prediction_status = 'PREDICTED'
            AND a1.selection_side IN (1,2)
            AND a2.selection_side IN (1,2)
            AND a1.win_probability IS NOT NULL
            AND a2.win_probability IS NOT NULL
            AND a1.experiment_id IS NULL AND a2.experiment_id IS NULL
            AND pr.experiment_id IS NULL
            AND pr.prediction_contract_version = 'predictive_agent_result_v1'
            AND pr.run_status = 'COMPLETED'
            AND pr.prediction_finalized_at <= %(as_of)s
          ORDER BY a1.match_id, pr.prediction_finalized_at,
                   a1.prediction_id, a2.prediction_id
        )
        SELECT
          fp.match_id, fp.a1_as_of, fp.a2_as_of, fp.issued_at,
          mv.match_start_at, mv.tour_scope::text,
          COALESCE(mv.surface::text, 'UNKNOWN'),
          m.tournament_edition_id,
          fp.a1_side, fp.a1_probability, fp.a2_side, fp.a2_probability,
          lr.outcome_status, lr.winner_side, lr.known_at
        FROM first_pair fp
        JOIN matches m ON m.match_id = fp.match_id
        LEFT JOIN LATERAL (
            SELECT match_start_at, tour_scope, surface
            FROM match_versions v
            WHERE v.match_id=fp.match_id
              AND v.as_of <= fp.a1_as_of
              AND v.source_observed_at <= fp.a1_as_of
              AND v.ingested_at <= fp.a1_as_of
            ORDER BY v.as_of DESC, v.source_observed_at DESC,
                     v.match_version_id DESC LIMIT 1
        ) mv ON TRUE
        LEFT JOIN LATERAL (
            SELECT outcome_status::text AS outcome_status, winner_side,
                   GREATEST(as_of, source_observed_at, ingested_at) AS known_at
            FROM match_result_observations mro
            WHERE mro.match_id=fp.match_id
              AND mro.as_of <= %(as_of)s
              AND mro.source_observed_at <= %(as_of)s
              AND mro.ingested_at <= %(as_of)s
            ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
                     mro.match_result_observation_id DESC LIMIT 1
        ) lr ON TRUE
        ORDER BY fp.match_id
    """


def normalize(rows: list[tuple], *, as_of: datetime) -> tuple[list[Pair], dict[str, int]]:
    rejected: Counter[str] = Counter()
    pairs: list[Pair] = []
    seen: set[int] = set()
    for (match_id, a1_time, a2_time, issued, start, tour, surface,
         edition_id, a1_side, a1_prob, a2_side, a2_prob,
         status, winner, result_known) in rows:
        mid = int(match_id)
        if mid in seen:
            raise ValueError("duplicate canonical paired fixture")
        seen.add(mid)
        if any(not isinstance(dt, datetime) or dt.tzinfo is None
               for dt in (a1_time, a2_time, issued, start)):
            rejected["MISSING_PIT_TIMESTAMPS"] += 1
            continue
        if (a1_time > issued or a2_time > issued or issued > as_of
            or issued > start - timedelta(minutes=PREMATCH_MINUTES)):
            rejected["LATE_OR_INVALID_PAIRED_ISSUANCE"] += 1
            continue
        if tour not in ("ATP_MAIN", "CHALLENGER"):
            rejected["OUT_OF_SCOPE_TOUR"] += 1
            continue
        if edition_id is None or a1_side not in (1,2) or a2_side not in (1,2):
            rejected["UNVERIFIABLE_PAIR_IDENTITY"] += 1
            continue
        if a1_prob is None or a2_prob is None:
            rejected["MISSING_PROBABILITY"] += 1
            continue
        p1, p2 = float(a1_prob), float(a2_prob)
        if not all(math.isfinite(p) and 0.5 <= p < 1 for p in (p1,p2)):
            rejected["INVALID_CONFIDENCE_PROBABILITY"] += 1
            continue
        target: int | None = None
        if status == "COMPLETED":
            if (winner not in (1,2) or not isinstance(result_known,datetime)
                or result_known.tzinfo is None or result_known > as_of
                or result_known < start):
                rejected["UNSAFE_RESULT_TIMELINE_OR_WINNER"] += 1
                continue
            target = int(winner == 1)
        # All non-COMPLETED results explicitly remain ungraded.
        pairs.append(Pair(
            match_id=mid, issued_at=issued, start_at=start,
            tour="ATP_TOUR" if tour=="ATP_MAIN" else "CHALLENGER",
            surface=str(surface), cluster_id=str(edition_id),
            a1_p1=p1 if a1_side == 1 else 1.0-p1,
            a2_p1=p2 if a2_side == 1 else 1.0-p2,
            winner_p1=target,
        ))
    return pairs, dict(sorted(rejected.items()))


def _logit(p: float) -> float:
    p = min(max(p, 1e-8), 1-1e-8)
    return math.log(p / (1-p))


def _sigmoid(x: float) -> float:
    return 1.0/(1.0 + math.exp(-x)) if x >= 0 else (
        math.exp(x)/(1.0+math.exp(x))
    )


def candidate_probabilities(row: Pair) -> dict[str, float]:
    a1, a2 = row.a1_p1, row.a2_p1
    return {
        "a1_surface_elo": a1,
        "a2_serve_return_markov": a2,
        "equal_linear_blend": 0.5*(a1+a2),
        "equal_log_odds_blend": _sigmoid(0.5*(_logit(a1)+_logit(a2))),
        "a1_weighted_log_odds_70_30": _sigmoid(0.7*_logit(a1)+0.3*_logit(a2)),
    }


def _stats(rows: list[tuple[float, int]]) -> dict[str, Any]:
    # An exact 50/50 forecast is a no-pick for hit rate; proper scores still
    # grade its honest 0.5 probability. No arbitrary player-1 tie-break.
    picks = [(p,y) for p,y in rows if abs(p-0.5)>1e-12]
    return {
        "scored_probabilities": len(rows),
        "winner_selection_coverage": len(picks)/len(rows) if rows else None,
        "winner_accuracy": (
            sum(int((p>0.5)==bool(y)) for p,y in picks)/len(picks) if picks else None
        ),
        "brier": brier(rows),
        "log_loss": log_loss(rows),
    }


def _cluster_ci(rows: list[tuple[str,float]], *, draws:int=500) -> list[float] | None:
    # This represents uncertainty *conditional on the recorded historical
    # cohort*. Repeated matches in a tournament edition form one cluster.
    groups: dict[str,list[float]] = defaultdict(list)
    for key,delta in rows:
        groups[key].append(delta)
    if len(groups)<4:
        return None
    keys=sorted(groups)
    rng=random.Random(20261120)
    vals=[]
    for _ in range(draws):
        diffs=[item for _ in keys for item in groups[rng.choice(keys)]]
        vals.append(sum(diffs)/len(diffs))
    vals.sort()
    return [vals[int((draws-1)*.025)],vals[int((draws-1)*.975)]]


def evaluate(pairs: list[Pair]) -> dict[str, Any]:
    dates = sorted({p.start_at.astimezone(UTC).date() for p in pairs})
    result: dict[str,Any]={
        "version":VERSION, "paired_pre_match_fixtures":len(pairs),
        "paired_unique_dates":len(dates),
        "research_only":True, "db_writes":False,
        "production_rules_modified":False,
        "competitor_vendor_data_imported":False,
        "scope":"our own A1 Elo and A2 Markov, earliest co-issued completed run",
    }
    if len(dates)<5:
        return {**result,"status":"INSUFFICIENT_DISTINCT_MATCH_DATES"}
    hold_start=dates[min(len(dates)-1,max(1,int(len(dates)*.8)))]
    hold=[r for r in pairs if r.start_at.astimezone(UTC).date()>=hold_start]
    scored=[r for r in hold if r.winner_p1 is not None]
    result.update({
        "holdout_start_utc":hold_start.isoformat(),
        "holdout_calendar_span_days":(
            max(r.start_at.date() for r in hold)-min(r.start_at.date() for r in hold)
        ).days+1,
        "holdout_paired_issued":len(hold),
        "holdout_paired_scored":len(scored),
        "holdout_pending_or_void":len(hold)-len(scored),
        "holdout_tours": {
            tour:sum(r.tour==tour for r in scored)
            for tour in ("ATP_TOUR","CHALLENGER")
        },
    })
    if len(scored)<20:
        return {**result,"status":"INSUFFICIENT_PAIRED_SETTLED_HOLDOUT"}
    data={name: [] for name in candidate_probabilities(scored[0])}
    per_tour={tour:{name:[] for name in data} for tour in ("ATP_TOUR","CHALLENGER")}
    delta={name:{"brier":[],"log_loss":[]} for name in data if name!="a1_surface_elo"}
    for row in scored:
        ps=candidate_probabilities(row)
        y=int(row.winner_p1)
        for name,p in ps.items():
            data[name].append((p,y))
            per_tour[row.tour][name].append((p,y))
        for name,p in ps.items():
            if name=="a1_surface_elo":
                continue
            a1=ps["a1_surface_elo"]
            delta[name]["brier"].append((row.cluster_id,(a1-y)**2-(p-y)**2))
            delta[name]["log_loss"].append((
                row.cluster_id,
                -(y*math.log(min(max(a1,1e-8),1-1e-8))+
                  (1-y)*math.log1p(-min(max(a1,1e-8),1-1e-8)))-
                (-(y*math.log(min(max(p,1e-8),1-1e-8))+
                   (1-y)*math.log1p(-min(max(p,1e-8),1-1e-8))))
            ))
    result["status"]="EVALUATED_HISTORICAL_PAIRED_SHADOW"
    result["holdout_models"]={name:_stats(values) for name,values in data.items()}
    result["per_tour"]={
        tour:{name:_stats(rows) for name,rows in model_data.items()}
        for tour,model_data in per_tour.items()
    }
    result["vs_a1_positive_means_lower_error"]={
        name:{
            "paired_brier_improvement":
                result["holdout_models"]["a1_surface_elo"]["brier"]-
                result["holdout_models"][name]["brier"],
            "paired_log_loss_improvement":
                result["holdout_models"]["a1_surface_elo"]["log_loss"]-
                result["holdout_models"][name]["log_loss"],
            "brier_cluster_ci":_cluster_ci(metrics["brier"]),
            "log_loss_cluster_ci":_cluster_ci(metrics["log_loss"]),
        }
        for name,metrics in delta.items()
    }
    result["release_eligible"]=False
    result["explanation"]=(
        "All candidate weights were fixed in advance, and no holdout scores "
        "were used to fit anything. Same earliest co-issued first pair for all "
        "candidates. Retrospective research only, not a licensed competitor "
        "head-to-head or evidence that Manager Model Picks improved."
    )
    return result


def run(conn:psycopg.Connection, *, as_of:datetime)->dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("explicit timezone required")
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(source_sql(),{"as_of":as_of}).fetchall()
    pairs,rejected=normalize(rows,as_of=as_of)
    result=evaluate(pairs)
    result["source_rows"]=len(rows)
    result["rejections"]=rejected
    result["as_of_utc"]=as_of.astimezone(UTC).isoformat().replace("+00:00","Z")
    return result


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-utc", required=True)
    args=parser.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        parser.error("as-of must be explicit UTC Z")
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        result=run(conn,as_of=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00")))
    print("A1_A2_PAIRED_RESEARCH "+json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
