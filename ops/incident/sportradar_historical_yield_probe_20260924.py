from __future__ import annotations

"""Bounded Sportradar historical yield probe (<=10 requests, read-only).

Fetches a small representative set of historical daily summaries and measures
entitlement, event yield, active-player coverage, and final-stat usability for
the current production active players (runs 505-507). Makes no writes.
"""

import json
import os
from collections import Counter

import psycopg

from sports_edge_tennis.live.credentials import load_secret
from sports_edge_tennis.live.sportradar_benchmark import deterministic_name_keys
from sports_edge_tennis.live.sportradar_fixtures import (
    SportradarFixtureError,
    parse_summary,
)
from sports_edge_tennis.live.sportradar_history_refresh import _parse_final_stats
from sports_edge_tennis.live.target_fixtures import fetch_sportradar_fixture_rows
from sports_edge_tennis.live.transport import TransportError

from datetime import date

RUN_IDS = (505, 506, 507)
PROBE_DATES = (
    date.fromisoformat("2026-09-07"),
    date.fromisoformat("2026-08-24"),
    date.fromisoformat("2026-07-21"),
    date.fromisoformat("2026-07-14"),
    date.fromisoformat("2026-06-29"),
    date.fromisoformat("2026-06-22"),
    date.fromisoformat("2026-05-18"),
    date.fromisoformat("2026-04-03"),
    date.fromisoformat("2026-03-17"),
    date.fromisoformat("2026-02-07"),
)


def _active_players(conn) -> dict[str, set[int]]:
    rows = conn.execute(
        """
        WITH targets AS (
            SELECT DISTINCT match_id FROM prediction_run_targets
            WHERE prediction_run_id = ANY(%s)
        ),
        latest AS (
            SELECT DISTINCT ON (mv.match_id) mv.match_id, mv.player_1_id, mv.player_2_id
            FROM match_versions mv
            WHERE mv.match_id IN (SELECT match_id FROM targets)
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC, mv.match_version_id DESC
        )
        SELECT DISTINCT p.player_id, p.canonical_name
        FROM (
            SELECT player_1_id AS pid FROM latest UNION SELECT player_2_id FROM latest
        ) x
        JOIN players p ON p.player_id = x.pid
        """
        ,
        (list(RUN_IDS),),
    ).fetchall()
    keyed: dict[str, set[int]] = {}
    for pid, name in rows:
        for key in deterministic_name_keys(str(name)):
            keyed.setdefault(key, set()).add(int(pid))
    return keyed


def main() -> int:
    api_key = load_secret("SPORTRADAR_TENNIS_API_KEY")
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not api_key or not dsn:
        print("SR_YIELD_PROBE_BLOCKED missing_key_or_dsn")
        return 2

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        active_keys = _active_players(conn)
        conn.rollback()

    requests = 0
    entitlement = Counter()
    totals: Counter[str] = Counter()
    per_date = []
    for target in PROBE_DATES:
        entry: dict[str, object] = {"date": target.isoformat()}
        try:
            received_at, summaries = fetch_sportradar_fixture_rows(api_key, target)
            requests += 1
            entitlement["OK"] += 1
        except TransportError as exc:
            entitlement[exc.kind] += 1
            entry["entitlement"] = exc.kind
            per_date.append(entry)
            continue
        entry["summaries"] = len(summaries)
        parsed = rejected = completed = active_matches = usable_stats = 0
        for summary in summaries:
            try:
                fixture = parse_summary(summary, received_at=received_at)
            except SportradarFixtureError:
                rejected += 1
                continue
            parsed += 1
            if fixture.match_status == "COMPLETED":
                completed += 1
            p1 = deterministic_name_keys(fixture.player_1_name)
            p2 = deterministic_name_keys(fixture.player_2_name)
            hit1 = any(k in active_keys for k in p1)
            hit2 = any(k in active_keys for k in p2)
            if hit1 or hit2:
                active_matches += 1
                if fixture.match_status == "COMPLETED":
                    try:
                        _parse_final_stats(summary, fixture=fixture)
                        usable_stats += 1
                    except ValueError:
                        pass
        entry.update(
            parsed=parsed,
            rejected=rejected,
            completed=completed,
            active_player_matches=active_matches,
            completed_active_with_final_stats=usable_stats,
        )
        per_date.append(entry)
        totals["summaries"] += len(summaries)
        totals["parsed"] += parsed
        totals["rejected"] += rejected
        totals["completed"] += completed
        totals["active_player_matches"] += active_matches
        totals["completed_active_with_final_stats"] += usable_stats

    print(
        "SR_HISTORICAL_YIELD "
        + json.dumps(
            {
                "transaction_read_only": read_only,
                "writes": 0,
                "requests": requests,
                "entitlement": dict(entitlement),
                "totals": dict(totals),
                "per_date": per_date,
            },
            sort_keys=True,
        )[:12000]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
