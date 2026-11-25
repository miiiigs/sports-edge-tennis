"""Read-only, local-only OddsPapi evidence export and replay.

Requires an authorized existing database connection; never deploys or calls a provider.
Prints aggregate evidence only. Optional private export is mode 0600, create-exclusive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import timedelta

import psycopg

from sports_edge_tennis.live.oddspapi_identity_cache import identity_invalidation_reason
from sports_edge_tennis.live.oddspapi_identity_history import (
    IdentityHistoryUnavailable,
    identity_at_cutoff,
)
from sports_edge_tennis.validation.odds_identity_market_funnel import audit, stamp, keys, BOOKS
from sports_edge_tennis.validation.preissue_odds_full_slate_dryrun import load_candidates
from sports_edge_tennis.validation.pit_bookmaker_model_picks_benchmark import source_sql

FIXTURE_SQL = """
SELECT v.player_1_id,v.player_2_id,v.match_start_at,v.tour_scope::text,
       v.qualifying_flag,m.tournament_edition_id,e.tournament_id,e.start_date,e.end_date,
       EXISTS (SELECT 1 FROM match_versions p WHERE p.match_id=m.match_id
          AND greatest(p.as_of,p.source_observed_at,p.ingested_at)<=%s
          AND (p.player_1_id,p.player_2_id)<>(v.player_1_id,v.player_2_id))
FROM matches m JOIN tournament_editions e USING (tournament_edition_id)
JOIN LATERAL (SELECT * FROM match_versions v WHERE v.match_id=m.match_id
 AND greatest(v.as_of,v.source_observed_at,v.ingested_at)<=%s
 ORDER BY v.as_of DESC,v.source_observed_at DESC,v.match_version_id DESC LIMIT 1) v ON true
WHERE m.match_id=%s
"""
SOURCE_SQL = """SELECT source_system,source_match_id FROM match_source_ids
WHERE match_id=%s AND created_at<=%s AND source_match_id IS NOT NULL
ORDER BY source_system,source_match_id"""
RAW_SQL = """
SELECT r.raw_odds_observation_id,r.provider_event_id,r.observation_time,r.received_at,
       r.created_at,r.observation_time_basis::text,r.payload_hash,
       r.payload_json->>'fixtureId',r.payload_json->>'tournamentId',
       r.payload_json->>'startTime',r.payload_json->>'participant1Id',
       r.payload_json->>'participant2Id',r.payload_json#>>'{externalProviders,betradarId}',
       r.payload_json->>'sportId'
FROM raw_odds_observations r
WHERE r.provider_code='ODDSPAPI' AND r.received_at<=%s AND r.created_at<=%s
 AND r.received_at>=%s
 AND (r.provider_event_id=ANY(%s)
 OR regexp_replace(r.payload_json#>>'{externalProviders,betradarId}','^sr:sport_event:','')=ANY(%s))
ORDER BY r.received_at DESC,r.raw_odds_observation_id DESC
"""
QUOTE_SQL = """
SELECT o.odds_snapshot_id,o.match_id,r.provider_event_id,b.provider_bookmaker_key,
       o.source_system,o.selection_type::text,o.decimal_price::text,o.market_status::text,
       o.source_observed_at,o.as_of,o.ingested_at,r.received_at,r.created_at,
       r.observation_time,r.observation_time_basis::text,r.payload_hash,
       r.payload_json->>'participant1Id',r.payload_json->>'participant2Id',
       r.payload_json->>'fixtureId',r.raw_odds_observation_id
FROM odds_snapshots o JOIN raw_odds_observations r USING(raw_odds_observation_id)
JOIN provider_bookmaker_mappings b USING(provider_bookmaker_mapping_id)
WHERE o.match_id=%s AND o.market_type='MATCH_WINNER' AND o.odds_origin='LIVE_PROVIDER'
 AND r.provider_code='ODDSPAPI' AND b.provider_code='ODDSPAPI'
 AND b.bookmaker_code=o.bookmaker_code AND b.normalization_class::text=o.source_system
 AND b.effective_from<=o.source_observed_at AND b.created_at<=o.ingested_at
 AND greatest(o.source_observed_at,o.as_of,o.ingested_at,r.received_at,r.created_at)<=%s
ORDER BY o.odds_snapshot_id
"""
# A raw observation can record suspension without any normalized prices.
RAW_BOOK_SQL = """
SELECT r.raw_odds_observation_id,b.key,
       b.value->>'suspended',b.value#>>'{markets,121,marketActive}',
       b.value#>>'{markets,121,outcomes,121,players,0,active}',
       b.value#>>'{markets,121,outcomes,122,players,0,active}'
FROM raw_odds_observations r
CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(r.payload_json->'bookmakerOdds')='object'
 THEN r.payload_json->'bookmakerOdds' ELSE '{}'::jsonb END) b
WHERE r.raw_odds_observation_id=ANY(%s)
 AND b.key=ANY(%s)
"""


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def iso(t):
    return t.isoformat()


def player_ids(conn, ids, at):
    if not all(ids) or len(set(ids)) != 2:
        return []
    rows = conn.execute("""SELECT source_player_id,player_id FROM player_source_ids
        WHERE source_system='ODDSPAPI' AND source_player_id=ANY(%s) AND created_at<=%s""",
        (list(ids), at)).fetchall()
    lookup = dict(rows)
    return [lookup.get(p) for p in ids]


def raw_event(conn, row, cutoff):
    rid,eid,obs,recv,created,basis,hash_,payload_id,tour,start,p1,p2,sr,sport = row
    if payload_id != eid or sport != "12" or not hash_ or not start:
        return None
    if not obs <= recv <= cutoff or created > cutoff:
        return None
    try:
        start_at = stamp(start)
        tournament = int(tour)
    except (ValueError, TypeError, AttributeError):
        return None
    return {"event_id":eid,"external_ids":{"SPORTRADAR":sr},
            "canonical_players":player_ids(conn,(p1,p2),min(cutoff,max(recv,created))),
            "tournament_id":tournament,"observed_at":iso(max(recv,created)),
            "start_at":iso(start_at),"raw_id":rid,"raw_hash":hash_,
            "observation_basis":basis,"source_observed_at":iso(obs),"received_at":iso(recv),
            "stored_at":iso(created)}


def extract_one(conn, mid, cutoff, report_at):
    counts = Counter()
    row = conn.execute(FIXTURE_SQL,(cutoff,cutoff,mid)).fetchone()
    if row is None:
        return None,counts+Counter({"missing_pit_fixture":1})
    p1,p2,start,tour,qual,edition,tid,edition_start,edition_end,conflict = row
    if tour not in ("ATP_MAIN","CHALLENGER") or qual is not False or cutoff>start-timedelta(minutes=30):
        return None,Counter({"ineligible_or_late":1})
    if conflict:
        return None,Counter({"historical_player_order_conflict":1})
    ids = defaultdict(set)
    for ns,value in conn.execute(SOURCE_SQL,(mid,cutoff)).fetchall():
        ids[ns].add(value)
    if any(len(values)>1 for ns,values in ids.items() if ns in ("ODDSPAPI","SPORTRADAR","PINNACLE")):
        counts["conflicting_source_ids"]+=1
    src={ns:next(iter(values)) for ns,values in ids.items() if len(values)==1}
    # Only independent tournament source mapping, with edition window evidence.
    allowed=[]
    if edition_start and edition_end and edition_start<=start.date()<=edition_end:
        for (value,) in conn.execute("""SELECT source_tournament_id FROM tournament_source_ids
             WHERE tournament_id=%s AND source_system='ODDSPAPI' AND created_at<=%s""",(tid,cutoff)).fetchall():
            if value.isascii() and value.isdecimal():
                allowed.append(int(value))
    counts["persistent_mapping"]+=int("ODDSPAPI" in src)
    counts["independent_tournament_mapping"]+=int(bool(allowed))
    cache=conn.execute("""SELECT oddspapi_event_id,last_verified_at,resolved_at,resolution_status,
        scheduled_start,player_orientation,sportradar_event_id FROM oddspapi_event_identity_cache WHERE canonical_match_id=%s""",
        (mid,)).fetchone()
    warm=bool(cache and cache[1] and cache[2] and cache[1]<=cutoff and cache[2]<=cutoff
        and cutoff-cache[1]<=timedelta(hours=24) and cache[3]=="EXACT" and cache[4]
        and abs(cache[4]-start)<=timedelta(minutes=30) and cache[5] in ("DIRECT","REVERSED")
        and cache[0] and (not src.get("SPORTRADAR") or not cache[6]
             or dict(keys({"SPORTRADAR":src["SPORTRADAR"]})).get("SPORTRADAR")
             ==dict(keys({"SPORTRADAR":cache[6]})).get("SPORTRADAR")))
    counts["cache_current_row_warm_at_cutoff"]+=int(warm)
    if cache and cache[1] and cache[1]>cutoff:
        counts["cache_updated_after_cutoff_history_unavailable"]+=1
    # Immutable history replaces the mutable cache row where it exists. Same
    # rule set, same tolerances; only the storage is append-only.
    try:
        history_state=identity_at_cutoff(conn,match_id=mid,as_of=cutoff)
    except IdentityHistoryUnavailable:
        counts["identity_history_unavailable"]+=1
    else:
        history_warm=bool(
            history_state.identity is not None
            and identity_invalidation_reason(
                history_state.identity,
                sportradar_event_id=src.get("SPORTRADAR"),
                scheduled_start=start,
                now=cutoff,
            ) is None
        )
        counts["identity_history_warm_at_cutoff"]+=int(history_warm)
    normalized=dict(keys(src))
    raw=conn.execute(RAW_SQL,(cutoff,cutoff,cutoff-timedelta(hours=24),
        [normalized["ODDSPAPI"]] if "ODDSPAPI" in normalized else [],
        [normalized["SPORTRADAR"]] if "SPORTRADAR" in normalized else [])).fetchall()
    counts["raw_observations"]+=len(raw)
    counts["fixture_with_raw_observation"]+=int(bool(raw))
    # Latest observation per event; never select an earlier valid row after an invalid latest row.
    latest={}
    for r in raw:
        latest.setdefault(r[1],r)
    events=[]
    for r in latest.values():
        event=raw_event(conn,r,cutoff)
        if event:
            events.append(event)
        else:
            counts["invalid_latest_raw_identity"]+=1
    event_ids={e["event_id"] for e in events}
    fixture={"match_id":mid,"source_ids":src,"players":[p1,p2],"verified_tournament_ids":sorted(set(allowed)),
             "start_at":iso(start),"cutoff":iso(cutoff)}
    quotes=[]
    # Include later quotations through report_at for classification, never use them at cutoff.
    for q in conn.execute(QUOTE_SQL,(mid,report_at)).fetchall():
        oid,_,eid,book,source,side,price,status,obs,asof,ing,recv,created,rawobs,basis,hash_,rp1,rp2,payload_id,rid=q
        if eid not in event_ids or book not in BOOKS:
            continue
        # Check every raw quote's participant bridge independently at ingestion.
        oriented=player_ids(conn,(rp1,rp2),ing)
        if set(oriented)!={p1,p2} or payload_id!=eid or side not in ("PLAYER_1","PLAYER_2"):
            counts["invalid_quote_side"]+=1
            player=None
        else:
            player=p1 if side=="PLAYER_1" else p2
        if not rawobs==obs or not recv<=ing or not created<=ing or not hash_:
            counts["invalid_quote_lineage_clock"]+=1
            # Mark invalid provenance without changing its time to a usable value.
            player=None
        quotes.append({"event_id":eid,"bookmaker":book,"source":source,"market":"MATCH_WINNER",
            "player_id":player,"price":price,"status":status,"observed_at":iso(obs),
            "as_of":iso(asof),"ingested_at":iso(ing),"snapshot_id":oid,"raw_id":rid,
            "raw_hash":hash_,"raw_received_at":iso(recv),"raw_created_at":iso(created),"time_basis":basis})
    # Suspend based on actual raw state even if the normalizer emitted no price.
    raw_lookup={r[0]:r for r in raw}
    for rid,book,susp,active,a1,a2 in conn.execute(RAW_BOOK_SQL,(list(raw_lookup),list(BOOKS))).fetchall():
        r=raw_lookup[rid]
        if r[1] not in event_ids:
            continue
        counts["raw_book_observations"]+=1
        if susp=="true" or active=="false" or a1=="false" or a2=="false":
            if r[7]!=r[1] or set(player_ids(conn,(r[10],r[11]),max(r[3],r[4])))!={p1,p2}:
                counts["unverified_raw_suspension_side"]+=1
                # A conflicting raw state must block comparison, not revive an older open quote.
                quotes.append({"event_id":r[1],"bookmaker":book,"source":"ODDSPAPI",
                    "market":"MATCH_WINNER","player_id":None,"price":None,"status":"UNKNOWN",
                    "observed_at":iso(r[2]),"as_of":iso(r[3]),"ingested_at":iso(max(r[3],r[4])),
                    "raw_id":rid,"raw_hash":r[6]})
                continue
            for player in (p1,p2):
                if any(q["raw_id"]==rid and q["bookmaker"]==book and q["player_id"]==player
                       and q["status"]=="SUSPENDED" for q in quotes):
                    continue
                quotes.append({"event_id":r[1],"bookmaker":book,
                   "source":"SHARP" if book=="pinnacle+30" else "OFFERED",
                   "market":"MATCH_WINNER","player_id":player,"price":None,"status":"SUSPENDED",
                   "observed_at":iso(r[2]),"as_of":iso(r[3]),"ingested_at":iso(max(r[3],r[4])),
                   "raw_id":rid,"raw_hash":r[6],"derived_raw_suspension":True})
            counts["raw_suspension_observations"]+=1
    counts["fixture_with_linked_quote_or_state"]+=int(bool(quotes))
    data={"audit_at":iso(cutoff),"fixtures":[fixture],"events":events,"quotes":quotes}
    return data,counts


def run(conn, *, as_of, mode, horizon_days=3, match_ids=None):
    # Explicit transaction safety. No mutation helpers or provider transport calls.
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout='60s'")
        if conn.execute("SHOW transaction_read_only").fetchone()[0]!="on":
            raise RuntimeError("read-only guard failed")
        if mode=="future":
            candidates=load_candidates(conn,now=as_of,horizon_days=horizon_days)
            cohort=[(c["match_id"],as_of) for c in candidates if c["tour_scope"] in ("ATP_MAIN","CHALLENGER")
                    and c["qualifying_flag"] is False and as_of+timedelta(minutes=30)<=c["match_start"]<=as_of+timedelta(days=7)]
        else:
            rows=conn.execute(source_sql(),{"as_of":as_of}).fetchall()
            cohort=[(r[0],r[2]) for r in rows if r[2] is not None]
        input_candidates=len(candidates) if mode=="future" else None
        if len({m for m,_ in cohort})!=len(cohort):
            raise ValueError("duplicate canonical cohort")
        if match_ids is not None:
            cohort=[(m,t) for m,t in cohort if m in match_ids]
        counters=Counter()
        identities=Counter()
        books=defaultdict(Counter)
        exports=[]
        qualified=0
        candidates=[]
        staged=[]
        for mid,cutoff in sorted(cohort):
            data,counts=extract_one(conn,mid,cutoff,as_of)
            counters.update(counts)
            if data is None:
                continue
            exports.append(data)
            result=audit(data)
            if counts["conflicting_source_ids"]:
                identities["IDENTITY_CONFLICT"]+=1
                continue
            staged.append((mid,data,counts,result))
        owners=defaultdict(set)
        for mid,data,counts,result in staged:
            if result["identity_counts"].get("VERIFIED_IDENTITY"):
                from sports_edge_tennis.validation.odds_identity_market_funnel import identity,event_keys
                index=defaultdict(set)
                for i,e in enumerate(data["events"]):
                    for key in event_keys(e):
                        index[key].add(i)
                _,event=identity(data["fixtures"][0],data["events"],index,stamp(data["audit_at"]))
                if event is not None:
                    owners[event["event_id"]].add(mid)
        collided={m for matches in owners.values() if len(matches)>1 for m in matches}
        for mid,data,counts,result in staged:
            if mid in collided:
                identities["IDENTITY_CONFLICT"]+=1
                continue
            identities.update(result["identity_counts"])
            for book,states in result["book_states"].items():
                books[book].update(states)
            qualified+=result["qualified_any_book"]
            history_available = counts["identity_history_unavailable"] == 0
            warm_at_cutoff = (counts["identity_history_warm_at_cutoff"]
                              if history_available
                              else counts["cache_current_row_warm_at_cutoff"])
            if result["identity_counts"].get("VERIFIED_IDENTITY") and not warm_at_cutoff:
                candidates.append(mid)
        summary={"mode":mode,"report_as_of":iso(as_of),"cohort_count":len(cohort),
                 "input_unique_candidates":input_candidates,
                 "cohort_sha256":digest([(m,iso(t)) for m,t in sorted(cohort)]),
                 "sample_sha256":digest(exports),"identity_counts":dict(identities),
                 "book_states":{k:dict(v) for k,v in books.items()},
                 "qualified_any_book":qualified,"diagnostic_counters":dict(counters),
                 "nonwarm_verified_candidates":len(candidates),
                 "nonwarm_verified_candidate_sha256":digest(candidates),
                 "identity_warm_method":("append_only_history_v1"
                     if counters["identity_history_unavailable"]==0
                     else "mutable_cache_current_row_fallback"),
                 "provider_calls":0,"database_writes":False,
                 "historical_cache_reconstruction":False,"research_only":True}
        return summary,exports


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of-utc",required=True)
    p.add_argument("--mode",choices=("future","original"),required=True)
    p.add_argument("--match-ids",type=int,nargs="+")
    p.add_argument("--private-export")
    a=p.parse_args()
    if not a.as_of_utc.endswith("Z"):
        p.error("use explicit UTC Z timestamp")
    dsn=os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        p.error("authorized TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn,autocommit=True,connect_timeout=10) as conn:
        summary,exports=run(conn,as_of=stamp(a.as_of_utc),mode=a.mode,match_ids=set(a.match_ids) if a.match_ids else None)
    if a.private_export:
        fd=os.open(a.private_export,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as out:
            json.dump(exports,out,sort_keys=True)
    print(json.dumps(summary,sort_keys=True))


if __name__=="__main__":
    main()
