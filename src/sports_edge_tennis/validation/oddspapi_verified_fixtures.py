"""Persist ten genuinely verified OddsPapi tennis fixtures in an isolated DB.

Selection uses existing production raw observations only. The production
database is opened read-only; the isolated database is disposable. No provider
request is made and no production row is written.

Each selected raw payload already has a canonical match and both participant
mappings. The module seeds the minimum canonical identity into the isolated DB,
then exercises the real ``ingest_provider_observation`` path. Evidence records
the raw observation id/hash, provider and canonical identity, orientation,
prices, state, timestamps and the inserted snapshot ids.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter

import psycopg

from sports_edge_tennis.live.oddspapi import ODDSPAPI_PLAN_BOOKMAKERS
from sports_edge_tennis.odds.providers.oddspapi_tennis import (
    build_oddspapi_tennis_observation,
)
from sports_edge_tennis.odds.service import ingest_provider_observation

VERSION = "ODDSPAPI_VERIFIED_FIXTURES_V1"
BOOK_CLASS = {
    "pinnacle+30": ("SHARP", "PINNACLE"),
    "1xbet": ("OFFERED", "1XBET"),
    "bet365": ("OFFERED", "BET365"),
}
SELECT_SQL = """
WITH cand AS (
  SELECT r.raw_odds_observation_id, r.provider_event_id, r.received_at,
         b AS book, r.payload_json,
         (r.payload_json->'bookmakerOdds'->b->'markets'->'121'
            ->'outcomes'->'121'->'players'->'0'->>'price') AS p1,
         (r.payload_json->'bookmakerOdds'->b->'markets'->'121'
            ->'outcomes'->'122'->'players'->'0'->>'price') AS p2,
         (r.payload_json->'bookmakerOdds'->b->'markets'->'121'
            ->'outcomes'->'121'->'players'->'0'->>'active') AS a1,
         (r.payload_json->'bookmakerOdds'->b->'markets'->'121'
            ->'outcomes'->'122'->'players'->'0'->>'active') AS a2,
         (r.payload_json->>'startTime')::timestamptz AS provider_start,
         ms.match_id
  FROM raw_odds_observations r
  JOIN LATERAL jsonb_object_keys(r.payload_json->'bookmakerOdds') b ON TRUE
  JOIN match_source_ids ms
    ON ms.source_system='ODDSPAPI' AND ms.source_match_id=r.provider_event_id
  WHERE r.provider_code='ODDSPAPI'
    AND b = ANY(%(books)s)
    AND r.payload_json->'bookmakerOdds'->b->'markets' ? '121'
), classified AS (
  SELECT c.*, v.tour_scope::text AS tour_scope,
         v.competition_tier::text AS competition_tier,
         v.match_start_at, v.player_1_id, v.player_2_id,
         v.surface::text AS surface, v.round_code, v.best_of_sets,
         v.qualifying_flag, v.match_status::text AS match_status,
         v.match_version_id, v.source_observed_at, v.as_of
  FROM cand c
  JOIN LATERAL (
    SELECT match_version_id, tour_scope, competition_tier, match_start_at,
           player_1_id, player_2_id, surface, round_code, best_of_sets,
           qualifying_flag, match_status, source_observed_at, as_of
    FROM match_versions mv
    WHERE mv.match_id=c.match_id
      AND mv.as_of<=c.received_at AND mv.source_observed_at<=c.received_at
    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
    LIMIT 1
  ) v ON TRUE
  WHERE c.a1='true' AND c.a2='true'
    AND c.p1::numeric>1 AND c.p2::numeric>1
    AND c.provider_start > c.received_at
    AND v.match_start_at > c.received_at
    AND v.match_status='SCHEDULED'
    AND v.tour_scope IN ('ATP_MAIN','CHALLENGER')
), mapped AS (
  SELECT x.*,
         (SELECT player_id FROM player_source_ids ps
           WHERE ps.source_system='ODDSPAPI'
             AND ps.source_player_id=x.payload_json->>'participant1Id') AS player_1_map,
         (SELECT player_id FROM player_source_ids ps
           WHERE ps.source_system='ODDSPAPI'
             AND ps.source_player_id=x.payload_json->>'participant2Id') AS player_2_map
  FROM classified x
), one_per_fixture AS (
  SELECT DISTINCT ON (provider_event_id) *
  FROM mapped
  WHERE player_1_map IS NOT NULL AND player_2_map IS NOT NULL
  ORDER BY provider_event_id, received_at, raw_odds_observation_id
), ranked AS (
  SELECT *,
         row_number() OVER (PARTITION BY tour_scope
                            ORDER BY received_at, raw_odds_observation_id) AS rn,
         count(*) OVER (PARTITION BY tour_scope) AS tour_n
  FROM one_per_fixture
)
SELECT * FROM ranked
WHERE (tour_scope='ATP_MAIN' AND rn<=%(per_tour)s)
   OR (tour_scope='CHALLENGER' AND rn<=%(per_tour)s)
ORDER BY tour_scope, rn
"""


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")).hexdigest()


def select_candidates(conn: psycopg.Connection, *, per_tour: int = 5) -> list[dict]:
    rows = conn.execute(SELECT_SQL, {
        "books": list(ODDSPAPI_PLAN_BOOKMAKERS), "per_tour": per_tour,
    }).fetchall()
    columns = (
        "raw_odds_observation_id", "provider_event_id", "received_at", "book",
        "payload_json", "p1", "p2", "a1", "a2", "provider_start", "match_id",
        "tour_scope", "competition_tier", "match_start_at", "player_1_id",
        "player_2_id", "surface", "round_code", "best_of_sets",
        "qualifying_flag", "match_status", "match_version_id",
        "source_observed_at", "as_of", "player_1_map", "player_2_map", "rn",
        "tour_n",
    )
    return [dict(zip(columns, row)) for row in rows]


class IsolatedSeeder:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.players: dict[int, int] = {}
        self.tournaments: dict[int, int] = {}
        self.editions: dict[int, int] = {}
        self.matches: dict[int, int] = {}
        self.versions: dict[int, int] = {}
        self._bookmakers_seeded = False

    def _scalar(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchone()[0]

    def player(self, production_player_id: int, name: str) -> int:
        local = self.players.get(production_player_id)
        if local is None:
            local = self._scalar(
                "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
                (name,))
            self.players[production_player_id] = int(local)
        return int(local)

    def tournament(self, production: dict) -> int:
        key = int(production["tournament_id"])
        local = self.tournaments.get(key)
        if local is None:
            local = self._scalar(
                """INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
                   VALUES (%s, %s::tour_scope, %s::competition_tier)
                   RETURNING tournament_id""",
                (production["tournament_name"], production["tour_scope"],
                 production["competition_tier"]))
            self.tournaments[key] = int(local)
        return int(local)

    def edition(self, production: dict) -> int:
        key = int(production["tournament_edition_id"])
        local = self.editions.get(key)
        if local is None:
            tournament_id = self.tournament(production)
            local = self._scalar(
                """INSERT INTO tournament_editions
                   (tournament_id, season_year, edition_name, surface, start_date, end_date)
                   VALUES (%s, %s, %s, %s::tennis_surface, %s, %s)
                   RETURNING tournament_edition_id""",
                (tournament_id, int(production["season_year"]),
                 production["edition_name"], production["edition_surface"],
                 production["edition_start"], production["edition_end"]))
            self.editions[key] = int(local)
        return int(local)

    def match(self, fixture: dict, production: dict) -> tuple[int, int]:
        key = int(fixture["match_id"])
        local_match = self.matches.get(key)
        if local_match is None:
            edition_id = self.edition(production)
            local_match = self._scalar(
                "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
                (edition_id,))
            self.matches[key] = int(local_match)
        local_version = self.versions.get(key)
        if local_version is None:
            p1 = self.player(int(production["player_1_id"]),
                             production["player_1_name"])
            p2 = self.player(int(production["player_2_id"]),
                             production["player_2_name"])
            local_version = self._scalar(
                """INSERT INTO match_versions
                   (match_id, player_1_id, player_2_id, match_start_at,
                    tour_scope, competition_tier, surface, round_code,
                    best_of_sets, qualifying_flag, match_status,
                    source_observed_at, as_of)
                   VALUES (%s,%s,%s,%s,%s::tour_scope,%s::competition_tier,
                           %s::tennis_surface,%s,%s,%s,%s::match_status,%s,%s)
                   RETURNING match_version_id""",
                (int(local_match), p1, p2, fixture["match_start_at"],
                 production["tour_scope"], production["competition_tier"],
                 production["surface"], production["round_code"],
                 int(production["best_of_sets"]),
                 bool(production["qualifying_flag"]),
                 production["match_status"],
                 fixture["source_observed_at"], fixture["as_of"]))
            self.versions[key] = int(local_version)
        return int(local_match), int(local_version)

    def bookmakers(self) -> None:
        if self._bookmakers_seeded:
            return
        for slug, (classification, code) in BOOK_CLASS.items():
            self.conn.execute(
                """INSERT INTO provider_bookmaker_mappings
                   (provider_code, provider_bookmaker_key, mapping_version,
                    bookmaker_code, normalization_class, effective_from)
                   VALUES ('ODDSPAPI', %s, 1, %s, %s::odds_normalization_class,
                           '2026-01-01T00:00:00Z')""",
                (slug, code, classification))
        self._bookmakers_seeded = True


def _production_metadata(conn: psycopg.Connection, fixture: dict) -> dict:
    row = conn.execute(
        """SELECT m.match_id, te.tournament_edition_id, te.tournament_id,
                  t.canonical_name, t.tour_scope::text,
                  t.competition_tier::text, te.season_year, te.edition_name,
                  te.surface::text, te.start_date, te.end_date,
                  v.player_1_id, p1.canonical_name,
                  v.player_2_id, p2.canonical_name
           FROM matches m
           JOIN tournament_editions te ON te.tournament_edition_id=m.tournament_edition_id
           JOIN tournaments t ON t.tournament_id=te.tournament_id
           JOIN match_versions v ON v.match_version_id=%s
           JOIN players p1 ON p1.player_id=v.player_1_id
           JOIN players p2 ON p2.player_id=v.player_2_id
           WHERE m.match_id=%s""",
        (fixture["match_version_id"], fixture["match_id"]),
    ).fetchone()
    return {
        "tournament_edition_id": int(row[1]), "tournament_id": int(row[2]),
        "tournament_name": row[3], "tour_scope": row[4],
        "competition_tier": row[5], "season_year": int(row[6]),
        "edition_name": row[7], "edition_surface": row[8],
        "edition_start": row[9], "edition_end": row[10],
        "player_1_id": int(row[11]), "player_1_name": row[12],
        "player_2_id": int(row[13]), "player_2_name": row[14],
        "surface": fixture["surface"], "round_code": fixture["round_code"],
        "best_of_sets": int(fixture["best_of_sets"]),
        "qualifying_flag": bool(fixture["qualifying_flag"]),
        "match_status": fixture["match_status"],
    }


def _expected_selection(fixture: dict) -> tuple[str, str]:
    """Map provider participant order onto canonical PLAYER_1/PLAYER_2."""
    if int(fixture["player_1_map"]) == int(fixture["player_1_id"]):
        return "PLAYER_1", "PLAYER_2"
    return "PLAYER_2", "PLAYER_1"


def run(production: psycopg.Connection, isolated: psycopg.Connection, *,
        per_tour: int = 5) -> dict:
    fixtures = select_candidates(production, per_tour=per_tour)
    if len(fixtures) < 2 * per_tour:
        raise RuntimeError(
            f"only {len(fixtures)} eligible fixtures found; need {2 * per_tour}")
    seeder = IsolatedSeeder(isolated)
    seeder.bookmakers()
    evidence = []
    for fixture in fixtures:
        production_meta = _production_metadata(production, fixture)
        mapped = {int(fixture["player_1_map"]), int(fixture["player_2_map"])}
        expected_players = {
            int(production_meta["player_1_id"]),
            int(production_meta["player_2_id"]),
        }
        if mapped != expected_players:
            raise RuntimeError(
                f"fixture {fixture['provider_event_id']} participant mapping "
                "does not match the canonical match version")
        local_match_id, local_version_id = seeder.match(fixture, production_meta)
        isolated.execute(
            """INSERT INTO match_source_ids
               (match_id, match_version_id, source_system, source_match_id)
               VALUES (%s,%s,'ODDSPAPI',%s)""",
            (local_match_id, local_version_id, fixture["provider_event_id"]))
        for provider_id, production_player_id in (
                (fixture["payload_json"]["participant1Id"], fixture["player_1_map"]),
                (fixture["payload_json"]["participant2Id"], fixture["player_2_map"])):
            local_player_id = seeder.players.get(int(production_player_id))
            if local_player_id is None:
                raise RuntimeError("mapped player was not seeded")
            isolated.execute(
                """INSERT INTO player_source_ids
                   (player_id, source_system, source_player_id)
                   VALUES (%s,'ODDSPAPI',%s)
                   ON CONFLICT (source_system, source_player_id) DO NOTHING""",
                (int(local_player_id), str(provider_id)))
        response_id = (
            f"VERIFY:{fixture['raw_odds_observation_id']}:"
            f"{fixture['received_at'].isoformat()}")
        observation = build_oddspapi_tennis_observation(
            event_payload=fixture["payload_json"],
            received_at=fixture["received_at"],
            response_id=response_id,
            allowed_bookmakers=(fixture["book"],),
        )
        if len(observation.markets) != 1:
            raise RuntimeError(
                f"fixture {fixture['provider_event_id']} did not emit exactly one market")
        result = ingest_provider_observation(isolated, observation)
        if result.normalized_inserted != 2:
            raise RuntimeError(
                f"fixture {fixture['provider_event_id']} inserted "
                f"{result.normalized_inserted} quotes: {result.details}")
        rows = isolated.execute(
            """SELECT s.odds_snapshot_id, s.selection_type::text,
                      s.decimal_price, s.market_status::text,
                      s.source_observed_at, s.as_of, s.ingested_at,
                      s.bookmaker_code, s.source_system::text,
                      s.source_snapshot_id, s.raw_odds_observation_id
               FROM odds_snapshots s
               JOIN raw_odds_observations r
                 ON r.raw_odds_observation_id=s.raw_odds_observation_id
               WHERE r.provider_event_id=%s
               ORDER BY s.selection_type""",
            (fixture["provider_event_id"],)).fetchall()
        isolated_raw = isolated.execute(
            """SELECT raw_odds_observation_id, payload_hash
               FROM raw_odds_observations
               WHERE provider_code='ODDSPAPI' AND provider_event_id=%s
               ORDER BY raw_odds_observation_id DESC LIMIT 1""",
            (fixture["provider_event_id"],)).fetchone()
        expected = _expected_selection(fixture)
        by_side = {row[1]: row for row in rows}
        if set(by_side) != {"PLAYER_1", "PLAYER_2"}:
            raise RuntimeError("persisted snapshot is not two-sided")
        if float(by_side["PLAYER_1"][2]) != float(
                fixture["p1"] if expected[0] == "PLAYER_1" else fixture["p2"]):
            raise RuntimeError("persisted PLAYER_1 price is misoriented")
        if float(by_side["PLAYER_2"][2]) != float(
                fixture["p2"] if expected[1] == "PLAYER_2" else fixture["p1"]):
            raise RuntimeError("persisted PLAYER_2 price is misoriented")
        evidence.append({
            "raw_odds_observation_id": fixture["raw_odds_observation_id"],
            "raw_payload_sha256": _sha(fixture["payload_json"]),
            "provider_fixture_id": fixture["provider_event_id"],
            "canonical_match_id_production": fixture["match_id"],
            "canonical_match_id_isolated": local_match_id,
            "canonical_match_version_id_production": fixture["match_version_id"],
            "canonical_match_version_id_isolated": local_version_id,
            "participants": [
                {"provider_id": str(fixture["payload_json"]["participant1Id"]),
                 "provider_name": fixture["payload_json"].get("participant1Name"),
                 "canonical_id": int(fixture["player_1_map"]),
                 "canonical_name": production_meta["player_1_name"]
                 if int(fixture["player_1_map"]) == production_meta["player_1_id"]
                 else production_meta["player_2_name"]},
                {"provider_id": str(fixture["payload_json"]["participant2Id"]),
                 "provider_name": fixture["payload_json"].get("participant2Name"),
                 "canonical_id": int(fixture["player_2_map"]),
                 "canonical_name": production_meta["player_2_name"]
                 if int(fixture["player_2_map"]) == production_meta["player_2_id"]
                 else production_meta["player_1_name"]},
            ],
            "bookmaker": fixture["book"],
            "market_id": "121",
            "outcome_ids": ["121", "122"],
            "selection_orientation": {
                "provider_participant_1": expected[0],
                "provider_participant_2": expected[1],
            },
            "prices": {
                "PLAYER_1": float(by_side["PLAYER_1"][2]),
                "PLAYER_2": float(by_side["PLAYER_2"][2]),
            },
            "market_status": by_side["PLAYER_1"][3],
            "provider_start": fixture["provider_start"].isoformat(),
            "received_at_acquisition": fixture["received_at"].isoformat(),
            "source_observed_at": by_side["PLAYER_1"][4].isoformat(),
            "as_of": by_side["PLAYER_1"][5].isoformat(),
            "ingested_at": by_side["PLAYER_1"][6].isoformat(),
            "source_system": by_side["PLAYER_1"][8],
            "bookmaker_code": by_side["PLAYER_1"][7],
            "snapshot_ids": [int(row[0]) for row in rows],
            "raw_odds_observation_id_persisted": int(isolated_raw[0]),
            "isolated_raw_payload_hash": isolated_raw[1],
            "raw_snapshot_points_to_isolated_raw": (
                int(by_side["PLAYER_1"][10]) == int(isolated_raw[0])),
            "tour_scope": fixture["tour_scope"],
            "competition_tier": fixture["competition_tier"],
            "surface": fixture["surface"],
            "time_basis_note": (
                "received_at is the real production acquisition time of this "
                "raw poll. provider_observed_at is absent for the live shape, "
                "so observation_time is the receive time. No historical "
                "createdAt is relabelled as original availability."),
        })
    return {
        "version": VERSION,
        "status": "ISOLATED_DATABASE_VERIFICATION",
        "fixtures_requested": 2 * per_tour,
        "fixtures_verified": len(evidence),
        "fixtures_by_tour": dict(Counter(item["tour_scope"] for item in evidence)),
        "fixtures_by_bookmaker": dict(Counter(item["bookmaker"] for item in evidence)),
        "evidence": evidence,
        "checks": {
            "two_sided_persisted": all(
                item["prices"]["PLAYER_1"] > 1 and item["prices"]["PLAYER_2"] > 1
                for item in evidence),
            "orientation_verified": True,
            "raw_lineage_verified": all(
                item["raw_snapshot_points_to_isolated_raw"] for item in evidence),
            "all_snapshots_open": all(
                item["market_status"] == "OPEN" for item in evidence),
        },
        "limitations": [
            "The isolated database is disposable and is not production.",
            "These ten fixtures are a verification sample, not a coverage estimate.",
            "The live shape carries no provider-observed timestamp; receive time is used.",
            "No historical createdAt is treated as information available to production earlier.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--per-tour", type=int, default=5)
    args = parser.parse_args(argv)
    production_dsn = os.environ.get("TENNIS_DATABASE_URL")
    isolated_dsn = os.environ.get("ISOLATED_DATABASE_URL")
    if not production_dsn or not isolated_dsn:
        parser.error("TENNIS_DATABASE_URL and ISOLATED_DATABASE_URL are required")
    with psycopg.connect(production_dsn, autocommit=True, connect_timeout=20,
                         options="-c default_transaction_read_only=on") as prod, \
         psycopg.connect(isolated_dsn, autocommit=True, connect_timeout=20) as iso:
        report = run(prod, iso, per_tour=args.per_tour)
    payload = json.dumps(report, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
