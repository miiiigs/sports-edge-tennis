"""Read-only audit of the tournament identity correction layer.

Shows active correction rows, the historical provider mapping target, the
corrected canonical target, target existence/scope, and how many current
scheduled matches still point through the historical (duplicate) edition.

Read-only. Never mutates anything.

Usage:
    TENNIS_DATABASE_URL=... python ops/audit_tournament_identity_corrections.py
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))


def _dsn() -> str | None:
    return os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")


def load_corrections(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.source_system, c.source_tournament_id, c.canonical_tournament_id,
               ct.canonical_name, ct.tour_scope::text, ct.competition_tier::text, c.reason
        FROM tournament_identity_corrections c
        JOIN tournaments ct ON ct.tournament_id = c.canonical_tournament_id
        ORDER BY c.source_system, c.source_tournament_id
        """
    ).fetchall()
    result = []
    for r in rows:
        historical = conn.execute(
            "SELECT ti.tournament_id, t.canonical_name, t.competition_tier::text "
            "FROM tournament_source_ids ti JOIN tournaments t ON t.tournament_id = ti.tournament_id "
            "WHERE ti.source_system = %s AND ti.source_tournament_id = %s",
            (r[0], r[1]),
        ).fetchone()
        scheduled = 0
        if historical is not None:
            scheduled = conn.execute(
                """
                SELECT count(*) FROM tournament_editions te
                JOIN matches m ON m.tournament_edition_id = te.tournament_edition_id
                JOIN LATERAL (
                    SELECT v.match_status FROM match_versions v
                    WHERE v.match_id = m.match_id
                    ORDER BY v.as_of DESC, v.source_observed_at DESC, v.match_version_id DESC LIMIT 1
                ) mv ON TRUE
                WHERE te.tournament_id = %s AND mv.match_status::text NOT IN ('COMPLETED','RETIRED','WALKOVER','CANCELLED')
                """,
                (historical[0],),
            ).fetchone()[0]
        result.append(
            {
                "source_system": r[0],
                "source_tournament_id": r[1],
                "canonical_tournament_id": r[2],
                "canonical_name": r[3],
                "scope": r[4],
                "tier": r[5],
                "reason": r[6],
                "historical_tournament_id": historical[0] if historical else None,
                "historical_name": historical[1] if historical else None,
                "historical_tier": historical[2] if historical else None,
                "historical_scheduled_matches": scheduled,
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit tournament identity corrections")
    parser.add_argument("--dsn", default=_dsn())
    args = parser.parse_args(argv)
    if not args.dsn:
        print("TOURNAMENT_CORRECTION_AUDIT_BLOCKED reason=NO_DATABASE")
        return 2
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        conn.read_only = True
        corrections = load_corrections(conn)
    print(json.dumps(corrections, indent=2))
    print(f"TOURNAMENT_CORRECTION_AUDIT total={len(corrections)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
