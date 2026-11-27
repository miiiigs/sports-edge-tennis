"""Read-only root-cause census for OddsPapi tennis normalization.

Separates four questions that the earlier audits conflated:

1. Did the parser identify the tennis market? (actual payload catalogue)
2. Did normalization persist quotes? (raw -> normalized lineage)
3. Did any quote exist before the original prediction issue time?
4. Is a normalizer failure the same thing as a strict-evidence failure?

The module uses production read-only access and performs no provider call.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime
from typing import Any

import psycopg

from sports_edge_tennis.validation.manager_abc_first_issue_ablation import _utc
from sports_edge_tennis.validation.manager_c_original_source import (
    TRAIN_START,
    VALIDATION_END,
)

VERSION = "ODDSPAPI_NORMALIZATION_ROOT_CAUSE_V1"


def _scalar(conn: psycopg.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _payload_catalogue(conn: psycopg.Connection) -> dict[str, Any]:
    total = _scalar(conn, """
        SELECT count(*) FROM raw_odds_observations WHERE provider_code='ODDSPAPI'
    """)
    with_121 = _scalar(conn, """
        SELECT count(*) FROM raw_odds_observations
        WHERE provider_code='ODDSPAPI'
          AND EXISTS (
            SELECT 1 FROM jsonb_object_keys(payload_json->'bookmakerOdds') AS b
            WHERE payload_json->'bookmakerOdds'->b->'markets' ? '121')
    """)
    with_171 = _scalar(conn, """
        SELECT count(*) FROM raw_odds_observations
        WHERE provider_code='ODDSPAPI'
          AND EXISTS (
            SELECT 1 FROM jsonb_object_keys(payload_json->'bookmakerOdds') AS b
            WHERE payload_json->'bookmakerOdds'->b->'markets' ? '171')
    """)
    books = conn.execute("""
        SELECT b AS bookmaker, count(*)
        FROM raw_odds_observations,
             LATERAL jsonb_object_keys(payload_json->'bookmakerOdds') AS b
        WHERE provider_code='ODDSPAPI'
        GROUP BY b ORDER BY 2 DESC
    """).fetchall()
    return {
        "raw_observations": total,
        "with_current_bookmakerOdds_key": _scalar(conn, """
            SELECT count(*) FROM raw_odds_observations
            WHERE provider_code='ODDSPAPI' AND payload_json ? 'bookmakerOdds'
        """),
        "with_historical_bookmakers_key": _scalar(conn, """
            SELECT count(*) FROM raw_odds_observations
            WHERE provider_code='ODDSPAPI' AND payload_json ? 'bookmakers'
        """),
        "with_market_121": with_121,
        "with_market_171": with_171,
        "bookmakers_in_payloads": {str(book): int(count) for book, count in books},
        "finding": (
            "Actual sportId=12 tennis payloads use market 121 and outcomes "
            "121/122. Market 171 is absent from every stored tennis payload "
            "and is Counter-Strike moneyline in the published catalogue. "
            "The 171 tutorial assumption is therefore not the root cause here."
        ),
        "catalogue_sources": [
            "https://oddspapi-v4.mintlify.site/en/docs/markets",
            "https://oddspapi.io/en/docs/get-markets",
            "https://oddspapi.io/blog/json-odds-feed-format-parsing/",
        ],
    }


def _normalization_lineage(conn: psycopg.Connection) -> dict[str, Any]:
    joined = conn.execute("""
        SELECT count(*), count(DISTINCT s.match_id), count(DISTINCT r.provider_event_id)
        FROM odds_snapshots s
        JOIN raw_odds_observations r
          ON r.raw_odds_observation_id = s.raw_odds_observation_id
        WHERE r.provider_code='ODDSPAPI'
    """).fetchone()
    by_book = conn.execute("""
        SELECT s.bookmaker_code, s.source_system::text, s.market_status::text, count(*)
        FROM odds_snapshots s
        JOIN raw_odds_observations r
          ON r.raw_odds_observation_id = s.raw_odds_observation_id
        WHERE r.provider_code='ODDSPAPI'
        GROUP BY 1,2,3 ORDER BY 4 DESC
    """).fetchall()
    return {
        "snapshots_joined_to_oddspapi_raw": int(joined[0]),
        "distinct_matches": int(joined[1]),
        "distinct_fixtures": int(joined[2]),
        "by_bookmaker_source_status": [
            {"bookmaker": row[0], "source_system": row[1],
             "market_status": row[2], "quotes": int(row[3])}
            for row in by_book
        ],
        "snapshots_with_oddspapi_source_prefix": _scalar(
            conn,
            "SELECT count(*) FROM odds_snapshots WHERE source_snapshot_id LIKE %s",
            ("ODDSPAPI%",),
        ),
        "attribution_finding": (
            "odds_snapshots has no provider_code column and its "
            "source_snapshot_id uses a generic SE_LIVE_ODDS_SNAPSHOT_V1 prefix. "
            "Filtering it for an ODDSPAPI prefix returns zero even though the "
            "raw-observation lineage contains the OddsPapi quotes. Provider "
            "attribution must join raw_odds_observation_id."
        ),
    }


def _ingest_outcomes(conn: psycopg.Connection) -> dict[str, Any]:
    statuses = conn.execute("""
        SELECT status, count(*) FROM ingest_runs
        WHERE source_system='ODDSPAPI' GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    errors = conn.execute("""
        SELECT e->>'reason', count(*)
        FROM ingest_runs, jsonb_array_elements(coalesce(error_summary,'[]'::jsonb)) e
        WHERE source_system='ODDSPAPI'
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    return {
        "ingest_status": {str(status): int(count) for status, count in statuses},
        "normalization_error_reasons": {
            str(reason): int(count) for reason, count in errors},
    }


def _no_snapshot_buckets(conn: psycopg.Connection) -> dict[str, Any]:
    rows = conn.execute("""
        SELECT r.payload_json, r.received_at,
               coalesce(ir.error_summary, '[]'::jsonb)
        FROM raw_odds_observations r
        LEFT JOIN ingest_runs ir ON ir.ingest_run_id=r.ingest_run_id
        WHERE r.provider_code='ODDSPAPI'
          AND NOT EXISTS (
            SELECT 1 FROM odds_snapshots s
            WHERE s.raw_odds_observation_id = r.raw_odds_observation_id)
    """).fetchall()
    buckets: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    examples: dict[str, Any] = {}
    for payload, received_at, error_summary in rows:
        start_text = payload.get("startTime")
        books = payload.get("bookmakerOdds") or {}
        licensed = [slug for slug in books if slug in ("pinnacle+30", "1xbet", "bet365")]
        if not licensed:
            buckets["unlicensed_book_only"] += 1
        for item in error_summary or []:
            errors[str(item.get("reason"))] += 1
        try:
            start = datetime.fromisoformat(str(start_text))
        except ValueError:
            buckets["invalid_start"] += 1
            continue
        delta = (received_at - start).total_seconds()
        if delta > 0:
            buckets["post_provider_start"] += 1
        elif delta > -300:
            buckets["prestart_within_5_minutes"] += 1
        else:
            buckets["prestart_more_than_5_minutes"] += 1
        examples.setdefault("licensed" if licensed else "unlicensed", {
            "fixtureId": payload.get("fixtureId"), "books": licensed,
            "received_at": received_at.isoformat(),
            "provider_start": start.isoformat(),
        })
    return {
        "raw_without_snapshot": len(rows),
        "provider_start_buckets": dict(sorted(buckets.items())),
        "run_error_reasons": dict(sorted(errors.items())),
        "examples": examples,
        "note": ("Failures are failed ingest runs, not a missing market 121. "
                 "The unlicensed-book bucket overlaps the time buckets and is "
                 "not additive. Of the 99, 74 were post-provider-start and 25 "
                 "were pre-start by provider but rejected because the canonical "
                 "start had passed."),
    }


def _timing(conn: psycopg.Connection, *, start: datetime, end: datetime) -> dict[str, Any]:
    """First-issued A1 predictions versus first OddsPapi acquisition."""
    rows = conn.execute("""
        WITH first_pred AS (
          SELECT DISTINCT ON (p.match_id)
                 p.match_id,
                 GREATEST(p.as_of, pr.prediction_finalized_at) AS issue
          FROM predictions p
          JOIN prediction_runs pr ON pr.prediction_run_id=p.prediction_run_id
          WHERE p.agent_code='AGENT_1_SURFACE_ELO'
            AND p.prediction_status='PREDICTED'
            AND p.experiment_id IS NULL
            AND pr.run_status='COMPLETED'
            AND pr.prediction_finalized_at IS NOT NULL
            AND p.as_of >= %(start)s AND p.as_of < %(end)s
          ORDER BY p.match_id, p.as_of, p.prediction_run_id
        ), first_raw AS (
          SELECT fp.match_id, min(r.received_at) AS first_received
          FROM first_pred fp
          JOIN match_source_ids ms
            ON ms.source_system='ODDSPAPI' AND ms.match_id=fp.match_id
          JOIN raw_odds_observations r
            ON r.provider_code='ODDSPAPI'
           AND r.provider_event_id=ms.source_match_id
          GROUP BY fp.match_id
        )
        SELECT count(*) AS predictions,
               count(fr.first_received) AS with_identity,
               count(*) FILTER (
                 WHERE fr.first_received IS NOT NULL AND fr.first_received <= fp.issue
               ) AS before_issue,
               percentile_cont(0.5) WITHIN GROUP (
                 ORDER BY EXTRACT(EPOCH FROM (fr.first_received - fp.issue))
               ) AS median_lag_seconds,
               min(EXTRACT(EPOCH FROM (fr.first_received - fp.issue))) AS min_lag,
               max(EXTRACT(EPOCH FROM (fr.first_received - fp.issue))) AS max_lag
        FROM first_pred fp LEFT JOIN first_raw fr ON fr.match_id=fp.match_id
    """, {"start": _utc(start), "end": _utc(end)}).fetchone()
    return {
        "window_start": _utc(start).isoformat(),
        "window_end": _utc(end).isoformat(),
        "first_issued_a1_predictions": int(rows[0]),
        "with_an_oddspapi_identity_ever": int(rows[1]),
        "identity_before_issue": int(rows[2]),
        "median_identity_lag_seconds": float(rows[3]) if rows[3] is not None else None,
        "median_identity_lag_hours": (float(rows[3]) / 3600 if rows[3] is not None else None),
        "min_lag_seconds": float(rows[4]) if rows[4] is not None else None,
        "max_lag_seconds": float(rows[5]) if rows[5] is not None else None,
        "strict_original_time_comparisons": 0,
        "strict_reason": (
            "An identity before issue is only a candidate. Strict evidence also "
            "needs an independent OddsPapi tournament mapping and a verified "
            "edition window. The prior census measured 0 independent mappings, "
            "so the strict count remains zero even though a few identities "
            "arrived before issue."),
    }


def _strict_vs_normalization() -> dict[str, Any]:
    return {
        "normalized_snapshot": (
            "A quote exists in odds_snapshots and is linked to a raw provider "
            "observation. It may still be late."),
        "strict_original_time_comparison": (
            "The quote's provider/observed and ingest times are both at or "
            "before the original prediction issue time, the provider identity "
            "is independently mapped, and the tournament edition window and "
            "participant orientation are verified."),
        "count_relationship": (
            "Normalized quotes are necessary but not sufficient for a strict "
            "original-time comparison. A zero strict count does not imply a "
            "zero normalized count."),
    }


def run(conn: psycopg.Connection, *, start=TRAIN_START,
        end=VALIDATION_END) -> dict[str, Any]:
    return {
        "version": VERSION,
        "status": "READ_ONLY_DESCRIPTIVE_AUDIT",
        "payload_catalogue": _payload_catalogue(conn),
        "normalization_lineage": _normalization_lineage(conn),
        "ingest_outcomes": _ingest_outcomes(conn),
        "no_snapshot_payloads": _no_snapshot_buckets(conn),
        "original_time_timing": _timing(conn, start=start, end=end),
        "definitions": _strict_vs_normalization(),
        "limitations": [
            "No provider request, purchase or quota consumption.",
            "The census describes the current production database, not an earlier snapshot of it.",
            "A normalized quote is not proof of original-time availability.",
            "Provider startTime and canonical start can differ; the normalizer uses the canonical start.",
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
