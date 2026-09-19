from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

import psycopg

TARGETS = (
    ("API_TENNIS_ODDS_V1:2026-09-19T04:06:16.100390+00:00:1153dc81be9241a787ae610850d233b3", "12164113"),
    ("API_TENNIS_ODDS_V1:2026-09-18T22:03:53.203957+00:00:0f0f6c0b143c4b6c84827590b7dfce3a", "12164113"),
    ("API_TENNIS_ODDS_V1:2026-09-17T13:05:48.841556+00:00:8540fd06a13e4f53af9c92a1f351a47e", "12163867"),
    ("API_TENNIS_ODDS_V1:2026-09-17T05:05:18.197910+00:00:194083df4c1747e28ae6a0b8b6ca63dd", "12163576"),
    ("API_TENNIS_ODDS_V1:2026-09-15T09:08:35.883604+00:00:8baabdf6972c4fbf941bed8463a5b050", "12163334"),
)


def _valid_price(value: object) -> bool:
    try:
        price = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return False
    return price.is_finite() and price > Decimal("1")


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("NO_DATABASE")

    conn = psycopg.connect(dsn)
    try:
        for response_id, event_id in TARGETS:
            row = conn.execute(
                """
                SELECT raw_odds_observation_id, ingest_run_id, payload_json
                FROM raw_odds_observations
                WHERE provider_code = 'API_TENNIS'
                  AND provider_response_id = %s
                  AND provider_event_id = %s
                ORDER BY raw_odds_observation_id DESC
                LIMIT 1
                """,
                (response_id, event_id),
            ).fetchone()
            if row is None:
                print(
                    f"OFFERED_PAYLOAD_AUDIT response_id={response_id} "
                    f"event_id={event_id} raw_present=0 writes=0 provider_requests=0",
                    flush=True,
                )
                continue

            raw_id, ingest_run_id, payload = row
            payload = payload or {}
            market = payload.get("Home/Away")
            home = market.get("Home") if isinstance(market, dict) else None
            away = market.get("Away") if isinstance(market, dict) else None
            home = home if isinstance(home, dict) else {}
            away = away if isinstance(away, dict) else {}

            shared = sorted(set(home).intersection(away), key=lambda value: str(value).casefold())
            valid = [
                str(key)
                for key in shared
                if _valid_price(home.get(key)) and _valid_price(away.get(key))
            ]
            home_only = sorted(str(key) for key in set(home) - set(away))
            away_only = sorted(str(key) for key in set(away) - set(home))

            normalized = conn.execute(
                """
                SELECT
                    selection_type::text,
                    count(*),
                    array_agg(DISTINCT bookmaker_code ORDER BY bookmaker_code)
                FROM odds_snapshots
                WHERE raw_odds_observation_id = %s
                GROUP BY selection_type
                ORDER BY selection_type
                """,
                (raw_id,),
            ).fetchall()
            ingest = conn.execute(
                """
                SELECT status::text, rows_inserted, rows_rejected, rows_unmatched, error_summary
                FROM ingest_runs
                WHERE ingest_run_id = %s
                """,
                (ingest_run_id,),
            ).fetchone()

            print(
                "OFFERED_PAYLOAD_AUDIT "
                f"response_id={response_id} event_id={event_id} raw_present=1 raw_id={raw_id} "
                f"home_away={int(isinstance(market, dict))} "
                f"home_books={len(home)} away_books={len(away)} "
                f"shared_books={len(shared)} valid_pairs={len(valid)} "
                f"valid_keys={','.join(valid) or '-'} "
                f"home_only={','.join(home_only) or '-'} "
                f"away_only={','.join(away_only) or '-'} "
                f"normalized={normalized!r} ingest={ingest!r} "
                "writes=0 provider_requests=0",
                flush=True,
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
