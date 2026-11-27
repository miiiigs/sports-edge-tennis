"""Read-only provider attribution, delay and catalogue reconciliation probe.

Reproduce (read-only production connection required):

    python ops/validation/provider_reconciliation_probe_20261127.py \
      --output docs/research/evidence/provider_reconciliation_real_20261127.json

No provider request, purchase, write or schema change is made.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sports_edge_tennis.odds.attribution import provider_coverage_report  # noqa: E402
from sports_edge_tennis.odds.catalogue import diagnose_payload  # noqa: E402
from sports_edge_tennis.validation.acquisition_delay_decomposition import (  # noqa: E402
    build_report,
    load_fixture_clocks,
)
from sports_edge_tennis.validation.api_tennis_acquisition_plan import (  # noqa: E402
    assess_entitlement,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("read-only TENNIS_DATABASE_URL required")

    now = datetime.now(UTC)
    out: dict = {"measured_at": now.isoformat(), "provider_calls": 0}
    with psycopg.connect(
        dsn, autocommit=True, connect_timeout=30,
        options="-c default_transaction_read_only=on",
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        out["coverage"] = provider_coverage_report(conn)

        delay = build_report(load_fixture_clocks(conn))
        out["delay_fixture_sample"] = delay.pop("fixtures_detail")[:5]
        out["delay"] = delay

        rows = conn.execute(
            """
            SELECT DISTINCT ON (r.provider_event_id) r.payload_json
            FROM raw_odds_observations r
            WHERE r.provider_code = 'ODDSPAPI'
            ORDER BY r.provider_event_id, r.observation_time DESC,
                     r.raw_odds_observation_id DESC
            """
        ).fetchall()
        codes: Counter[str] = Counter()
        severity: Counter[str] = Counter()
        fixtures_with_alerts = 0
        for (payload,) in rows:
            alerts = diagnose_payload(payload)
            fixtures_with_alerts += int(bool(alerts))
            for alert in alerts:
                codes[alert.code] += 1
                severity[alert.severity] += 1
        out["catalogue_diagnostics"] = {
            "fixtures": len(rows),
            "fixtures_with_alerts": fixtures_with_alerts,
            "alert_codes": dict(sorted(codes.items())),
            "severity": dict(sorted(severity.items())),
        }

        out["fixture_metadata"] = {
            "fixtures": conn.execute(
                "SELECT count(DISTINCT provider_event_id) FROM raw_odds_observations "
                "WHERE provider_code='ODDSPAPI'"
            ).fetchone()[0],
            "with_updated_at": conn.execute(
                "SELECT count(DISTINCT provider_event_id) FROM raw_odds_observations "
                "WHERE provider_code='ODDSPAPI' AND payload_json ? 'updatedAt'"
            ).fetchone()[0],
            "with_true_start_time": conn.execute(
                "SELECT count(DISTINCT provider_event_id) FROM raw_odds_observations "
                "WHERE provider_code='ODDSPAPI' AND payload_json ? 'trueStartTime'"
            ).fetchone()[0],
            "with_status_id": conn.execute(
                "SELECT count(DISTINCT provider_event_id) FROM raw_odds_observations "
                "WHERE provider_code='ODDSPAPI' AND payload_json ? 'statusId'"
            ).fetchone()[0],
            "with_betradar_external_id": conn.execute(
                "SELECT count(DISTINCT provider_event_id) FROM raw_odds_observations "
                "WHERE provider_code='ODDSPAPI' "
                "AND payload_json->'externalProviders'->>'betradarId' IS NOT NULL"
            ).fetchone()[0],
            "api_tennis_external_id_note": (
                "No API-Tennis external id field exists in the stored OddsPapi payload."
            ),
        }

        out["identity_cache"] = {
            "rows": conn.execute(
                "SELECT count(*) FROM oddspapi_event_identity_cache"
            ).fetchone()[0],
            "orientations": dict(conn.execute(
                "SELECT player_orientation, count(*) FROM oddspapi_event_identity_cache "
                "GROUP BY 1 ORDER BY 1"
            ).fetchall()),
            "statuses": dict(conn.execute(
                "SELECT resolution_status, count(*) FROM oddspapi_event_identity_cache "
                "GROUP BY 1 ORDER BY 1"
            ).fetchall()),
            "tournaments": conn.execute(
                "SELECT count(DISTINCT oddspapi_tournament_id) "
                "FROM oddspapi_event_identity_cache"
            ).fetchone()[0],
        }

        out["tournament_editions"] = {
            "canonical_editions": conn.execute(
                "SELECT count(*) FROM tournament_editions"
            ).fetchone()[0],
            "missing_end_date": conn.execute(
                "SELECT count(*) FROM tournament_editions WHERE end_date IS NULL"
            ).fetchone()[0],
            "cached_oddspapi_tournament_editions": conn.execute(
                """
                SELECT count(DISTINCT m.tournament_edition_id)
                FROM oddspapi_event_identity_cache c
                JOIN matches m ON m.match_id = c.canonical_match_id
                """
            ).fetchone()[0],
            "cached_oddspapi_missing_end_date": conn.execute(
                """
                SELECT count(DISTINCT m.tournament_edition_id)
                FROM oddspapi_event_identity_cache c
                JOIN matches m ON m.match_id = c.canonical_match_id
                JOIN tournament_editions te
                  ON te.tournament_edition_id = m.tournament_edition_id
                WHERE te.end_date IS NULL
                """
            ).fetchone()[0],
            "tournament_source_ids_by_provider": dict(conn.execute(
                "SELECT source_system, count(*) FROM tournament_source_ids "
                "GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()),
        }

        out["provider_start_drift"] = {
            row[0]: {
                "matched": row[1], "min_minutes": row[2],
                "median_minutes": row[3], "max_minutes": row[4],
                "within_30": row[5], "within_60": row[6],
                "provider_earlier": row[7], "exact_tie": row[8],
                "provider_later": row[9],
            }
            for row in conn.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (ms.match_id)
                           ms.match_id,
                           (r.payload_json->>'startTime')::timestamptz AS provider_start
                    FROM raw_odds_observations r
                    JOIN match_source_ids ms
                      ON ms.source_system = 'ODDSPAPI'
                     AND ms.source_match_id = r.payload_json->>'fixtureId'
                    WHERE r.provider_code = 'ODDSPAPI'
                    ORDER BY ms.match_id, r.observation_time DESC,
                             r.raw_odds_observation_id DESC
                )
                SELECT 'ODDSPAPI', count(*),
                       min(extract(epoch FROM (l.provider_start - v.match_start_at))/60),
                       percentile_cont(0.5) WITHIN GROUP (
                           ORDER BY extract(epoch FROM (l.provider_start - v.match_start_at))/60),
                       max(extract(epoch FROM (l.provider_start - v.match_start_at))/60),
                       count(*) FILTER (
                           WHERE abs(extract(epoch FROM (l.provider_start - v.match_start_at))/60) <= 30),
                       count(*) FILTER (
                           WHERE abs(extract(epoch FROM (l.provider_start - v.match_start_at))/60) <= 60),
                       count(*) FILTER (WHERE l.provider_start < v.match_start_at),
                       count(*) FILTER (WHERE l.provider_start = v.match_start_at),
                       count(*) FILTER (WHERE l.provider_start > v.match_start_at)
                FROM latest l
                JOIN LATERAL (
                    SELECT match_start_at FROM match_versions mv
                    WHERE mv.match_id = l.match_id
                    ORDER BY mv.as_of DESC, mv.match_version_id DESC LIMIT 1
                ) v ON TRUE
                GROUP BY 1
                """
            ).fetchall()
        }

        horizon = now + timedelta(days=14)
        out["upcoming_cohort"] = {
            "window_start": now.isoformat(),
            "window_end": horizon.isoformat(),
            "by_tour": dict(conn.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (mv.match_id)
                           mv.match_id, mv.tour_scope, mv.match_start_at
                    FROM match_versions mv
                    WHERE mv.as_of <= %s
                    ORDER BY mv.match_id, mv.as_of DESC, mv.match_version_id DESC
                )
                SELECT tour_scope, count(*) FROM latest
                WHERE match_start_at >= %s AND match_start_at < %s
                  AND tour_scope IN ('ATP_MAIN','CHALLENGER')
                GROUP BY 1 ORDER BY 1
                """,
                (now, now, horizon),
            ).fetchall()),
            "with_api_tennis_mapping": conn.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (mv.match_id)
                           mv.match_id, mv.tour_scope, mv.match_start_at
                    FROM match_versions mv WHERE mv.as_of <= %s
                    ORDER BY mv.match_id, mv.as_of DESC, mv.match_version_id DESC
                )
                SELECT count(DISTINCT l.match_id) FROM latest l
                JOIN match_source_ids ms
                  ON ms.match_id = l.match_id AND ms.source_system='API_TENNIS'
                WHERE l.match_start_at >= %s AND l.match_start_at < %s
                  AND l.tour_scope IN ('ATP_MAIN','CHALLENGER')
                """,
                (now, now, horizon),
            ).fetchone()[0],
            "with_oddspapi_mapping": conn.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (mv.match_id)
                           mv.match_id, mv.tour_scope, mv.match_start_at
                    FROM match_versions mv WHERE mv.as_of <= %s
                    ORDER BY mv.match_id, mv.as_of DESC, mv.match_version_id DESC
                )
                SELECT count(DISTINCT l.match_id) FROM latest l
                JOIN match_source_ids ms
                  ON ms.match_id = l.match_id AND ms.source_system='ODDSPAPI'
                WHERE l.match_start_at >= %s AND l.match_start_at < %s
                  AND l.tour_scope IN ('ATP_MAIN','CHALLENGER')
                """,
                (now, now, horizon),
            ).fetchone()[0],
        }

        out["provider_overlap"] = {
            "matches_with_both_provider_ids": conn.execute(
                """
                SELECT count(*) FROM (
                    SELECT match_id FROM match_source_ids
                    WHERE source_system IN ('API_TENNIS','ODDSPAPI')
                    GROUP BY match_id
                    HAVING count(DISTINCT source_system) = 2
                ) x
                """
            ).fetchone()[0],
            "matches_with_both_normalized_books": conn.execute(
                """
                SELECT count(*) FROM (
                    SELECT o.match_id FROM odds_snapshots o
                    JOIN raw_odds_observations r USING (raw_odds_observation_id)
                    WHERE r.provider_code IN ('API_TENNIS','ODDSPAPI')
                    GROUP BY o.match_id
                    HAVING count(DISTINCT r.provider_code) = 2
                ) x
                """
            ).fetchone()[0],
        }

        out["bookmaker_availability"] = {
            str(row[0]): {
                "quotes": row[1], "matches": row[2],
                "first": row[3], "last": row[4],
            }
            for row in conn.execute(
                """
                SELECT o.bookmaker_code, count(*), count(DISTINCT o.match_id),
                       min(o.source_observed_at), max(o.source_observed_at)
                FROM odds_snapshots o
                JOIN raw_odds_observations r USING (raw_odds_observation_id)
                WHERE o.bookmaker_code IN ('PINNACLE','PNCL')
                GROUP BY 1 ORDER BY 1
                """
            ).fetchall()
        }

        out["bookmaker_overlap_same_match"] = [
            {"api_tennis_book": row[0], "oddspapi_book": row[1], "matches": row[2]}
            for row in conn.execute(
                """
                SELECT a.bookmaker_code, b.bookmaker_code, count(DISTINCT a.match_id)
                FROM odds_snapshots a
                JOIN raw_odds_observations ra USING (raw_odds_observation_id)
                JOIN odds_snapshots b ON b.match_id = a.match_id
                JOIN raw_odds_observations rb
                  ON rb.raw_odds_observation_id = b.raw_odds_observation_id
                WHERE ra.provider_code = 'API_TENNIS'
                  AND rb.provider_code = 'ODDSPAPI'
                  AND a.bookmaker_code = b.bookmaker_code
                GROUP BY 1,2 ORDER BY 3 DESC
                """
            ).fetchall()
        ]

        by_tour = out["upcoming_cohort"]["by_tour"]
        out["balanced_160_target"] = {
            "requested_per_tour": 80,
            "ATP_MAIN": {
                "eligible": int(by_tour.get("ATP_MAIN", 0)),
                "shortfall": max(0, 80 - int(by_tour.get("ATP_MAIN", 0))),
            },
            "CHALLENGER": {
                "eligible": int(by_tour.get("CHALLENGER", 0)),
                "shortfall": max(0, 80 - int(by_tour.get("CHALLENGER", 0))),
            },
            "substituted_from_other_competition": False,
            "targets_are_targets_not_achievements": True,
        }

        entitlement_path = (
            ROOT / "docs/research/evidence/api_tennis_odds_entitlement_real_20261126.json"
        )
        if entitlement_path.exists():
            evidence = json.loads(entitlement_path.read_text())
            entitlement = assess_entitlement(
                evidence,
                evidence_reference=str(
                    entitlement_path.relative_to(ROOT).as_posix()),
            )
            out["api_tennis_entitlement"] = {
                "status": entitlement.status,
                "entitlement_established": entitlement.entitlement_established,
                "quota_established": entitlement.quota_established,
                "may_make_billable_requests": entitlement.may_make_billable_requests,
                "evidence_reference": entitlement.evidence_reference,
                "billable_calls_made_by_this_probe": 0,
            }
        out["pinnacle_availability"] = {
            "source_system_availability": out["bookmaker_availability"],
            "current_oddspapi_plan_includes_pinnacle": True,
            "current_api_tennis_quota_unproven": True,
            "note": (
                "Pinnacle is present in the stored OddsPapi plan as mapping "
                "'pinnacle+30' and has current normalized prices. API-Tennis "
                "PNCL is historical; its current entitlement is unproven."
            ),
        }

    payload = json.dumps(out, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
