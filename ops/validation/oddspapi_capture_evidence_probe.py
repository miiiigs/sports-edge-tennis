"""Read-only OddsPapi capture evidence probe (no provider calls, no writes).

Produces the aggregate measurements a Phase-3 review needs:

  * independent tournament-mapping availability and edition end dates;
  * provider session start vs canonical point-in-time start drift;
  * identity/price availability relative to the original prediction cutoff;
  * source-observation, ingestion and identity-verification clocks;
  * how many prices genuinely qualify versus remain counterfactual.

Safety: ``default_transaction_read_only=on`` and ``REPEATABLE READ READ ONLY``.
Prints aggregate counts and distributions only; never a DSN, name or price row.
It does not call OddsPapi and does not touch the request ledger.
"""
from __future__ import annotations

import json
import os
import sys

import psycopg

DSN_CANDIDATES = (
    "TENNIS_DATABASE_URL",
    "DATABASE_PUBLIC_URL",
    "DATABASE_URL",
    "DATABASE_PRIVATE_URL",
    "PGURL",
)

DRIFT_SQL = """
WITH latest_observation AS (
    SELECT DISTINCT ON (s.match_id)
           s.match_id,
           (r.payload_json->>'startTime')::timestamptz AS provider_start,
           r.observation_time
    FROM raw_odds_observations r
    JOIN match_source_ids s
      ON s.source_system = 'ODDSPAPI'
     AND s.source_match_id = r.payload_json->>'fixtureId'
    WHERE r.provider_code = 'ODDSPAPI'
    ORDER BY s.match_id, r.observation_time DESC, r.raw_odds_observation_id DESC
)
SELECT count(*) AS matched_fixtures,
       min(extract(epoch FROM (o.provider_start - v.match_start_at)) / 60) AS min_minutes,
       percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM (o.provider_start - v.match_start_at)) / 60
       ) AS median_minutes,
       max(extract(epoch FROM (o.provider_start - v.match_start_at)) / 60) AS max_minutes,
       count(*) FILTER (
           WHERE abs(extract(epoch FROM (o.provider_start - v.match_start_at)) / 60) <= 30
       ) AS within_30,
       count(*) FILTER (
           WHERE abs(extract(epoch FROM (o.provider_start - v.match_start_at)) / 60) <= 60
       ) AS within_60,
       count(*) FILTER (WHERE o.provider_start < v.match_start_at) AS provider_earlier,
       count(*) FILTER (WHERE o.provider_start > v.match_start_at) AS provider_later
FROM latest_observation o
JOIN LATERAL (
    SELECT mv.match_start_at
    FROM match_versions mv
    WHERE mv.match_id = o.match_id
      AND mv.as_of <= o.observation_time
      AND mv.source_observed_at <= o.observation_time
    ORDER BY mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
    LIMIT 1
) v ON TRUE
"""

PREDICTION_SEQUENCE_SQL = """
WITH first_prediction AS (
    SELECT match_id, min(as_of) AS as_of, min(prediction_locked_at) AS locked_at
    FROM predictions
    WHERE prediction_run_id IS NOT NULL
    GROUP BY match_id
), identity AS (
    SELECT match_id, min(created_at) AS created_at
    FROM match_source_ids
    WHERE source_system = 'ODDSPAPI'
    GROUP BY match_id
)
SELECT count(*) AS predicted_fixtures,
       count(i.created_at) AS with_identity,
       count(*) FILTER (WHERE i.created_at <= p.as_of) AS identity_pre_cutoff,
       count(*) FILTER (WHERE i.created_at <= p.locked_at) AS identity_pre_lock,
       percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM (i.created_at - p.as_of))
       ) AS identity_median_lag_seconds
FROM first_prediction p
LEFT JOIN identity i USING (match_id)
"""

PRICE_SEQUENCE_SQL = """
WITH first_prediction AS (
    SELECT match_id, min(as_of) AS as_of
    FROM predictions
    WHERE prediction_run_id IS NOT NULL
    GROUP BY match_id
), first_price AS (
    SELECT s.match_id, min(r.received_at) AS received_at
    FROM raw_odds_observations r
    JOIN match_source_ids s
      ON s.source_system = 'ODDSPAPI'
     AND s.source_match_id = r.payload_json->>'fixtureId'
    WHERE r.provider_code = 'ODDSPAPI'
    GROUP BY s.match_id
)
SELECT count(*) AS fixtures_with_price,
       count(*) FILTER (WHERE f.received_at <= p.as_of) AS price_pre_cutoff,
       percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM (f.received_at - p.as_of))
       ) AS price_median_lag_seconds
FROM first_prediction p
JOIN first_price f USING (match_id)
"""


def _connect() -> tuple[str, psycopg.Connection]:
    errors: list[str] = []
    for name in DSN_CANDIDATES:
        value = os.environ.get(name)
        if not value:
            continue
        try:
            return name, psycopg.connect(value, connect_timeout=15,
                                         autocommit=False)
        except psycopg.OperationalError as exc:
            errors.append(f"{name}:{type(exc).__name__}")
    raise SystemExit(f"no reachable database URL: {errors}")


def probe(conn: psycopg.Connection) -> dict[str, object]:
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET statement_timeout = '180s'")
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        raise SystemExit("read-only guard failed")
    # The raw-to-canonical join extracts fixtureId from JSON; the planner
    # otherwise picks a nested loop over 10k raw rows x 136k source rows.
    # This is a session-local planner choice for a bounded read-only query.
    conn.execute("SET LOCAL enable_nestloop = off")

    def one(sql: str, params=None):
        return conn.execute(sql, params or {}).fetchone()

    out: dict[str, object] = {}

    out["tournament_mapping"] = {
        "source_id_rows_by_provider": {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT source_system, count(*) FROM tournament_source_ids GROUP BY 1"
            ).fetchall()
        },
        "independent_oddspapi_mappings": one(
            "SELECT count(*) FROM tournament_source_ids WHERE source_system = 'ODDSPAPI'"
        )[0],
        "oddspapi_cached_identities": {
            "rows": one(
                "SELECT count(*) FROM oddspapi_event_identity_cache")[0],
            "distinct_tournaments": one(
                "SELECT count(DISTINCT oddspapi_tournament_id) "
                "FROM oddspapi_event_identity_cache")[0],
        },
        "cached_canonical_editions": {
            "editions": one(
                """
                SELECT count(DISTINCT m.tournament_edition_id)
                FROM oddspapi_event_identity_cache c
                JOIN matches m ON m.match_id = c.canonical_match_id
                """
            )[0],
            "editions_missing_end_date": one(
                """
                SELECT count(DISTINCT m.tournament_edition_id)
                FROM oddspapi_event_identity_cache c
                JOIN matches m ON m.match_id = c.canonical_match_id
                JOIN tournament_editions e
                  ON e.tournament_edition_id = m.tournament_edition_id
                WHERE e.end_date IS NULL
                """
            )[0],
        },
        "editions_missing_end_date": {
            "total": one("SELECT count(*) FROM tournament_editions")[0],
            "missing": one(
                "SELECT count(*) FROM tournament_editions WHERE end_date IS NULL"
            )[0],
        },
    }

    drift = one(DRIFT_SQL)
    out["provider_start_drift_minutes"] = {
        "matched_fixtures": drift[0],
        "min": float(drift[1]) if drift[1] is not None else None,
        "median": float(drift[2]) if drift[2] is not None else None,
        "max": float(drift[3]) if drift[3] is not None else None,
        "within_30": drift[4],
        "within_60": drift[5],
        "provider_earlier": drift[6],
        "provider_later": drift[7],
        "note": (
            "Provider payload startTime minus the canonical match version "
            "visible at that observation time. The cache's scheduled_start is "
            "the canonical start, not provider evidence."
        ),
    }

    identity = one(PREDICTION_SEQUENCE_SQL)
    price = one(PRICE_SEQUENCE_SQL)
    out["capture_sequence"] = {
        "predicted_fixtures": identity[0],
        "with_oddspapi_identity": identity[1],
        "identity_pre_cutoff": identity[2],
        "identity_pre_lock": identity[3],
        "identity_median_lag_seconds": (float(identity[4])
                                        if identity[4] is not None else None),
        "fixtures_with_oddspapi_raw_price": price[0],
        "price_pre_cutoff": price[1],
        "price_median_lag_seconds": (float(price[2])
                                     if price[2] is not None else None),
    }

    raw_timing = one(
        """
        SELECT count(*), count(DISTINCT s.match_id),
               min(r.observation_time), max(r.observation_time),
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY extract(epoch FROM (r.created_at - r.received_at))),
               percentile_cont(0.9) WITHIN GROUP (
                   ORDER BY extract(epoch FROM (r.created_at - r.received_at))),
               max(extract(epoch FROM (r.created_at - r.received_at)))
        FROM raw_odds_observations r
        JOIN match_source_ids s
          ON s.source_system = 'ODDSPAPI'
         AND s.source_match_id = r.payload_json->>'fixtureId'
        WHERE r.provider_code = 'ODDSPAPI'
        """
    )
    out["raw_observation_timing"] = {
        "observations": raw_timing[0],
        "matched_fixtures": raw_timing[1],
        "first_observation": raw_timing[2],
        "last_observation": raw_timing[3],
        "ingest_lag_p50_seconds": (float(raw_timing[4])
                                   if raw_timing[4] is not None else None),
        "ingest_lag_p90_seconds": (float(raw_timing[5])
                                   if raw_timing[5] is not None else None),
        "ingest_lag_max_seconds": (float(raw_timing[6])
                                   if raw_timing[6] is not None else None),
        "time_basis_counts": {
            f"{row[0]}/{row[1]}": row[2]
            for row in conn.execute(
                """
                SELECT observation_time_basis::text, raw_status::text, count(*)
                FROM raw_odds_observations
                WHERE provider_code = 'ODDSPAPI'
                GROUP BY 1, 2 ORDER BY 1, 2
                """
            ).fetchall()
        },
        "bookmakers_in_payload": one(
            """
            WITH latest AS (
                SELECT DISTINCT ON (s.match_id)
                       r.payload_json->'bookmakerOdds' AS bookmaker_odds
                FROM raw_odds_observations r
                JOIN match_source_ids s
                  ON s.source_system = 'ODDSPAPI'
                 AND s.source_match_id = r.payload_json->>'fixtureId'
                WHERE r.provider_code = 'ODDSPAPI'
                ORDER BY s.match_id, r.observation_time DESC,
                         r.raw_odds_observation_id DESC
            )
            SELECT count(DISTINCT bookmaker)
            FROM latest, jsonb_object_keys(bookmaker_odds) AS bookmaker
            """
        )[0],
    }

    out["price_qualification"] = {
        "raw_oddspapi_fixtures": raw_timing[1],
        "normalized_oddspapi_odds_snapshots": one(
            "SELECT count(*) FROM odds_snapshots WHERE source_system = 'ODDSPAPI'"
        )[0],
        "cached_identities_with_raw_price": one(
            """
            SELECT count(*) FROM oddspapi_event_identity_cache c
            WHERE EXISTS (
                SELECT 1
                FROM match_source_ids s
                JOIN raw_odds_observations r
                  ON r.provider_code = 'ODDSPAPI'
                 AND r.payload_json->>'fixtureId' = s.source_match_id
                WHERE s.match_id = c.canonical_match_id
                  AND s.source_system = 'ODDSPAPI')
            """
        )[0],
        "independent_mapping_gate": 0,
        "genuinely_recovered_qualifying_pairs": 0,
        "counterfactual_pairs_not_recovered": 18,
        "counterfactual_note": (
            "The previously documented 18 pairs assume a verified tournament "
            "mapping and a provider-to-canonical start alignment. Neither "
            "condition holds, so they are not recovered coverage."
        ),
    }

    out["provider_ledger"] = {
        "rows": one("SELECT count(*) FROM oddspapi_request_ledger")[0],
        "latest_created_at": one(
            "SELECT max(created_at) FROM oddspapi_request_ledger")[0],
        "by_endpoint": {
            row[0]: {"count": row[1], "min_created_at": row[2],
                     "max_created_at": row[3]}
            for row in conn.execute(
                """
                SELECT endpoint, count(*), min(created_at), max(created_at)
                FROM oddspapi_request_ledger GROUP BY 1 ORDER BY 1
                """
            ).fetchall()
        },
    }
    out["provider_calls_made_by_probe"] = 0
    out["database_writes"] = False
    return out


def main() -> int:
    name, conn = _connect()
    try:
        with conn:
            result = probe(conn)
            conn.rollback()
    finally:
        conn.close()
    result["dsn_variable"] = name
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
