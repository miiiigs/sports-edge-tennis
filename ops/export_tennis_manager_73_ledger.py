"""Read-only, point-in-time first-production Manager dataset for 73% research.

Run with an authorized read-only production DSN in the local environment.
Never writes to production, never includes API keys, wager sizing or odds.
Uses the same existing first-production Manager CTE as the Results scorecard.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from sports_edge_tennis.performance.regimes import (
    first_production_model_picks_cte,
    is_complete_model_regime_key,
    resolve_current_model_regime,
)


def export_sql() -> str:
    return f"""
        WITH {first_production_model_picks_cte()}
        SELECT
            p.match_id,
            COALESCE(
                (mv.match_start_at AT TIME ZONE 'Asia/Manila')::date,
                (p.picked_at AT TIME ZONE 'Asia/Manila')::date
            ) AS manila_date,
            p.consensus_probability,
            p.model_regime_key,
            p.picked_at,
            CASE
                WHEN r.outcome_status = 'COMPLETED'
                  AND r.winner_side = p.selected_side THEN 'WON'
                WHEN r.outcome_status = 'COMPLETED'
                  AND r.winner_side IN (1, 2)
                  AND r.winner_side <> p.selected_side THEN 'LOST'
                WHEN r.outcome_status IN ('RETIRED', 'WALKOVER', 'CANCELLED')
                THEN 'VOID'
                ELSE 'PENDING'
            END AS result_status
        FROM first_model_picks p
        LEFT JOIN LATERAL (
            SELECT mv0.match_start_at
            FROM match_versions mv0
            WHERE mv0.match_id = p.match_id
              AND mv0.as_of <= p.picked_at
              AND mv0.source_observed_at <= p.picked_at
            ORDER BY mv0.as_of DESC,
                     mv0.source_observed_at DESC,
                     mv0.match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        LEFT JOIN LATERAL (
            SELECT mro.outcome_status::text AS outcome_status,
                   mro.winner_side
            FROM match_result_observations mro
            WHERE mro.match_id = p.match_id
              AND mro.outcome_status IN
                  ('COMPLETED', 'RETIRED', 'WALKOVER', 'CANCELLED')
            ORDER BY mro.as_of DESC,
                     mro.source_observed_at DESC,
                     mro.match_result_observation_id DESC
            LIMIT 1
        ) r ON TRUE
        WHERE p.model_regime_key = %s
          AND COALESCE(
            (mv.match_start_at AT TIME ZONE 'Asia/Manila')::date,
            (p.picked_at AT TIME ZONE 'Asia/Manila')::date
          ) <= %s
        ORDER BY manila_date, p.picked_at, p.match_id
    """


def rows_for_regime(conn: psycopg.Connection, regime: str, through):
    """Validated query matching the exact Manager-first production ledger."""
    if not is_complete_model_regime_key(regime):
        raise ValueError("model regime lacks complete lineage")
    raw = conn.execute(export_sql(), (regime, through)).fetchall()
    out = []
    seen = set()
    for match_id, day, prob, key, picked_at, result in raw:
        if int(match_id) in seen:
            raise ValueError("duplicate canonical sporting match")
        seen.add(int(match_id))
        if result not in ("WON", "LOST", "VOID", "PENDING"):
            raise ValueError("unknown result status")
        if not 0 <= float(prob) <= 1:
            raise ValueError("invalid original Manager probability")
        out.append({
            "aligned_pick_type": "MANAGER_CONSENSUS",
            "match_id": int(match_id),
            "manila_date": day.isoformat(),
            "aligned_probability": float(prob),
            "result_status": result,
            "model_regime_key": key,
            "picked_at": picked_at.isoformat(),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Export validated Manager PIT research ledger")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--regime", help="complete original Manager model regime key; default latest")
    args = parser.parse_args()

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("EXPORT_BLOCKED reason=NO_AUTHORIZED_DATABASE_DSN")
        return 2

    # Never include an incomplete local calendar day.
    through = datetime.now(ZoneInfo("Asia/Manila")).date() - timedelta(days=1)
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            if args.regime:
                regime = args.regime
            else:
                latest = resolve_current_model_regime(conn)
                if latest is None or not latest.has_complete_lineage or latest.is_inconsistent:
                    print("EXPORT_BLOCKED reason=LATEST_MODEL_LINEAGE_UNVERIFIED")
                    return 2
                regime = latest.regime_key
            rows = rows_for_regime(conn, regime, through)
    except (ValueError, psycopg.Error):
        print("EXPORT_BLOCKED reason=READ_ONLY_QUERY_OR_LINEAGE_ERROR")
        return 2
    if not rows:
        print("EXPORT_BLOCKED reason=NO_COMPLETE_DAY_MANAGER_EVIDENCE")
        return 2
    fingerprint = hashlib.sha256(
        "\n".join(
            f"{x['match_id']}:{x['picked_at']}:{x['aligned_probability']}:"
            f"{x['result_status']}:{x['model_regime_key']}" for x in rows
        ).encode("utf-8")
    ).hexdigest()
    data = {
        "scope": "FIRST_PRODUCTION_MANAGER_ONLY",
        "model_regime_key": regime,
        "timezone": "Asia/Manila",
        "through_completed_date": through.isoformat(),
        "export_generated_at_utc": datetime.now(UTC).isoformat(),
        "row_count": len(rows),
        "fingerprint_sha256": fingerprint,
        "data": rows,
    }
    # Local artifact only; never write results back to production.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"EXPORT_READY manager_rows={len(rows)} output={args.output.name} sha256={fingerprint[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
