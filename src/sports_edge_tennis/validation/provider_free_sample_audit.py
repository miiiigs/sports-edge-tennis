"""Offline, no-cost audit of provider sample CSVs before any purchase.

Assess advertised sample columns and their non-empty values. This intentionally
does not claim that small samples establish ATP/Challenger coverage, historical
point-in-time availability, accuracy, retention rights, or a useful new provider.
No network, DB, API keys, or filesystem writes are performed by this module.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

VERSION="FREE_TENNIS_PROVIDER_SAMPLE_FIELD_GATE_V1"

# Aliases are *hints*, not verified Cito/TennisData schemas. Unknown columns
# must be retained so a source engineer can review them manually.
MATCH_FIELDS:dict[str,tuple[str,...]]={
    "fixture_id":("match_id","fixture_id","event_id","id"),
    "player1_id":("player_1_id","player1_id","player_a_id","home_player_id"),
    "player2_id":("player_2_id","player2_id","player_b_id","away_player_id"),
    "match_start":("match_start_at","start_time","start_at","scheduled_at","match_datetime","date"),
    "tour_level":("tour_scope","tour_level","level","competition_tier","tournament_level"),
    "surface":("surface","court_surface"),
    "actual_round":("round","round_code","round_name"),
    "winner":("winner_id","winner_player_id","winner_side","winner"),
    "match_status":("match_status","status","outcome_status"),
    "fixture_published_at":("fixture_published_at","first_seen_at","observed_at","source_observed_at"),
    "fixture_ingested_at":("fixture_ingested_at","ingested_at"),
    "rating1":("player1_elo","p1_elo","elo1","home_elo","player_1_elo"),
    "rating2":("player2_elo","p2_elo","elo2","away_elo","player_2_elo"),
    "rating_as_of":("elo_as_of","rating_as_of","ratings_timestamp","ranking_date","rank_as_of"),
    "serve1":("player1_service_points_won","p1_serve_points_won","server1_points_won"),
    "serve2":("player2_service_points_won","p2_serve_points_won","server2_points_won"),
    "return1":("player1_return_points_won","p1_return_points_won"),
    "return2":("player2_return_points_won","p2_return_points_won"),
    "book1_odds":("bookmaker_player1_odds","p1_odds","home_odds","opening_odds1"),
    "book2_odds":("bookmaker_player2_odds","p2_odds","away_odds","opening_odds2"),
    "bookmaker_name":("bookmaker","bookmaker_name","sportsbook"),
    "odds_observed_at":("odds_observed_at","odds_timestamp","odds_created_at"),
}
PLAYER_FIELDS:dict[str,tuple[str,...]]={
    "player_id":("player_id","id","athlete_id"),
    "player_name":("player_name","name","full_name"),
    "global_rank":("current_rank","rank","ranking","atp_rank"),
    "rank_as_of":("ranking_date","rank_as_of","ranking_as_of"),
    "historical_depth":("career_matches","matches_played","career_match_count"),
    "level_history":("competition_level_history","itf_history","junior_history"),
    "last_match_at":("last_match_at","last_completed_match_date"),
    "surface_elo":("surface_elo","hard_elo","clay_elo","grass_elo"),
    "rating_uncertainty":("rating_deviation","glicko_rd","elo_uncertainty"),
}

# Based on known existing repository feature families and the latest real PIT
# audits. "partial" is not a population-wide completeness estimate.
CURRENT_SYSTEM={
    "fixture_id":"known","player1_id":"known","player2_id":"known",
    "match_start":"known","tour_level":"known","surface":"partial",
    "actual_round":"known","winner":"known","match_status":"known",
    "fixture_published_at":"partial","fixture_ingested_at":"known",
    "rating1":"partial","rating2":"partial","rating_as_of":"partial",
    "serve1":"partial","serve2":"partial","return1":"partial","return2":"partial",
    "book1_odds":"partial","book2_odds":"partial","bookmaker_name":"partial",
    "odds_observed_at":"partial",
    "player_id":"known","player_name":"known","global_rank":"partial",
    "rank_as_of":"partial","historical_depth":"partial",
    "level_history":"not_verified","last_match_at":"partial",
    "surface_elo":"partial","rating_uncertainty":"not_verified",
}


def normalize(column:str)->str:
    return "".join(c if c.isalnum() else "_" for c in column.strip().lower()).strip("_")


def _granularity(value:str)->str:
    value=value.strip()
    if not value:
        return "empty"
    try:
        date.fromisoformat(value)
        return "date_only"
    except ValueError:
        pass
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        return "timestamp_aware" if dt.tzinfo else "timestamp_naive"
    except ValueError:
        return "unrecognized"


def analyze(path:Path, *, kind:str, limit:int=20000)->dict[str,Any]:
    if kind not in ("matches","players"):
        raise ValueError("kind must be matches or players")
    if limit<1 or limit>100000:
        raise ValueError("limit must be 1..100000")
    if not path.is_file():
        raise FileNotFoundError(path)
    size=path.stat().st_size
    if size>20_000_000:
        raise ValueError("provider sample exceeds 20MB safety limit")
    with path.open("r",encoding="utf-8-sig",newline="") as file:
        reader=csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError("CSV must provide a non-empty header")
        original=[h.strip() for h in reader.fieldnames if h is not None]
        norm=[normalize(h) for h in original]
        if not all(norm) or len(set(norm))!=len(norm):
            raise ValueError("blank or duplicate column after normalization")
        groups=MATCH_FIELDS if kind=="matches" else PLAYER_FIELDS
        alias={key:next((name for name in aliases if name in norm),None)
               for key,aliases in groups.items()}
        counts=Counter()
        granularity=Counter()
        rows=0
        id_values=set()
        duplicate_ids=0
        sample_identifier=alias.get("fixture_id" if kind=="matches" else "player_id")
        for row in reader:
            if rows>=limit:
                break
            rows+=1
            payload={normalize(str(k)):str(v).strip() if v is not None else ""
                     for k,v in row.items() if k is not None}
            for key,col in alias.items():
                if col is not None and payload.get(col):
                    counts[key]+=1
            if kind=="matches" and alias.get("match_start"):
                granularity[_granularity(payload.get(alias["match_start"],""))]+=1
            if sample_identifier:
                identifier=payload.get(sample_identifier,"")
                if identifier:
                    if identifier in id_values:
                        duplicate_ids+=1
                    id_values.add(identifier)
    # Hash the actual bytes, not just a path/name, for reproducible fixture
    # and provider sample reviews; no source rows are copied to output.
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    fields={
        name:{
            "column":alias[name],
            "nonempty_rows":counts[name],
            "nonempty_fraction":counts[name]/rows if rows else None,
            "current_system_state":CURRENT_SYSTEM.get(name,"not_verified"),
            "additional_coverage_not_proven":True,
        } for name in groups
    }
    # Critical pair/group completeness is conservative: min marginal count
    # only; it is an UPPER BOUND, not the number of complete joined rows.
    def possible(*names:str)->int:
        return min((counts.get(n,0) for n in names), default=0)
    paired={}
    if kind=="matches":
        paired={
            "both_players_potential_upper_bound":possible("player1_id","player2_id"),
            "both_serve_potential_upper_bound":possible("serve1","serve2"),
            "both_return_potential_upper_bound":possible("return1","return2"),
            "two_sided_odds_with_timestamp_potential_upper_bound":
                possible("book1_odds","book2_odds","bookmaker_name","odds_observed_at"),
            "rating_both_sides_with_valid_asof_upper_bound":
                possible("rating1","rating2","rating_as_of"),
        }
    return {
        "version":VERSION,"kind":kind,"sha256":digest,
        "sample_bytes":size,"rows_inspected":rows,"max_rows":limit,
        "truncated_at_limit":rows==limit,
        "columns":original,"unknown_columns":[original[i] for i,n in enumerate(norm)
            if not any(n in a for a in groups.values())],
        "fields":fields,"potential_joint_field_upper_bounds":paired,
        "match_start_value_granularity":dict(sorted(granularity.items())),
        "duplicate_nonempty_sample_identifiers":duplicate_ids,
        "network_requests":0,"cost_usd":0,"database_writes":0,
        "commercial_use_and_storage_rights":"NOT_VERIFIED",
        "source_observation_time_validity":"NOT_VERIFIED",
        "full_atp_challenger_coverage":"NOT_VERIFIED",
        "incremental_vs_our_current_matches":"NOT_VERIFIED",
        "purchase_approved":False,
        "note":("Observed sample columns and filled cells only. Review raw "
                "source meanings and obtain actual overlapping canonical IDs, "
                "pre-match capture timestamps, and written commercial rights. "
                "Date-only samples cannot establish fixture-level order.")
    }


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matches-csv",type=Path)
    parser.add_argument("--players-csv",type=Path)
    parser.add_argument("--sample-limit",type=int,default=20000)
    args=parser.parse_args(argv)
    if not args.matches_csv and not args.players_csv:
        parser.error("at least one --matches-csv or --players-csv required")
    reports=[]
    for kind,path in (("matches",args.matches_csv),("players",args.players_csv)):
        if path is not None:
            reports.append(analyze(path,kind=kind,limit=args.sample_limit))
    print("TENNIS_PROVIDER_FREE_SAMPLE_AUDIT "+json.dumps(reports,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
