"""Exact canonical sporting identities for the five unresolved Model Picks.

Read-only. Exports only athletes/tournament and provider identity keys; no
private user data, DB mutations, result inference, bookmaker requests or odds.
Use alongside Nov19 provider status re-observation, not to infer winners.
"""
from __future__ import annotations
import json
import os
from typing import Any
import psycopg

PENDING_IDS=(130903,131712,133361,133347,133978)

_SQL="""
WITH latest AS (
 SELECT DISTINCT ON (mv.match_id) mv.match_id,mv.player_1_id,
        mv.player_2_id,mv.match_start_at,mv.match_status::text AS fixture_status
 FROM match_versions mv
 WHERE mv.match_id = ANY(%(ids)s)
 ORDER BY mv.match_id,mv.as_of DESC,mv.source_observed_at DESC,mv.match_version_id DESC
)
SELECT l.match_id,p1.canonical_name AS player1,p2.canonical_name AS player2,
       t.canonical_name AS tournament,t.tour_scope::text,
       te.season_year,l.fixture_status,
       (SELECT psi.source_player_id
        FROM player_source_ids psi WHERE psi.player_id=l.player_1_id
             AND psi.source_system='API_TENNIS' LIMIT 1) AS p1_key,
       (SELECT psi.source_player_id
        FROM player_source_ids psi WHERE psi.player_id=l.player_2_id
             AND psi.source_system='API_TENNIS' LIMIT 1) AS p2_key,
       (SELECT ti.source_tournament_id
        FROM tournament_source_ids ti WHERE ti.tournament_id=t.tournament_id
             AND ti.source_system='API_TENNIS' LIMIT 1) AS tournament_key,
       (SELECT si.source_match_id
        FROM match_source_ids si WHERE si.match_id=l.match_id
             AND si.source_system='API_TENNIS' AND si.source_match_id IS NOT NULL
        ORDER BY si.match_source_id DESC LIMIT 1) AS event_key
FROM latest l JOIN matches m ON m.match_id=l.match_id
JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
JOIN tournaments t ON t.tournament_id=te.tournament_id
JOIN players p1 ON p1.player_id=l.player_1_id
JOIN players p2 ON p2.player_id=l.player_2_id
ORDER BY l.match_id
"""


def load(conn:psycopg.Connection)->list[dict[str,Any]]:
    rows=conn.execute(_SQL,{"ids":list(PENDING_IDS)}).fetchall()
    out=[]
    for row in rows:
        mid,p1,p2,tournament,tour,year,status,s1,s2,tk,event=row
        out.append({
            "canonical_match_id":mid,"player1":p1,"player2":p2,
            "tournament":tournament,"tour":tour,"year":year,
            "canonical_latest_status":status,
            "provider_player1_key":s1,"provider_player2_key":s2,
            "provider_tournament_key":tk,"provider_event_key":event,
            "manual_grade_required":True,
        })
    return out


def main()->int:
    with psycopg.connect(os.environ["TENNIS_DATABASE_URL"],
                         autocommit=True,connect_timeout=12) as conn:
        conn.execute("SET default_transaction_read_only=on")
        rows=load(conn)
        print("PENDING_IDENTITY_AUDIT "+json.dumps({
            "cases":rows,"expected_count":len(PENDING_IDS),
            "found":len(rows),"database_writes":0,
            "api_tennis_requests":0,"oddspapi_requests":0,
            "forced_results":0,
        },sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
