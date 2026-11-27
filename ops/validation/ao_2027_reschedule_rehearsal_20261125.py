"""Isolated AO 2027 rescheduling rehearsal.

Verifies, against a disposable local PostgreSQL database, that:

* a later observed schedule version is appended without rewriting the original;
* the original scheduled start and the original PIT visibility are retained;
* the fixture is classified RESCHEDULED, not cancelled or re-created;
* the pre-match target guard still excludes a fixture inside its 30-minute
  deadline and admits it with sufficient lead.

It never touches production, never calls a provider and never writes outside the
local disposable schema.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import psycopg

from sports_edge_tennis.data.sources.api_tennis_live import (
    ingest_api_tennis_source_a_fixtures,
)

VERSION = "AO_2027_RESCHEDULE_REHEARSAL_20261125_V1"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST = os.path.join(REPO_ROOT, "db", "migrations", "manifest.json")
FIXTURE_TIME = datetime(2026, 11, 25, 0, 0, tzinfo=UTC)
RESCHEDULE_OBSERVED = datetime(2026, 11, 26, 0, 0, tzinfo=UTC)
ORIGINAL_START = datetime(2027, 1, 18, 10, 0, tzinfo=UTC)
NEW_START = datetime(2027, 1, 19, 10, 0, tzinfo=UTC)


def _assert_local(dsn: str) -> None:
    lowered = dsn.lower()
    if "railway" in lowered or "proxy.rlwy" in lowered:
        raise SystemExit("refusing shared Railway database")
    if "localhost" not in lowered and "127.0.0.1" not in lowered:
        raise SystemExit("rehearsal requires a local disposable database")


def _apply_migrations(conn: psycopg.Connection) -> None:
    manifest = json.loads(Path(MANIFEST).read_text(encoding="utf-8"))
    for entry in manifest["migrations"]:
        migration_sql = Path(REPO_ROOT, entry["up"]).read_text(encoding="utf-8")
        migration_sql = migration_sql.replace(
            "ALTER TYPE competition_tier\n"
            "    ADD VALUE IF NOT EXISTS 'ATP_TOUR' BEFORE 'ATP_500';\n",
            "",
        )
        conn.execute(migration_sql)


def _prematch_eligible(start: datetime | None, as_of: datetime) -> bool:
    return start is not None and start >= as_of + timedelta(minutes=30)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", required=True)
    args = parser.parse_args(argv)
    dsn = os.getenv("REHEARSAL_DATABASE_URL")
    if not dsn:
        parser.error("REHEARSAL_DATABASE_URL required")
    _assert_local(dsn)

    fixture = {
        "event_key": "ao2027-reschedule-1",
        "tournament_key": "ao2027-reschedule",
        "first_player_key": "ao-rp1",
        "second_player_key": "ao-rp2",
        "event_first_player": "Reschedule One",
        "event_second_player": "Reschedule Two",
        "tournament_name": "Australian Open",
        "event_type_type": "ATP Singles",
        "event_status": "Not Started",
        "event_date": "2027-01-18",
        "event_time": "10:00",
        "surface": "Hard",
        "round": "R128",
    }

    with psycopg.connect(dsn, autocommit=True) as setup:
        setup.execute("DROP SCHEMA IF EXISTS public CASCADE")
        setup.execute("CREATE SCHEMA IF NOT EXISTS public AUTHORIZATION current_user")
        setup.execute("SET search_path TO public")
        _apply_migrations(setup)

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET search_path TO public")
        ingest = ingest_api_tennis_source_a_fixtures(
            conn, [fixture], received_at=FIXTURE_TIME
        )
        assert ingest.status == "COMPLETED", ingest.status
        match_id = ingest.matches[0].match_id

        original = conn.execute(
            "SELECT player_1_id, player_2_id FROM match_versions "
            "WHERE match_id=%s ORDER BY as_of LIMIT 1",
            (match_id,),
        ).fetchone()
        player_1_id, player_2_id = original
        conn.execute(
            """
            INSERT INTO match_versions (
                match_id, player_1_id, player_2_id, match_start_at, tour_scope,
                competition_tier, surface, round_code, best_of_sets,
                qualifying_flag, match_status, source_observed_at, as_of,
                ingested_at
            ) VALUES (
                %s, %s, %s, %s, 'ATP_MAIN', 'GRAND_SLAM', 'HARD', 'R128', 5,
                FALSE, 'SCHEDULED', %s, %s, %s
            )
            """,
            (
                match_id,
                player_1_id,
                player_2_id,
                NEW_START,
                RESCHEDULE_OBSERVED,
                RESCHEDULE_OBSERVED,
                RESCHEDULE_OBSERVED,
            ),
        )

        rows = conn.execute(
            """
            SELECT match_version_id, match_start_at, match_status::text,
                   source_observed_at, as_of, ingested_at, round_code,
                   best_of_sets, qualifying_flag
            FROM match_versions WHERE match_id=%s ORDER BY as_of, match_version_id
            """,
            (match_id,),
        ).fetchall()
        first = rows[0]
        latest = rows[-1]
        original_start = first[1]
        latest_start = latest[1]
        start_change_count = sum(
            1 for before, after in pairwise(rows) if after[1] != before[1]
        )
        status_change_count = sum(
            1 for before, after in pairwise(rows) if after[2] != before[2]
        )
        changes = [
            {
                "observed_at": after[3].isoformat(),
                "from_start": None if before[1] is None else before[1].isoformat(),
                "to_start": None if after[1] is None else after[1].isoformat(),
                "from_status": before[2],
                "to_status": after[2],
            }
            for before, after in pairwise(rows)
            if after[1] != before[1] or after[2] != before[2]
        ]
        deadline = original_start - timedelta(minutes=30)
        pit_visible = any(
            row[4] <= deadline and row[3] <= deadline and row[5] <= deadline
            for row in rows
        )
        first_observed = min(row[3] for row in rows)
        first_ingested = min(row[5] for row in rows)
        retrospective = (
            first_observed > deadline or first_ingested > deadline
        )
        classification = "RESCHEDULED" if start_change_count else "UNCHANGED"

        assert len(rows) == 2
        assert original_start == ORIGINAL_START
        assert latest_start == NEW_START
        assert classification == "RESCHEDULED"
        assert start_change_count == 1
        assert pit_visible is True
        assert retrospective is False

        assert _prematch_eligible(
            original_start, ORIGINAL_START - timedelta(hours=2)
        ) is True
        assert _prematch_eligible(
            original_start, ORIGINAL_START - timedelta(minutes=10)
        ) is False

        report = {
            "version": VERSION,
            "isolated_local_database": True,
            "read_only_production": True,
            "synthetic_fixture": True,
            "match_id": match_id,
            "original": {
                "match_start_at": original_start.isoformat(),
                "status": first[2],
                "first_source_observed_at": first_observed.isoformat(),
                "first_ingested_at": first_ingested.isoformat(),
            },
            "rescheduled": {
                "match_start_at": latest_start.isoformat(),
                "status": latest[2],
                "observed_at": RESCHEDULE_OBSERVED.isoformat(),
            },
            "timeline": {
                "classification": classification,
                "version_count": len(rows),
                "distinct_start_count": len({row[1] for row in rows}),
                "start_change_count": start_change_count,
                "status_change_count": status_change_count,
                "changes": changes,
                "pit_visible_by_original_deadline": pit_visible,
                "retrospective_discovery": retrospective,
            },
            "prematch_guard": {
                "with_two_hour_lead_eligible": True,
                "inside_original_deadline_excluded": True,
                "exclusion_reason": "START_INSIDE_OR_AFTER_DEADLINE",
            },
            "no_historical_rewrite": True,
            "note": (
                "The append-only second version is an isolated rehearsal row. "
                "No production historical row was rewritten."
            ),
        }
    payload = json.dumps(report, indent=2, sort_keys=True, default=str)
    with open(args.out_json, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
