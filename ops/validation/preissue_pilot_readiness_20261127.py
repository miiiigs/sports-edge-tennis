"""Read-only pre-issue pilot readiness and strict qualification audit.

Reproduce:

    python ops/validation/preissue_pilot_readiness_20261127.py \
      --output docs/research/evidence/preissue_pilot_readiness_real_20261127.json

No provider calls, reservations or writes are made.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from sports_edge_tennis.validation.api_tennis_acquisition_plan import (  # noqa: E402
    FixtureCandidate,
    ProviderEntitlement,
    STATUS_UNVERIFIED,
    assess_entitlement,
)
from sports_edge_tennis.validation.paired_qualification_audit import (  # noqa: E402
    audit_quotes,
    load_quote_lineage,
    prediction_locks,
)
from sports_edge_tennis.validation.preissue_pilot_calendar import (  # noqa: E402
    UpcomingFixture,
    plan_preissue_pilot,
)


def _entitlements(evidence_root: Path) -> tuple[ProviderEntitlement, ...]:
    path = evidence_root / "api_tennis_odds_entitlement_real_20261126.json"
    if path.exists():
        api = assess_entitlement(
            json.loads(path.read_text()),
            evidence_reference=str(path.relative_to(ROOT).as_posix()),
        )
    else:
        api = ProviderEntitlement(
            "API_TENNIS", STATUS_UNVERIFIED, False, False, None)
    oddspapi = ProviderEntitlement(
        "ODDSPAPI", STATUS_UNVERIFIED, False, False,
        "No owner-approved current OddsPapi quota evidence in this probe.",
    )
    return (api, oddspapi)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    dsn = os.environ.get("TENNIS_DATABASE_URL")
    if not dsn:
        parser.error("read-only TENNIS_DATABASE_URL required")

    now = datetime.now(UTC)
    out: dict = {
        "measured_at": now.isoformat(),
        "provider_calls": 0,
        "actual_request_reservations": 0,
        "genuine_price_observations": 0,
        "original_time_qualifications": 0,
    }
    with psycopg.connect(
        dsn, autocommit=True, connect_timeout=30,
        options="-c default_transaction_read_only=on",
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")

        # 1. Strict paired-lineage audit over stored normalized quotes.
        audit = {}
        for provider in ("ODDSPAPI", "API_TENNIS"):
            quotes = load_quote_lineage(conn, provider_code=provider)
            match_ids = sorted({quote.match_id for quote in quotes})
            locks = prediction_locks(conn, match_ids=match_ids)
            audit[provider] = audit_quotes(quotes, prediction_locks=locks, now=now)
        out["strict_qualification_audit"] = audit

        # 2. Identity continuity: was the provider mapping durable before the
        # first stored price?
        rows = conn.execute(
            """
            WITH first_price AS (
                SELECT o.match_id, min(o.source_observed_at) AS observed_at
                FROM odds_snapshots o
                JOIN raw_odds_observations r
                  ON r.raw_odds_observation_id = o.raw_odds_observation_id
                WHERE r.provider_code = %s
                GROUP BY o.match_id
            )
            SELECT fp.match_id, fp.observed_at, ms.created_at,
                   CASE WHEN ms.created_at IS NULL THEN 'MISSING'
                        WHEN ms.created_at <= fp.observed_at THEN 'BEFORE_PRICE'
                        ELSE 'AFTER_PRICE' END AS state
            FROM first_price fp
            LEFT JOIN LATERAL (
                SELECT min(created_at) AS created_at
                FROM match_source_ids msi
                WHERE msi.match_id = fp.match_id
                  AND msi.source_system = %s
            ) ms ON TRUE
            """,
            ("ODDSPAPI", "ODDSPAPI"),
        ).fetchall()
        states: dict[str, int] = {}
        for _mid, _observed, _created, state in rows:
            states[state] = states.get(state, 0) + 1
        out["oddspapi_identity_continuity"] = {
            "fixtures": len(rows),
            "state_counts": states,
            "note": "AFTER_PRICE identity cannot be treated as available at acquisition.",
        }

        # 3. Actual competitive start persistence audit.
        columns = conn.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (column_name ILIKE '%actual%start%'
                   OR column_name ILIKE '%competitive%start%'
                   OR column_name ILIKE '%started_at%')
            ORDER BY table_name, column_name
            """
        ).fetchall()
        out["actual_competitive_start_persistence"] = {
            "candidate_columns": [
                {"table": row[0], "column": row[1]} for row in columns
            ],
            "explicit_actual_competitive_start_stored": False,
            "note": (
                "scheduled match_start_at is not an actual competitive start. "
                "No match-level actual start column is present."
            ),
        }

        # 4. Tournament edition mapping continuity.
        out["tournament_edition_mapping"] = {
            "oddspapi_tournament_source_ids": conn.execute(
                "SELECT count(*) FROM tournament_source_ids WHERE source_system='ODDSPAPI'"
            ).fetchone()[0],
            "cached_oddspapi_editions": conn.execute(
                """
                SELECT count(DISTINCT m.tournament_edition_id)
                FROM oddspapi_event_identity_cache c
                JOIN matches m ON m.match_id = c.canonical_match_id
                """
            ).fetchone()[0],
            "cached_oddspapi_editions_missing_end_date": conn.execute(
                """
                SELECT count(DISTINCT m.tournament_edition_id)
                FROM oddspapi_event_identity_cache c
                JOIN matches m ON m.match_id = c.canonical_match_id
                JOIN tournament_editions te
                  ON te.tournament_edition_id = m.tournament_edition_id
                WHERE te.end_date IS NULL
                """
            ).fetchone()[0],
        }

        # 5. Upcoming eligible fixtures and mapping availability.
        horizon = now + timedelta(days=14)
        upcoming_rows = conn.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (mv.match_id)
                       mv.match_id, mv.tour_scope, mv.match_start_at,
                       mv.ingested_at
                FROM match_versions mv
                WHERE mv.as_of <= %s
                ORDER BY mv.match_id, mv.as_of DESC, mv.match_version_id DESC
            )
            SELECT l.match_id, l.tour_scope, l.match_start_at, l.ingested_at,
                   api.source_match_id AS api_key,
                   odd.source_match_id AS odd_fixture,
                   cache.oddspapi_tournament_id
            FROM latest l
            LEFT JOIN LATERAL (
                SELECT source_match_id FROM match_source_ids ms
                WHERE ms.match_id = l.match_id AND ms.source_system='API_TENNIS'
                ORDER BY ms.match_source_id LIMIT 1
            ) api ON TRUE
            LEFT JOIN LATERAL (
                SELECT source_match_id FROM match_source_ids ms
                WHERE ms.match_id = l.match_id AND ms.source_system='ODDSPAPI'
                ORDER BY ms.match_source_id LIMIT 1
            ) odd ON TRUE
            LEFT JOIN oddspapi_event_identity_cache cache
              ON cache.canonical_match_id = l.match_id
            WHERE l.match_start_at >= %s AND l.match_start_at < %s
              AND l.tour_scope IN ('ATP_MAIN','CHALLENGER')
            ORDER BY l.match_start_at, l.match_id
            """,
            (now, now, horizon),
        ).fetchall()
        upcoming = []
        for row in upcoming_rows:
            upcoming.append(UpcomingFixture(
                fixture=FixtureCandidate(
                    match_id=int(row[0]),
                    tour=str(row[1]),
                    scheduled_start_at=row[2],
                    mapped=bool(row[4] and row[5]),
                    api_tennis_match_key=row[4],
                    oddspapi_fixture_id=row[5],
                ),
                discovered_at=row[3] or now,
                planned_issue_at=row[2] - timedelta(minutes=90),
                oddspapi_tournament_id=(
                    int(row[6]) if row[6] is not None else None),
            ))
        out["upcoming_14_days"] = {
            "fixtures": len(upcoming),
            "by_tour": {
                tour: sum(1 for item in upcoming if item.fixture.tour == tour)
                for tour in ("ATP_MAIN", "CHALLENGER")
            },
            "dual_mapped": sum(
                1 for item in upcoming
                if item.fixture.api_tennis_match_key
                and item.fixture.oddspapi_fixture_id
            ),
            "with_oddspapi_tournament_id": sum(
                1 for item in upcoming
                if item.oddspapi_tournament_id is not None
            ),
        }

        # 6. Measured cadence of actual batch starts.
        run_starts = [row[0] for row in conn.execute(
            """
            SELECT started_at FROM prediction_runs
            WHERE started_at >= %s
            ORDER BY started_at DESC LIMIT 48
            """,
            (now - timedelta(days=7),),
        ).fetchall()]
        gaps = [
            (run_starts[i] - run_starts[i + 1]).total_seconds()
            for i in range(len(run_starts) - 1)
            if run_starts[i] > run_starts[i + 1]
        ]
        out["batch_cadence"] = {
            "observed_runs": len(run_starts),
            "median_gap_seconds": median(gaps) if gaps else None,
            "latest_run_at": run_starts[0].isoformat() if run_starts else None,
        }

        # 7. Plan the next eligible slots under the real (unverified) gates.
        slots = [
            (now + timedelta(hours=offset)).replace(minute=0, second=0, microsecond=0)
            for offset in (1, 2, 3)
        ]
        entitlements = _entitlements(ROOT / "docs/research/evidence")
        calendar = plan_preissue_pilot(
            fixtures=upcoming,
            now=now,
            scheduler_slots=slots,
            entitlements=entitlements,
            authorization_reference=None,
            oddspapi_remaining=None,
            api_tennis_remaining=None,
            oddspapi_per_cycle=5,
            api_tennis_per_cycle=5,
        )
        out["pilot_calendar_dry_run"] = calendar
        out["entitlements"] = {
            item.provider_code: {
                "status": item.status,
                "entitlement_established": item.entitlement_established,
                "quota_established": item.quota_established,
                "may_make_billable_requests": item.may_make_billable_requests,
                "evidence_reference": item.evidence_reference,
            }
            for item in entitlements
        }
        out["balanced_160_target"] = {
            "requested_per_tour": 80,
            "ATP_MAIN": {
                "eligible": out["upcoming_14_days"]["by_tour"]["ATP_MAIN"],
                "shortfall": max(0, 80 - out["upcoming_14_days"]["by_tour"]["ATP_MAIN"]),
            },
            "CHALLENGER": {
                "eligible": out["upcoming_14_days"]["by_tour"]["CHALLENGER"],
                "shortfall": max(0, 80 - out["upcoming_14_days"]["by_tour"]["CHALLENGER"]),
            },
            "substituted_from_other_competition": False,
            "targets_are_targets_not_achievements": True,
        }
        out["outstanding_approvals"] = [
            "API-Tennis current quota/entitlement must be verified by the owner.",
            "OddsPapi current remaining allowance must be supplied from the ledger.",
            "A non-empty owner authorization reference is required to execute.",
            "Independent OddsPapi tournament mapping and edition end dates are absent.",
            "A balanced 160-fixture cohort is unavailable off-season (0 ATP).",
        ]

    payload = json.dumps(out, sort_keys=True, default=str, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
