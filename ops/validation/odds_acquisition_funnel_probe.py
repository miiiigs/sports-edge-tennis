"""Read-only OddsPapi acquisition-funnel reconciliation (no provider calls).

Reconciles the earlier 23 qualifying pairs among the frozen 1,342 original Model
Picks with the latest identity/price timing audits and the strict independent
evidence rules. It separates:

  * persisted provider fixture links;
  * independently verified identities (requires an independent tournament
    mapping, a populated edition window and bounded start drift);
  * valid both-sided match-winner markets;
  * observations and receipts before first issue;
  * late ingestion and provider-time backfills;
  * the counterfactual pair count if the mapping gate were satisfied.

Safety: read-only, aggregate output, no licensed row-level export, no provider
request, no write and no threshold change.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.validation.odds_identity_market_export import extract_one
from sports_edge_tennis.validation.odds_identity_market_funnel import audit
from sports_edge_tennis.validation.original_preissue_odds_gap_audit import (
    load_cohort,
)
from sports_edge_tennis.validation.original_preissue_odds_gap_audit import (
    run as legacy_run,
)

VERSION = "ODDSPAPI_ACQUISITION_FUNNEL_V1"
START = datetime(2026, 9, 10, tzinfo=UTC)
REPORT_AT = datetime(2026, 11, 22, 15, 28, 44, tzinfo=UTC)
BOTH_SIDED_STATES = {
    "QUALIFIED", "LATE_QUOTATION", "STALE_QUOTATION", "SUSPENDED_MARKET",
    "NON_OPEN_MARKET", "UNSYNCHRONIZED_MARKET", "INVALID_PRICE",
    "AMBIGUOUS_QUOTE_STATE",
}


def _stamp(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value))


def timing_intersection(conn: psycopg.Connection, picks: list[dict]) -> dict:
    values = [(p["match_id"], max(p["model_as_of"], p["prediction_finalized_at"]))
              for p in picks]
    rows = conn.execute(
        """
        WITH picks(match_id, cutoff) AS (
            SELECT * FROM unnest(%s::bigint[], %s::timestamptz[])
        )
        SELECT
            count(*) AS cohort,
            count(identity.created_at) AS with_persisted_identity,
            count(*) FILTER (WHERE identity.created_at <= picks.cutoff) AS identity_at_issue,
            count(price.received_at) AS with_raw_price,
            count(*) FILTER (WHERE price.received_at <= picks.cutoff) AS price_received_at_issue,
            count(*) FILTER (WHERE price.observation_time <= picks.cutoff) AS price_observed_at_issue
        FROM picks
        LEFT JOIN LATERAL (
            SELECT min(s.created_at) AS created_at
            FROM match_source_ids s
            WHERE s.match_id = picks.match_id
              AND s.source_system = 'ODDSPAPI'
              AND s.source_match_id IS NOT NULL
        ) identity ON TRUE
        LEFT JOIN LATERAL (
            SELECT min(r.received_at) AS received_at,
                   min(r.observation_time) AS observation_time
            FROM raw_odds_observations r
            JOIN match_source_ids s
              ON s.source_system = 'ODDSPAPI'
             AND s.source_match_id = r.payload_json->>'fixtureId'
            WHERE s.match_id = picks.match_id
              AND r.provider_code = 'ODDSPAPI'
        ) price ON TRUE
        """,
        ([v[0] for v in values], [v[1] for v in values]),
    ).fetchone()
    return {
        "cohort": rows[0], "with_persisted_identity": rows[1],
        "identity_at_issue": rows[2], "with_raw_price": rows[3],
        "price_received_at_issue": rows[4],
        "price_observed_at_issue": rows[5],
        "interpretation": (
            "Persisted identity and raw price counts are current rows. "
            "'at issue' requires the provider link or receipt before the frozen "
            "first-issue cutoff; later links/prices cannot be promoted."
        ),
    }


def raw_survival(conn: psycopg.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM raw_odds_observations WHERE provider_code='ODDSPAPI'),
            (SELECT count(*) FROM raw_odds_observations r
              WHERE r.provider_code='ODDSPAPI'
                AND NOT EXISTS (SELECT 1 FROM match_source_ids s
                    WHERE s.source_system='ODDSPAPI'
                      AND s.source_match_id=r.payload_json->>'fixtureId')),
            (SELECT count(*) FROM oddspapi_event_identity_cache),
            (SELECT count(*) FROM odds_snapshots WHERE source_system='ODDSPAPI'),
            (SELECT count(*) FROM match_source_ids WHERE source_system='ODDSPAPI'),
            (SELECT count(*) FROM tournament_source_ids WHERE source_system='ODDSPAPI'),
            (SELECT count(*) FROM tournament_editions WHERE end_date IS NULL),
            (SELECT count(*) FROM tournament_editions)
        """
    ).fetchone()
    return {
        "raw_observations": rows[0],
        "raw_observations_without_persisted_fixture_link": rows[1],
        "identity_cache_rows": rows[2],
        "normalized_oddspapi_snapshots": rows[3],
        "persisted_oddspapi_match_links": rows[4],
        "independent_tournament_mappings": rows[5],
        "editions_missing_end_date": rows[6],
        "editions": rows[7],
    }


def drift(conn: psycopg.Connection, picks: list[dict]) -> dict:
    ids = [p["match_id"] for p in picks]
    row = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (s.match_id)
                   s.match_id,
                   (r.payload_json->>'startTime')::timestamptz AS provider_start,
                   r.observation_time
            FROM raw_odds_observations r
            JOIN match_source_ids s
              ON s.source_system='ODDSPAPI'
             AND s.source_match_id=r.payload_json->>'fixtureId'
            WHERE r.provider_code='ODDSPAPI' AND s.match_id=ANY(%s)
            ORDER BY s.match_id, r.observation_time DESC,
                     r.raw_odds_observation_id DESC
        )
        SELECT count(*),
               min(extract(epoch FROM (provider_start - v.match_start_at))/60),
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY extract(epoch FROM (provider_start - v.match_start_at))/60),
               max(extract(epoch FROM (provider_start - v.match_start_at))/60),
               count(*) FILTER (WHERE abs(extract(epoch FROM
                   (provider_start - v.match_start_at))/60) <= 30)
        FROM latest
        JOIN LATERAL (
            SELECT mv.match_start_at FROM match_versions mv
            WHERE mv.match_id=latest.match_id
              AND mv.as_of<=latest.observation_time
              AND mv.source_observed_at<=latest.observation_time
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC LIMIT 1
        ) v ON TRUE
        """,
        (ids,),
    ).fetchone()
    return {
        "matched_fixtures": row[0],
        "min_minutes": float(row[1]) if row[1] is not None else None,
        "median_minutes": float(row[2]) if row[2] is not None else None,
        "max_minutes": float(row[3]) if row[3] is not None else None,
        "within_30": row[4],
    }


def classify_fixture(data: dict | None, counts: Counter) -> dict:
    if data is None:
        return {"identity": "MISSING_OR_INELIGIBLE", "books": {}, **counts}
    fixture = data["fixtures"][0]
    cutoff = _stamp(fixture["cutoff"])
    result = audit(data)
    strict_identity = result["identity_counts"]
    strict_books = result["book_states"]
    # Counterfactual: assume the independent tournament mapping gate is
    # satisfied. Everything else (24h identity age, player orientation, start
    # drift, quote clocks, both sides, de-vig range) is unchanged.
    relaxed = json.loads(json.dumps(data))
    relaxed["fixtures"][0]["verified_tournament_ids"] = sorted({
        int(e["tournament_id"]) for e in data["events"]})
    relaxed_result = audit(relaxed)
    quotes = data["quotes"]
    observed_before = sum(
        1 for q in quotes if q.get("observed_at") and _stamp(q["observed_at"]) <= cutoff)
    received_before = sum(
        1 for q in quotes if q.get("raw_received_at")
        and _stamp(q["raw_received_at"]) <= cutoff)
    late_ingested = sum(
        1 for q in quotes
        if q.get("observed_at") and q.get("ingested_at")
        and _stamp(q["observed_at"]) <= cutoff < _stamp(q["ingested_at"]))
    provider_time = sum(1 for q in quotes if q.get("time_basis") == "PROVIDER_TIME")
    both_sided = sum(
        1 for states in strict_books.values()
        if any(state in BOTH_SIDED_STATES for state in states)
    )
    both_sided_relaxed = sum(
        1 for states in relaxed_result["book_states"].values()
        if any(state in BOTH_SIDED_STATES for state in states)
    )
    return {
        "identity": "VERIFIED" if strict_identity.get("VERIFIED_IDENTITY") else next(
            iter(strict_identity), "UNKNOWN"),
        "identity_counts": strict_identity,
        "relaxed_identity_counts": relaxed_result["identity_counts"],
        "books": strict_books,
        "relaxed_books": relaxed_result["book_states"],
        "qualified": int(result["qualified_any_book"] > 0),
        "relaxed_qualified": int(relaxed_result["qualified_any_book"] > 0),
        "both_sided_any_book": int(both_sided > 0),
        "relaxed_both_sided_any_book": int(both_sided_relaxed > 0),
        "quotes": len(quotes),
        "quotes_observed_before_issue": observed_before,
        "quotes_received_before_issue": received_before,
        "quotes_late_ingested": late_ingested,
        "quotes_provider_time_basis": provider_time,
        "raw_observations": counts.get("raw_observations", 0),
        "fixture_with_raw_observation": counts.get("fixture_with_raw_observation", 0),
        "invalid_latest_raw_identity": counts.get("invalid_latest_raw_identity", 0),
        "raw_book_observations": counts.get("raw_book_observations", 0),
        "raw_suspension_observations": counts.get("raw_suspension_observations", 0),
        "persistent_mapping": counts.get("persistent_mapping", 0),
        "independent_tournament_mapping": counts.get("independent_tournament_mapping", 0),
    }


def direct_stages(conn: psycopg.Connection, picks: list[dict]) -> dict:
    """Exact cohort counts in one query, independent of the per-fixture audit."""
    ids = [p["match_id"] for p in picks]
    cutoffs = [max(p["model_as_of"], p["prediction_finalized_at"]) for p in picks]
    row = conn.execute(
        """
        WITH picks(match_id, cutoff) AS (
            SELECT * FROM unnest(%s::bigint[], %s::timestamptz[])
        ),
        ident AS (
            SELECT p.match_id, p.cutoff, min(s.created_at) AS created
            FROM picks p
            LEFT JOIN match_source_ids s
              ON s.match_id = p.match_id
             AND s.source_system = 'ODDSPAPI'
             AND s.source_match_id IS NOT NULL
            GROUP BY 1, 2
        ),
        rawp AS (
            SELECT p.match_id, p.cutoff, min(r.received_at) AS recv,
                   min(r.observation_time) AS obs, count(r.*) AS n
            FROM picks p
            LEFT JOIN match_source_ids s
              ON s.match_id = p.match_id AND s.source_system = 'ODDSPAPI'
            LEFT JOIN raw_odds_observations r
              ON r.provider_code = 'ODDSPAPI'
             AND r.payload_json->>'fixtureId' = s.source_match_id
            GROUP BY 1, 2
        ),
        qs AS (
            SELECT p.match_id, p.cutoff, o.source_system::text AS src,
                   o.bookmaker_code AS bk, o.selection_type::text AS side,
                   o.market_status::text AS st, o.source_observed_at AS obs,
                   o.as_of AS a, o.ingested_at AS ing
            FROM picks p
            JOIN odds_snapshots o
              ON o.match_id = p.match_id
             AND o.market_type = 'MATCH_WINNER'
             AND o.selection_type IN ('PLAYER_1','PLAYER_2')
        ),
        two AS (
            SELECT match_id, src, bk FROM qs
            GROUP BY 1, 2, 3 HAVING count(DISTINCT side) = 2
        ),
        two_pre AS (
            SELECT q.match_id, q.src, q.bk
            FROM qs q JOIN picks p ON p.match_id = q.match_id
            WHERE q.st = 'OPEN'
              AND q.obs <= p.cutoff AND q.a <= p.cutoff AND q.ing <= p.cutoff
              AND q.obs >= p.cutoff - interval '6 hours'
            GROUP BY 1, 2, 3 HAVING count(DISTINCT q.side) = 2
        )
        SELECT
          (SELECT count(*) FROM picks),
          (SELECT count(*) FROM ident WHERE created IS NOT NULL),
          (SELECT count(*) FROM ident WHERE created <= cutoff),
          (SELECT count(*) FROM rawp WHERE n > 0),
          (SELECT count(*) FROM rawp WHERE recv <= cutoff),
          (SELECT count(*) FROM rawp WHERE obs <= cutoff),
          (SELECT count(DISTINCT match_id) FROM qs),
          (SELECT count(DISTINCT match_id) FROM two),
          (SELECT count(DISTINCT match_id) FROM two_pre),
          (SELECT count(DISTINCT q.match_id) FROM qs q
             JOIN picks p ON p.match_id = q.match_id WHERE q.obs <= p.cutoff),
          (SELECT count(DISTINCT q.match_id) FROM qs q
             JOIN picks p ON p.match_id = q.match_id WHERE q.ing <= p.cutoff),
          (SELECT count(DISTINCT q.match_id) FROM qs q
             JOIN picks p ON p.match_id = q.match_id
            WHERE q.obs <= p.cutoff AND q.ing > p.cutoff),
          (SELECT count(DISTINCT q.match_id) FROM qs q
             JOIN picks p ON p.match_id = q.match_id
            WHERE q.obs < p.cutoff - interval '6 hours'),
          (SELECT count(*) FROM picks p
            WHERE NOT EXISTS (SELECT 1 FROM two WHERE two.match_id = p.match_id)
              AND NOT EXISTS (SELECT 1 FROM ident i
                              WHERE i.match_id = p.match_id AND i.created IS NOT NULL))
        """,
        (ids, cutoffs),
    ).fetchone()
    keys = (
        "original_predictions", "identity_current", "identity_at_issue",
        "raw_price_any", "price_received_at_issue", "price_observed_at_issue",
        "any_quote", "two_sided_any_book", "two_sided_preissue",
        "observed_before_issue", "ingested_before_issue", "late_ingested",
        "backfilled_observation", "unmatched_no_quote_no_identity",
    )
    return dict(zip(keys, row))


def fast_probe(conn: psycopg.Connection) -> dict:
    """Aggregate-only cohort reconciliation in a few set-based queries."""
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        raise SystemExit("read-only guard failed")
    picks = load_cohort(conn, start=START, end=REPORT_AT, as_of=REPORT_AT)
    legacy = legacy_run(conn, start=START, end=REPORT_AT, as_of=REPORT_AT,
                        sample_size=100)
    report = {
        "version": VERSION,
        "mode": "fast_aggregate",
        "report_as_of_utc": REPORT_AT.isoformat(),
        "cohort": {
            "original_predictions": legacy["original_selection_rows"],
            "by_pick_type": dict(sorted(Counter(
                p["pick_type"] for p in picks).items())),
        },
        "direct_stages": direct_stages(conn, picks),
        "timing_intersection": timing_intersection(conn, picks),
        "legacy_audit": {
            "fully_qualifying_live_preissue_two_sided": legacy[
                "fully_qualifying_live_preissue_two_sided"],
            "unqualified_original_selections": legacy[
                "unqualified_original_selections"],
            "failure_categories": legacy["failure_categories"],
            "qualifying_pair_count_observations_are_non_oddspapi": (
                "The 23 legacy pairs are SHARP/OFFERED odds_snapshots: "
                "21 OFFERED (SBO/1XBET/PNCL/BETANO/BET365) and 2 SHARP "
                "PINNACLE. Only one of the 23 currently has an OddsPapi "
                "identity and raw price; at issue, exactly one did."),
        },
        "raw_survival": raw_survival(conn),
        "provider_start_drift": drift(conn, picks),
        "provider_calls": 0,
        "database_writes": False,
        "research_only": True,
    }
    return report


def probe(conn: psycopg.Connection, *, limit: int | None = None) -> dict:
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        raise SystemExit("read-only guard failed")
    picks = load_cohort(conn, start=START, end=REPORT_AT, as_of=REPORT_AT)
    if limit:
        picks = picks[:limit]
    identity_ids = {
        int(row[0]) for row in conn.execute(
            "SELECT DISTINCT match_id FROM match_source_ids "
            "WHERE source_system='ODDSPAPI' AND match_id=ANY(%s)",
            ([p["match_id"] for p in picks],)).fetchall()
    }
    counters: Counter = Counter()
    identities: Counter = Counter()
    books: Counter = Counter()
    relaxed_books: Counter = Counter()
    fixtures: Counter = Counter()
    rows = []
    for pick in picks:
        issue = max(pick["model_as_of"], pick["prediction_finalized_at"])
        if pick["match_id"] not in identity_ids:
            fixtures["skipped_no_persisted_identity"] += 1
            identities["MISSING_IDENTITY"] += 1
            continue
        data, counts = extract_one(conn, pick["match_id"], issue, REPORT_AT)
        counters.update(counts)
        if data is None:
            fixtures["missing_or_ineligible"] += 1
            continue
        row = classify_fixture(data, counts)
        rows.append(row)
        fixtures["audited"] += 1
        identities.update(row["identity_counts"])
        for book, states in row["books"].items():
            books.update({f"{book}:{state}": n for state, n in states.items()})
        for book, states in row["relaxed_books"].items():
            relaxed_books.update({f"{book}:{state}": n for state, n in states.items()})
        for key in ("qualified", "relaxed_qualified", "both_sided_any_book",
                    "relaxed_both_sided_any_book"):
            fixtures[key] += row[key]
        if row["quotes_observed_before_issue"]:
            fixtures["fixtures_with_quote_observed_before_issue"] += 1
        if row["quotes_received_before_issue"]:
            fixtures["fixtures_with_quote_received_before_issue"] += 1
        if row["quotes_late_ingested"]:
            fixtures["fixtures_with_late_ingested_quote"] += 1
    legacy = legacy_run(conn, start=START, end=REPORT_AT, as_of=REPORT_AT,
                        sample_size=100)
    return {
        "version": VERSION,
        "report_as_of_utc": REPORT_AT.isoformat(),
        "cohort": {
            "original_predictions": legacy["original_selection_rows"],
            "limit": limit,
            "by_pick_type": dict(sorted(Counter(
                p["pick_type"] for p in picks).items())),
        },
        "timing_intersection": timing_intersection(conn, picks),
        "direct_stages": direct_stages(conn, picks),
        "fixture_stages": dict(sorted(fixtures.items())),
        "extractor_counters": dict(sorted(counters.items())),
        "strict_identity_counts": dict(sorted(identities.items())),
        "strict_book_states": dict(sorted(books.items())),
        "counterfactual_mapping_book_states": dict(sorted(relaxed_books.items())),
        "legacy_audit": {
            "fully_qualifying_live_preissue_two_sided": legacy[
                "fully_qualifying_live_preissue_two_sided"],
            "unqualified_original_selections": legacy[
                "unqualified_original_selections"],
            "failure_categories": legacy["failure_categories"],
        },
        "raw_survival": raw_survival(conn),
        "provider_start_drift": drift(conn, picks),
        "interpretation": {
            "strict_qualifying_pairs": (
                "0 unless a row in fixture_stages says otherwise. The strict "
                "gate requires an independent ODDSPAPI tournament mapping and a "
                "populated edition end date; neither exists."),
            "counterfactual": (
                "counterfactual_mapping_book_states removes only the independent "
                "tournament-mapping gate. It is not recovered coverage."),
            "legacy_23": (
                "The legacy audit does not require an independent tournament "
                "mapping. Its 23 pairs are pre-issue stored live quotes, not "
                "independently verified provider identities."),
        },
        "provider_calls": 0,
        "database_writes": False,
        "research_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("authorized TENNIS_DATABASE_URL required")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=15) as conn:
        report = (fast_probe(conn) if args.fast
                  else probe(conn, limit=args.limit))
        conn.execute("ROLLBACK")
    payload = json.dumps(report, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
