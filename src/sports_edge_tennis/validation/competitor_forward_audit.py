"""Permissioned offline pre-match tennis competitor benchmark.

Only canonical, frozen fixture identity and authorized pre-match captured
forecasts are accepted. Does not fetch vendor sites, connect to production,
generate picks or interact with bookmaker endpoints. Input snapshots must be
independently retained; CSV hashes DO NOT prove external publication time.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TIERS = frozenset({"ATP_TOUR", "CHALLENGER"})
DRAWS = frozenset({"MAIN_DRAW", "QUALIFYING"})
SCORED = frozenset({"COMPLETED"})
PERMISSIONS = frozenset({"internal", "licensed", "authorized_public_export"})
CUTOFF_MINUTES = 30
VERSION = "ATP_CH_COMPETITOR_OFFLINE_V1"


def utc(value: str) -> datetime:
    if not value or not (value.endswith("Z") or value.endswith("+00:00")):
        raise ValueError("timestamp must be explicit UTC")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp offset must be +00:00")
    return parsed


def _mean(numbers: list[float]) -> float | None:
    return sum(numbers) / len(numbers) if numbers else None


def _logloss(p: float, y: int) -> float:
    return -(math.log(p) if y else math.log1p(-p))


def _metrics(points: list[tuple[float, int]]) -> dict[str, Any]:
    return {
        "settled_evaluated": len(points),
        "hit_rate": _mean([float((p >= .5) == bool(y)) for p,y in points]),
        "brier": _mean([(p-y)**2 for p,y in points]),
        "log_loss": _mean([_logloss(p,y) for p,y in points]),
    }


def _cluster_ci(rows: list[tuple[str, float]], *, draws: int = 500) -> list[float] | None:
    clusters: dict[str, list[float]] = defaultdict(list)
    for cluster, diff in rows:
        clusters[cluster].append(diff)
    if len(clusters) < 4:
        return None
    keys = sorted(clusters)
    rng = random.Random(20261118)
    samples = []
    for _ in range(draws):
        values: list[float] = []
        for _ in keys:
            values.extend(clusters[rng.choice(keys)])
        samples.append(sum(values)/len(values))
    samples.sort()
    return [samples[int(.025*(draws-1))], samples[int(.975*(draws-1))]]


def compare(fixtures: list[dict[str, str]],
            forecasts: list[dict[str, str]], *,
            baseline: str = "sports_edge_original",
            cutoff_minutes: int = CUTOFF_MINUTES) -> dict[str, Any]:
    if cutoff_minutes < 1 or cutoff_minutes > 1440:
        raise ValueError("invalid immutable pre-match cutoff")
    universe: dict[str, dict[str, Any]] = {}
    for row in fixtures:
        mid = row["match_id"].strip()
        if not mid or mid in universe:
            raise ValueError("blank or duplicate canonical match_id")
        tier = row["tour"].strip()
        draw = row["draw"].strip()
        if tier not in TIERS or draw not in DRAWS:
            raise ValueError("fixture outside permitted ATP/Ch singles universe")
        if not row["player1_id"].strip() or not row["player2_id"].strip():
            raise ValueError("fixture participant identities missing")
        if row["player1_id"] == row["player2_id"]:
            raise ValueError("fixture has identical players")
        frozen_start = utc(row["frozen_start_utc"])
        status = row["result_status"].strip().upper()
        if status in SCORED and row["winner_side"] not in ("1","2"):
            raise ValueError("terminal match missing winner")
        if status not in SCORED | {"PENDING","SCHEDULED","RETIRED","WALKOVER","DEFAULTED","VOID","CANCELLED","POSTPONED"}:
            raise ValueError("unknown result status")
        tournament = row["tournament_id"].strip()
        if not tournament:
            raise ValueError("missing tournament identifier")
        universe[mid] = {
            "tier":tier, "draw":draw, "p1":row["player1_id"],
            "p2":row["player2_id"], "start":frozen_start,
            "result":status, "y":int(row["winner_side"]=="1") if status in SCORED else None,
            "cluster":tournament + ":" + frozen_start.date().isoformat()[:7],
        }

    # Each source must contribute one and only one eligible forecast per
    # match. Ambiguous duplicates remove both to prevent choosing a winner
    # after seeing the result.
    accepted: dict[tuple[str,str], float] = {}
    rejected: Counter[str] = Counter()
    duplicates: set[tuple[str,str]] = set()
    sources = {baseline}
    for row in forecasts:
        source = row.get("source","").strip()
        mid = row.get("match_id","").strip()
        if not source:
            rejected["MISSING_SOURCE"] += 1
            continue
        sources.add(source)
        if mid not in universe:
            rejected["UNKNOWN_MATCH"] += 1
            continue
        key = (source,mid)
        if key in duplicates:
            rejected["DUPLICATE_FORECAST"] += 1
            continue
        if key in accepted:
            del accepted[key]
            duplicates.add(key)
            rejected["DUPLICATE_FORECAST"] += 2
            continue
        fx = universe[mid]
        if row.get("rights","").strip() not in PERMISSIONS:
            rejected["RIGHTS_NOT_APPROVED"] += 1
            continue
        if row.get("player1_id","").strip()!=fx["p1"] or row.get("player2_id","").strip()!=fx["p2"]:
            rejected["PARTICIPANT_ORIENTATION_MISMATCH"] += 1
            continue
        try:
            pub = utc(row.get("published_at_utc",""))
            cap = utc(row.get("captured_at_utc",""))
            prob = float(row.get("p_player1",""))
        except (ValueError,TypeError):
            rejected["INVALID_FORECAST"] += 1
            continue
        if not 0 < prob < 1:
            rejected["INVALID_PROBABILITY"] += 1
            continue
        deadline = fx["start"]-timedelta(minutes=cutoff_minutes)
        if pub>cap or pub>deadline or cap>deadline:
            rejected["LATE_OR_UNVERIFIABLE_TIMESTAMP"] += 1
            continue
        if not row.get("model_version","").strip():
            rejected["MISSING_FROZEN_MODEL_VERSION"] += 1
            continue
        accepted[key]=prob

    scored = {mid:fx for mid,fx in universe.items() if fx["y"] is not None}
    tiers = ["ATP_TOUR","CHALLENGER"]
    per_tour = []
    pairs = []
    for tour in tiers:
        tour_ids = [mid for mid,fx in universe.items() if fx["tier"]==tour]
        settled = [mid for mid in tour_ids if mid in scored]
        for source in sorted(sources):
            all_seen = [mid for mid in tour_ids if (source,mid) in accepted]
            points = [(accepted[(source,mid)],scored[mid]["y"]) for mid in settled
                      if (source,mid) in accepted]
            per_tour.append({
                "tour":tour,"source":source, "universe_matches":len(tour_ids),
                "settled_matches":len(settled),"forward_captured":len(all_seen),
                "missing_forecasts":len(tour_ids)-len(all_seen),
                "settled_coverage":len(points)/len(settled) if settled else None,
                **_metrics(points)
            })
            if source == baseline:
                continue
            both = [mid for mid in settled if (source,mid) in accepted
                    and (baseline,mid) in accepted]
            paired_ll = []
            paired_brier = []
            for mid in both:
                p=accepted[(source,mid)]
                p0=accepted[(baseline,mid)]
                y=scored[mid]["y"]
                cl=scored[mid]["cluster"]
                paired_ll.append((cl,_logloss(p,y)-_logloss(p0,y)))
                paired_brier.append((cl,(p-y)**2-(p0-y)**2))
            pairs.append({
                "tour":tour,"challenger":source,"baseline":baseline,
                "paired_settled_matches":len(both),
                "paired_log_loss_delta":_mean([v for _,v in paired_ll]),
                "paired_brier_delta":_mean([v for _,v in paired_brier]),
                "paired_log_loss_95pct_cluster_ci":_cluster_ci(paired_ll),
                "paired_brier_95pct_cluster_ci":_cluster_ci(paired_brier),
                "direction":"negative_delta_favors_challenger",
                "sample_caution":"descriptive only; no improvement conclusion without adequate clusters and prospective validation",
            })

    return {
        "version":VERSION,"pre_match_cutoff_minutes":cutoff_minutes,
        "universe_unique_matches":len(universe),
        "settled_unique_matches":len(scored),
        "pending_or_void_matches":len(universe)-len(scored),
        "sources":sorted(sources),
        "rejected_forecast_rows":dict(sorted(rejected.items())),
        "per_tour":per_tour,"paired":pairs,
        "provenance_notice":(
            "All sources and rights are user-supplied. In-file timestamps and "
            "SHA-256 cannot independently prove pre-match publication. Not a "
            "live or ROI validation; no vendor scraping, odds or staking."
        )
    }


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures_csv",type=Path)
    parser.add_argument("forecasts_csv",type=Path)
    parser.add_argument("--baseline",default="sports_edge_original")
    parser.add_argument("--cutoff-minutes",type=int,default=CUTOFF_MINUTES)
    args=parser.parse_args(argv)
    paths=(args.fixtures_csv,args.forecasts_csv)
    with paths[0].open(newline="",encoding="utf-8") as f:
        fixtures=list(csv.DictReader(f))
    with paths[1].open(newline="",encoding="utf-8") as f:
        forecasts=list(csv.DictReader(f))
    result=compare(fixtures,forecasts,baseline=args.baseline,
                   cutoff_minutes=args.cutoff_minutes)
    result["sha256_input_files"]={
        str(p.name):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }
    print(json.dumps(result,sort_keys=True,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
