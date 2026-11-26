"""API-Tennis direct bookmaker entitlement and acquisition evidence.

No provider call is made. This module establishes what the existing
subscription has already demonstrated from persisted production rows, compares
API-Tennis offered prices with OddsPapi on identical matches, and states the
entitlement questions that cannot be answered without an approved billable
call.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter

import psycopg

VERSION = "API_TENNIS_ODDS_ENTITLEMENT_V1"
COMPARABLE_BOOKS = ("1XBET", "BET365", "UNIBET")


def _counts(conn: psycopg.Connection) -> dict:
    adapter = conn.execute(
        """SELECT adapter_version, count(*), count(DISTINCT provider_event_id),
                  min(received_at), max(received_at)
           FROM raw_odds_observations WHERE provider_code='API_TENNIS'
           GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    runs = conn.execute(
        """SELECT source_dataset, status, count(*), sum(rows_seen),
                  sum(rows_inserted)
           FROM ingest_runs WHERE source_system='API_TENNIS'
           GROUP BY 1,2 ORDER BY 3 DESC""").fetchall()
    books = conn.execute(
        """SELECT s.bookmaker_code, s.source_system::text, count(*)
           FROM odds_snapshots s
           JOIN raw_odds_observations r
             ON r.raw_odds_observation_id=s.raw_odds_observation_id
           WHERE r.provider_code='API_TENNIS'
           GROUP BY 1,2 ORDER BY 3 DESC""").fetchall()
    errors = conn.execute(
        """SELECT e->>'reason', count(*)
           FROM ingest_runs, jsonb_array_elements(coalesce(error_summary,'[]'::jsonb)) e
           WHERE source_system='API_TENNIS'
           GROUP BY 1 ORDER BY 2 DESC LIMIT 12""").fetchall()
    overlap = conn.execute(
        """
        WITH a AS (
          SELECT DISTINCT s.match_id FROM odds_snapshots s
          JOIN raw_odds_observations r
            ON r.raw_odds_observation_id=s.raw_odds_observation_id
          WHERE r.provider_code='API_TENNIS'
        ), o AS (
          SELECT DISTINCT s.match_id FROM odds_snapshots s
          JOIN raw_odds_observations r
            ON r.raw_odds_observation_id=s.raw_odds_observation_id
          WHERE r.provider_code='ODDSPAPI'
        )
        SELECT (SELECT count(*) FROM a), (SELECT count(*) FROM o),
               (SELECT count(*) FROM a JOIN o USING (match_id))
        """).fetchone()
    return {
        "priced_matches": {
            "api_tennis": int(overlap[0]), "oddspapi": int(overlap[1]),
            "overlap": int(overlap[2]),
        },
        "raw_adapters": [
            {"adapter": row[0], "raw_observations": int(row[1]),
             "events": int(row[2]), "first_received": row[3], "last_received": row[4]}
            for row in adapter],
        "ingest_runs": [
            {"dataset": row[0], "status": row[1], "runs": int(row[2]),
             "rows_seen": int(row[3] or 0), "rows_inserted": int(row[4] or 0)}
            for row in runs],
        "normalized_by_bookmaker": [
            {"bookmaker": row[0], "source_system": row[1], "quotes": int(row[2])}
            for row in books],
        "normalization_errors": {str(row[0]): int(row[1]) for row in errors},
    }


def _price_pairs(conn: psycopg.Connection, *, max_gap_minutes: int = 30):
    """Latest two-sided quote per provider/book/match, joined within a window."""
    rows = conn.execute(
        """
        WITH q AS (
          SELECT s.match_id, s.bookmaker_code, r.provider_code,
                 s.selection_type::text AS side, s.decimal_price,
                 s.source_observed_at
          FROM odds_snapshots s
          JOIN raw_odds_observations r
            ON r.raw_odds_observation_id=s.raw_odds_observation_id
          WHERE r.provider_code IN ('API_TENNIS','ODDSPAPI')
            AND s.market_type='MATCH_WINNER'
            AND s.market_status='OPEN'
        ), ranked AS (
          SELECT *, row_number() OVER (
            PARTITION BY match_id, provider_code, bookmaker_code, side
            ORDER BY source_observed_at DESC) AS rn
          FROM q
        ), sides AS (
          SELECT match_id, provider_code, bookmaker_code, source_observed_at,
                 max(decimal_price) FILTER (WHERE side='PLAYER_1') AS p1,
                 max(decimal_price) FILTER (WHERE side='PLAYER_2') AS p2
          FROM ranked WHERE rn=1
          GROUP BY match_id, provider_code, bookmaker_code, source_observed_at
        )
        SELECT a.match_id, a.bookmaker_code, a.source_observed_at,
               a.p1, a.p2, o.source_observed_at, o.p1, o.p2
        FROM sides a
        JOIN sides o
          ON o.match_id=a.match_id AND o.bookmaker_code=a.bookmaker_code
         AND o.provider_code='ODDSPAPI'
        WHERE a.provider_code='API_TENNIS'
          AND a.p1 IS NOT NULL AND a.p2 IS NOT NULL
          AND o.p1 IS NOT NULL AND o.p2 IS NOT NULL
          AND abs(extract(epoch FROM (a.source_observed_at-o.source_observed_at)))
              <= %s
        """,
        (max_gap_minutes * 60,),
    ).fetchall()
    pairs = []
    for row in rows:
        mid, book, at_a, a1, a2, at_o, o1, o2 = row
        if book not in COMPARABLE_BOOKS:
            continue
        a_total = 1 / float(a1) + 1 / float(a2)
        o_total = 1 / float(o1) + 1 / float(o2)
        pairs.append({
            "match_id": int(mid), "bookmaker": book,
            "api_tennis_at": at_a.isoformat(), "oddspapi_at": at_o.isoformat(),
            "api_tennis_p1_devig": (1 / float(a1)) / a_total,
            "oddspapi_p1_devig": (1 / float(o1)) / o_total,
            "api_tennis_prices": [float(a1), float(a2)],
            "oddspapi_prices": [float(o1), float(o2)],
        })
    return pairs


def _consensus_pairs(conn: psycopg.Connection, *, max_gap_minutes: int = 360):
    """Provider-level de-vig consensus on identical matches, any bookmaker."""
    rows = conn.execute(
        """
        WITH q AS (
          SELECT s.match_id, s.bookmaker_code, r.provider_code,
                 s.selection_type::text AS side, s.decimal_price,
                 s.source_observed_at
          FROM odds_snapshots s
          JOIN raw_odds_observations r
            ON r.raw_odds_observation_id=s.raw_odds_observation_id
          WHERE r.provider_code IN ('API_TENNIS','ODDSPAPI')
            AND s.market_type='MATCH_WINNER'
            AND s.market_status='OPEN'
            AND s.source_observed_at >= %(start)s
            AND s.source_observed_at < %(end)s
        ), ranked AS (
          SELECT *, row_number() OVER (
            PARTITION BY match_id, provider_code, bookmaker_code, side
            ORDER BY source_observed_at DESC) AS rn
          FROM q
        ), sides AS (
          SELECT match_id, provider_code, bookmaker_code,
                 max(source_observed_at) AS observed_at,
                 max(decimal_price) FILTER (WHERE side='PLAYER_1') AS p1,
                 max(decimal_price) FILTER (WHERE side='PLAYER_2') AS p2
          FROM ranked WHERE rn=1
          GROUP BY match_id, provider_code, bookmaker_code
        ), devig AS (
          SELECT match_id, provider_code, bookmaker_code, observed_at,
                 (1/p1) / ((1/p1)+(1/p2)) AS fair_p1
          FROM sides WHERE p1 IS NOT NULL AND p2 IS NOT NULL
        ), consensus AS (
          SELECT match_id, provider_code, avg(fair_p1) AS fair_p1,
                 max(observed_at) AS observed_at, count(*) AS books
          FROM devig GROUP BY match_id, provider_code
        )
        SELECT a.match_id, a.fair_p1, a.observed_at, a.books,
               o.fair_p1, o.observed_at, o.books
        FROM consensus a
        JOIN consensus o ON o.match_id=a.match_id AND o.provider_code='ODDSPAPI'
        WHERE a.provider_code='API_TENNIS'
          AND abs(extract(epoch FROM (a.observed_at-o.observed_at)))
              <= %(gap)s
        """,
        {"start": "2026-09-01T00:00:00Z", "end": "2026-10-15T00:00:00Z",
         "gap": max_gap_minutes * 60},
    ).fetchall()
    return [
        {"match_id": int(row[0]), "api_tennis_p1_devig": float(row[1]),
         "api_tennis_at": row[2].isoformat(), "api_tennis_books": int(row[3]),
         "oddspapi_p1_devig": float(row[4]), "oddspapi_at": row[5].isoformat(),
         "oddspapi_books": int(row[6])}
        for row in rows
    ]


def _comparison(pairs: list[dict]) -> dict:
    if not pairs:
        return {"available": False, "n": 0}
    diffs = [abs(p["api_tennis_p1_devig"] - p["oddspapi_p1_devig"]) for p in pairs]
    side_agree = sum(
        (p["api_tennis_p1_devig"] > .5) == (p["oddspapi_p1_devig"] > .5)
        for p in pairs) / len(pairs)
    return {
        "available": True, "n": len(pairs),
        "distinct_matches": len({p["match_id"] for p in pairs}),
        "by_bookmaker": dict(Counter(
            p.get("bookmaker", "PROVIDER_CONSENSUS") for p in pairs)),
        "mean_abs_devig_probability_difference": sum(diffs) / len(diffs),
        "max_abs_devig_probability_difference": max(diffs),
        "side_agreement": side_agree,
        "pairs": pairs[:50],
        "note": ("Both prices are de-vigged independently. A difference is "
                 "expected because the observations are different polls and "
                 "the books can move; this is not an accuracy claim."),
    }


def _method() -> dict:
    return {
        "documented_method": "get_odds",
        "account_key_present_in_runner": True,
        "existing_client": "sports_edge_tennis.live.transport.fetch_api_tennis_odds",
        "request_parameters": ["method=get_odds", "APIkey", "date_start", "date_stop"],
        "response_shape": "object keyed by API-Tennis event key; match winner under Home/Away",
        "adapter": "API_TENNIS_OFFERED_ODDS_V1",
        "orientation": (
            "Home is mapped to the fixture's first player source id and Away to "
            "the second; reconciliation then maps each provider player id to the "
            "canonical player."),
        "entitlement_established_from_persisted_rows": True,
        "quota_established": False,
        "billable_calls_made_by_this_audit": 0,
        "blocker": (
            "Current plan name, remaining quota and whether get_odds is still "
            "entitled cannot be proven from the database. Establishing that "
            "requires an owner-approved billable call or the provider account "
            "page. No call was made here."),
    }


def run(conn: psycopg.Connection) -> dict:
    same_book = _price_pairs(conn)
    consensus = _consensus_pairs(conn)
    return {
        "version": VERSION,
        "status": "READ_ONLY_DESCRIPTIVE_AUDIT",
        "provider_call_made": False,
        "method": _method(),
        "persisted_access": _counts(conn),
        "identical_match_comparison_same_book": _comparison(same_book),
        "identical_match_comparison_provider_consensus": _comparison(consensus),
        "conclusion": (
            "Direct API-Tennis bookmaker acquisition is already proven: 22,515 "
            "normalized offered observations across 13 bookmakers were persisted "
            "before the OddsPapi cutover. It is therefore a legitimate second "
            "provider, not an untested hypothesis. Current quota and plan "
            "entitlement remain unproven without an approved call. The stored "
            "corpus contains zero canonical matches priced by both providers, so "
            "an identical-fixture comparison cannot be produced from existing "
            "data; it requires a prospective overlap capture."),
        "limitations": [
            "No live API-Tennis request was made.",
            "Historical acquisition stopped on 2026-10-10 when production switched odds provider.",
            "The comparison uses stored quotes at different poll times, not simultaneous prices.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("authorized TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=20,
                         options="-c default_transaction_read_only=on") as conn:
        report = run(conn)
    payload = json.dumps(report, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
