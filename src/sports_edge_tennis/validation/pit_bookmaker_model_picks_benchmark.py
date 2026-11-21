"""Read-only, publication-cutoff bookmaker baseline for original Model Picks.

Only quotes *already ingested by* first-issued Model Pick time are legal.
Both sides must be same bookmaker, canonical fixture and <=5 min apart.
No closing prices, retroactive quote reconstruction, API spend or production
prediction-policy changes. This is a matched original-pick study, NOT a
market benchmark over the full 134k canonical sporting fixtures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)

VERSION = "MODEL_PICKS_PIT_MARKET_TWO_WAY_BENCHMARK_V1"
MAX_QUOTE_AGE_HOURS = 6
MAX_SIDE_SPAN_SECONDS = 300
REQUIRED_PREMATCH_MINUTES = 30
RESEARCH_SEED = 20261121
COVERAGE_TARGETS = (1.0, .75, .50, .40, .25)


@dataclass(frozen=True)
class MarketPair:
    match_id: int
    edition_id: int
    run_id: int
    issued_at: datetime
    start_at: datetime
    tour: str
    surface: str
    pick_type: str
    picked_p1: float
    winner_p1: int | None
    book_source: str
    bookmaker: str
    odds1: float
    odds2: float
    odds_observed_at: datetime
    odds_side_span_sec: float
    a1_p1: float | None
    a2_p1: float | None

    @property
    def market_p1(self)->float:
        inverse1,inverse2=1/self.odds1,1/self.odds2
        return inverse1/(inverse1+inverse2)

    @property
    def favourite_confidence(self)->float:
        return max(self.market_p1,1-self.market_p1)


def source_sql()->str:
    return f"""
        WITH {aligned_production_model_picks_cte()},
        picks AS (
          SELECT p.match_id,p.prediction_run_id,p.picked_at,
                 p.selected_side,p.aligned_probability,p.aligned_pick_type,
                 m.tournament_edition_id,
                 mv.tour_scope::text AS tour_scope,
                 COALESCE(mv.surface::text,'UNKNOWN') AS surface,
                 mv.match_start_at,mv.player_1_id,mv.player_2_id,
                 EXISTS (
                   SELECT 1 FROM match_versions prior
                   WHERE prior.match_id=p.match_id
                     AND prior.as_of<=p.picked_at
                     AND prior.source_observed_at<=p.picked_at
                     AND prior.ingested_at<=p.picked_at
                     AND (prior.player_1_id!=mv.player_1_id
                          OR prior.player_2_id!=mv.player_2_id)
                 ) AS player_order_conflict,
                 lr.outcome_status,lr.winner_side,lr.known_at,
                 a1.selection_side,a1.win_probability,
                 a2.selection_side,a2.win_probability
          FROM aligned_model_picks p
          JOIN matches m ON m.match_id=p.match_id
          LEFT JOIN LATERAL (
             SELECT tour_scope,surface,match_start_at,
                    player_1_id,player_2_id
             FROM match_versions v
             WHERE v.match_id=p.match_id
               AND v.as_of<=p.picked_at
               AND v.source_observed_at<=p.picked_at
               AND v.ingested_at<=p.picked_at
             ORDER BY v.as_of DESC,v.source_observed_at DESC,
                      v.match_version_id DESC LIMIT 1
          ) mv ON TRUE
          LEFT JOIN LATERAL (
             SELECT outcome_status::text AS outcome_status,winner_side,
                    GREATEST(as_of,source_observed_at,ingested_at) AS known_at
             FROM match_result_observations ro
             WHERE ro.match_id=p.match_id
               AND ro.as_of<=%(as_of)s
               AND ro.source_observed_at<=%(as_of)s
               AND ro.ingested_at<=%(as_of)s
             ORDER BY as_of DESC,source_observed_at DESC,
                      match_result_observation_id DESC LIMIT 1
          ) lr ON TRUE
          LEFT JOIN predictions a1 ON a1.match_id=p.match_id
             AND a1.prediction_run_id=p.prediction_run_id
             AND a1.agent_code='AGENT_1_SURFACE_ELO'
             AND a1.prediction_status='PREDICTED'
          LEFT JOIN predictions a2 ON a2.match_id=p.match_id
             AND a2.prediction_run_id=p.prediction_run_id
             AND a2.agent_code='AGENT_2_HIERARCHICAL_MARKOV'
             AND a2.prediction_status='PREDICTED'
          WHERE p.picked_at<=%(as_of)s
        )
        SELECT p.*,
               q.book_source,q.bookmaker,q.odds1,q.odds2,
               q.last_observed_at,q.side_span_sec
        FROM picks p
        LEFT JOIN LATERAL (
           WITH latest_side AS (
             SELECT DISTINCT ON (
                 o.source_system,o.bookmaker_code,o.selection_type
             ) o.source_system,o.bookmaker_code,
               o.selection_type::text AS side,
               o.decimal_price,o.market_status::text AS status,
               o.source_observed_at
             FROM odds_snapshots o
             WHERE o.match_id=p.match_id
               AND o.market_type='MATCH_WINNER'
               AND o.selection_type IN ('PLAYER_1','PLAYER_2')
               AND o.source_observed_at<=p.picked_at
               AND o.as_of<=p.picked_at
               AND o.ingested_at<=p.picked_at
               AND o.source_observed_at >=
                   p.picked_at - interval '6 hours'
             ORDER BY o.source_system,o.bookmaker_code,o.selection_type,
                      o.source_observed_at DESC,o.as_of DESC,
                      o.odds_snapshot_id DESC
           ),
           paired AS (
             SELECT source_system AS book_source,
                    bookmaker_code AS bookmaker,
                    max(decimal_price) FILTER (WHERE side='PLAYER_1')
                        AS odds1,
                    max(decimal_price) FILTER (WHERE side='PLAYER_2')
                        AS odds2,
                    max(source_observed_at) AS last_observed_at,
                    extract(epoch from
                       max(source_observed_at)-min(source_observed_at))
                       AS side_span_sec
             FROM latest_side
             GROUP BY source_system,bookmaker_code
             HAVING count(*)=2
                AND count(*) FILTER (WHERE status='OPEN')=2
                AND max(source_observed_at)-min(source_observed_at)
                   <= interval '5 minutes'
           )
           SELECT * FROM paired
           ORDER BY CASE WHEN book_source='SHARP'
                              AND bookmaker='PINNACLE' THEN 0
                         WHEN bookmaker='PINNACLE' THEN 1
                         WHEN bookmaker='1XBET' THEN 2
                         WHEN bookmaker='BET365' THEN 3
                         ELSE 4 END,
                    last_observed_at DESC,book_source,bookmaker
           LIMIT 1
        ) q ON TRUE
        ORDER BY p.match_id
    """


def _to_p1(side:Any,confidence:Any)->float|None:
    if side not in (1,2) or confidence is None:
        return None
    p=float(confidence)
    if not math.isfinite(p) or not .5<=p<1:
        return None
    return p if side==1 else 1-p


def normalize(rows:list[tuple], *, as_of:datetime
              )->tuple[list[MarketPair],dict[str,int],dict[str,int]]:
    issues=Counter()
    summary=Counter()
    pairs=[]
    seen=set()
    for row in rows:
        (mid,run_id,issued,pick_side,pick_prob,pick_type,edition,
         tour,surface,start,player1,player2,order_conflict,
         status,winner,known,a1_side,a1_prob,a2_side,a2_prob,
         book_source,bookmaker,odds1,odds2,odds_at,span)=row
        mid=int(mid)
        if mid in seen:
            raise ValueError("duplicate first-issued Model Pick for canonical match")
        seen.add(mid)
        summary["first_issued_model_picks"]+=1
        if any(not isinstance(t,datetime) or t.tzinfo is None
               for t in (issued,start)):
            issues["MISSING_PIT_MATCH_OR_ISSUANCE_TIME"]+=1
            continue
        if issued>as_of or issued>start-timedelta(minutes=REQUIRED_PREMATCH_MINUTES):
            issues["LATE_MODEL_PICK"]+=1
            continue
        if tour not in ("ATP_MAIN","CHALLENGER") or edition is None:
            issues["OUT_OF_SCOPE_OR_MISSING_EDITION"]+=1
            continue
        if order_conflict or player1 is None or player2 is None:
            issues["HISTORIC_PLAYER_ORDER_CONFLICT"]+=1
            continue
        p1=_to_p1(pick_side,pick_prob)
        if p1 is None:
            issues["INVALID_ORIGINAL_MODEL_PROBABILITY"]+=1
            continue
        summary["pit_eligible_model_picks"]+=1
        if status=="COMPLETED":
            if (winner not in (1,2)
                or not isinstance(known,datetime) or known.tzinfo is None
                or known>as_of or known<start):
                issues["UNSAFE_COMPLETED_RESULT"]+=1
                continue
            summary["settled_pit_picks"]+=1
            y=int(winner==1)
        else:
            summary["pending_or_void_pit_picks"]+=1
            y=None
        if (odds1 is None or odds2 is None or odds_at is None):
            issues["NO_TIMELY_TWO_SIDED_SAME_BOOK_QUOTE"]+=1
            continue
        if (not isinstance(odds_at,datetime) or odds_at.tzinfo is None
            or odds_at>issued or span is None
            or not 0<=float(span)<=MAX_SIDE_SPAN_SECONDS
            or issued-odds_at>timedelta(hours=MAX_QUOTE_AGE_HOURS)):
            issues["INVALID_PAIR_TIMESTAMP_OR_STALENESS"]+=1
            continue
        o1,o2=float(odds1),float(odds2)
        if not all(math.isfinite(x) and x>1 for x in (o1,o2)):
            issues["INVALID_ODDS"]+=1
            continue
        booksum=1/o1+1/o2
        if not .95<=booksum<=1.35:
            issues["ABNORMAL_TWO_WAY_BOOKSUM"]+=1
            continue
        summary["pit_two_sided_market_pairs"]+=1
        if y is not None:
            summary["settled_pit_market_pairs"]+=1
        pairs.append(MarketPair(
            match_id=mid,edition_id=int(edition),run_id=int(run_id),
            issued_at=issued,start_at=start,
            tour="ATP_TOUR" if tour=="ATP_MAIN" else "CHALLENGER",
            surface=str(surface),pick_type=str(pick_type),
            picked_p1=p1,winner_p1=y,book_source=str(book_source),
            bookmaker=str(bookmaker),odds1=o1,odds2=o2,
            odds_observed_at=odds_at,odds_side_span_sec=float(span),
            a1_p1=_to_p1(a1_side,a1_prob),
            a2_p1=_to_p1(a2_side,a2_prob)
        ))
    return pairs,dict(sorted(summary.items())),dict(sorted(issues.items()))


def _score(pairs:list[MarketPair], key:str)->dict[str,Any]:
    scored=[r for r in pairs if r.winner_p1 is not None]
    vals=[(getattr(r,key),int(r.winner_p1)) for r in scored
          if getattr(r,key) is not None]
    n=len(vals)
    if not n:
        return {"n":0,"winner_picks":0,"accuracy":None,
                "brier":None,"log_loss":None,"mean_confidence":None}
    winner=[(p,y) for p,y in vals if abs(p-.5)>1e-12]
    return {"n":n,"winner_picks":len(winner),
            "accuracy":(sum(int((p>.5)==bool(y)) for p,y in winner)/len(winner)
                        if winner else None),
            "brier":sum((p-y)**2 for p,y in vals)/n,
            "log_loss":-sum(y*math.log(min(max(p,1e-8),1-1e-8))+
                             (1-y)*math.log1p(-min(max(p,1e-8),1-1e-8))
                             for p,y in vals)/n,
            "mean_confidence":sum(max(p,1-p) for p,y in vals)/n}


def _metrics(pairs:list[MarketPair],*,full_denominator:int)->dict[str,Any]:
    n=len(pairs)
    if not n:
        return {"n":0,"share_of_original_eligible":0,
                "market":_score(pairs,"market_p1"),
                "original_model":_score(pairs,"picked_p1")}
    actual=[r for r in pairs if r.winner_p1 is not None]
    roi=[(r.odds1 if r.picked_p1>.5 else r.odds2)
         if ((r.picked_p1>.5)==bool(r.winner_p1)) else 0
         for r in actual if abs(r.picked_p1-.5)>1e-12]
    return {
        "n":n,"share_of_original_eligible":n/full_denominator
            if full_denominator else None,
        "market":_score(pairs,"market_p1"),
        "original_model":_score(pairs,"picked_p1"),
        "a1":_score(pairs,"a1_p1"),
        "a2":_score(pairs,"a2_p1"),
        "descriptive_model_pick_same_timestamp_mean_odds":
            sum(r.odds1 if r.picked_p1>.5 else r.odds2 for r in actual)/len(actual)
            if actual else None,
        "descriptive_equal_stake_model_pick_roi":
            sum(roi)/len(roi)-1 if roi else None,
        "roi_is_not_value_bet_strategy":True,
    }


def _paired_cluster_ci(scored:list[MarketPair], *,
                       iterations:int=400)->dict[str,Any]:
    by_edition=defaultdict(list)
    for r in scored:
        by_edition[r.edition_id].append(r)
    if len(by_edition)<4:
        return {"status":"INSUFFICIENT_EDITION_CLUSTERS",
                "edition_count":len(by_edition)}
    keys=sorted(by_edition)
    rng=random.Random(RESEARCH_SEED)
    out=[]
    for _ in range(iterations):
        sample=[item for _ in keys
                for item in by_edition[rng.choice(keys)]]
        delta=sum((r.picked_p1-int(r.winner_p1))**2
                  -(r.market_p1-int(r.winner_p1))**2
                  for r in sample)/len(sample)
        out.append(delta)
    out.sort()
    return {
        "status":"HISTORICAL_TOURNAMENT_CLUSTER_BOOTSTRAP",
        "iterations":iterations,"seed":RESEARCH_SEED,
        "original_minus_market_brier_delta_95pct":[
            out[int((iterations-1)*.025)],out[int((iterations-1)*.975)]],
        "edition_count":len(keys),
    }


def evaluate(pairs:list[MarketPair],counts:dict[str,int],issues:dict[str,int],
             *,as_of:datetime)->dict[str,Any]:
    h=hashlib.sha256()
    for p in sorted(pairs,key=lambda r:r.match_id):
        h.update((
            f"{p.match_id}|{p.run_id}|{p.issued_at.isoformat()}|"
            f"{p.odds_observed_at.isoformat()}|{p.book_source}|{p.bookmaker}|"
            f"{p.odds1:.6f}|{p.odds2:.6f}|"
            f"{p.picked_p1:.6f}|{p.winner_p1}\n"
        ).encode())
    scored=[r for r in pairs if r.winner_p1 is not None]
    baseline={
        "version":VERSION,"status":"READ_ONLY_EVALUATION",
        "as_of_utc":as_of.astimezone(UTC).isoformat(),
        "counts":counts,"exclusions":issues,
        "paired_unique_matches":len(pairs),"scored_unique_matches":len(scored),
        "dataset_sha256":h.hexdigest(),
        "quote_max_age_hours":MAX_QUOTE_AGE_HOURS,
        "paired_side_max_gap_seconds":MAX_SIDE_SPAN_SECONDS,
        "matched_model_pick_cohort_not_all_market_matches":True,
        "full_market_eligible_census_done":False,
        "historical_closing_odds_used":False,
        "market_price_only_if_ingested_before_original_pick":True,
        "research_only":True,"db_writes":False,
        "live_model_rule_changed":False,"release_eligible":False,
    }
    if len(scored)<20:
        return {**baseline,"status":"INSUFFICIENT_PIT_MARKET_MATCHES",
                "reason":"No claims of reliable market accuracy with fewer than 20 settled pairs."}
    n=len(scored)
    observed=_metrics(scored,full_denominator=n)
    tiers={}
    ordered=sorted(scored,key=lambda r:(-r.favourite_confidence,r.match_id))
    for share in COVERAGE_TARGETS:
        chosen=ordered[:max(1,math.ceil(n*share))]
        tiers[str(share)]={**_metrics(chosen,full_denominator=n),
                           "coverage_of_all_original_settled_picks":
                           len(chosen)/counts.get("settled_pit_picks",1)
                           if counts.get("settled_pit_picks") else None}
    bands={}
    for threshold in (.60,.65,.70,.75,.80):
        selected=[r for r in scored if r.favourite_confidence>=threshold]
        bands[str(threshold)]=_metrics(selected,full_denominator=n)
    per_tour={}
    for tour in ("ATP_TOUR","CHALLENGER"):
        group=[r for r in scored if r.tour==tour]
        per_tour[tour]=_metrics(group,full_denominator=n)
    by_book={}
    for book in sorted({r.bookmaker for r in scored}):
        group=[r for r in scored if r.bookmaker==book]
        by_book[book]=_metrics(group,full_denominator=n)
    return {**baseline,"status":"EVALUATED_HISTORICAL_SAME_CUTOFF_MARKET",
            "same_fixture_market_vs_original":observed,
            "top_market_strength_coverage":tiers,
            "market_confidence_bands":bands,
            "by_tour":per_tour,"by_bookmaker":by_book,
            "original_minus_market_brier":
                observed["original_model"]["brier"]-
                observed["market"]["brier"],
            "paired_cluster_uncertainty":_paired_cluster_ci(scored),
            "scientific_limitations":(
                "Historical first-issued aligned Model Picks only, not all "
                "eligible ATP/Challenger match fixtures. Market book priority "
                "prefers Pinnacle if available. No model trained or tuned. "
                "Some historical fixtures/quotes may be missing. Filter coverage "
                "is on the matched cohort and must also be reported relative "
                "to all actual eligible Model Picks. No external vendor H2H. "
                "Bookmaker prices at a closing timestamp cannot be substituted "
                "for the original publication time."
            )}


def run(conn:psycopg.Connection, *,as_of:datetime)->dict[str,Any]:
    if as_of.tzinfo is None:
        raise ValueError("explicit timezone required")
    conn.execute("SET default_transaction_read_only=on")
    rows=conn.execute(source_sql(),{"as_of":as_of}).fetchall()
    pairs,counts,issues=normalize(rows,as_of=as_of)
    return {**evaluate(pairs,counts,issues,as_of=as_of),
            "sql_first_issued_rows":len(rows)}


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-utc",required=True)
    args=parser.parse_args(argv)
    if not args.as_of_utc.endswith("Z"):
        parser.error("as-of must use UTC Z")
    if not os.environ.get("TENNIS_DATABASE_URL"):
        parser.error("TENNIS_DATABASE_URL required")
    cutoff=datetime.fromisoformat(args.as_of_utc.replace("Z","+00:00"))
    with psycopg.connect(os.environ["TENNIS_DATABASE_URL"],
                         autocommit=True,connect_timeout=10) as conn:
        output=run(conn,as_of=cutoff)
    print("TENNIS_PIT_MARKET_BENCHMARK "+json.dumps(output,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
