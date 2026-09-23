from __future__ import annotations

"""Read-only surface-source and A3 shared/network audit for runs 505-507.

Determines every available deterministic surface source per target and quantifies
A3 indirect (shared-opponent) support. No writes, no provider calls.
"""

import argparse
import json
import os
from collections import Counter

import psycopg

RUN_IDS = (505, 506, 507)


def _targets(conn) -> list[dict]:
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (mv.match_id)
                mv.match_id, mv.player_1_id, mv.player_2_id,
                mv.surface::text AS mv_surface,
                m.tournament_edition_id, te.tournament_id,
                te.surface::text AS edition_surface,
                mv.tour_scope::text AS tour_scope,
                mv.competition_tier::text AS competition_tier,
                mv.qualifying_flag
            FROM match_versions mv
            JOIN matches m ON m.match_id = mv.match_id
            JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
            WHERE mv.match_id IN (
                SELECT match_id FROM prediction_run_targets
                WHERE prediction_run_id = ANY(%s)
            )
            ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC
        )
        SELECT match_id, player_1_id, player_2_id, mv_surface, tournament_edition_id,
               tournament_id, edition_surface, tour_scope, competition_tier, qualifying_flag
        FROM latest
        ORDER BY match_id
        """,
        (list(RUN_IDS),),
    ).fetchall()
    return [
        {
            "match_id": int(r[0]),
            "p1": int(r[1]),
            "p2": int(r[2]),
            "mv_surface": r[3],
            "edition_id": int(r[4]),
            "tournament_id": int(r[5]),
            "edition_surface": r[6],
            "tour_scope": r[7],
            "tier": r[8],
            "qualifying": bool(r[9]),
        }
        for r in rows
    ]


def _tournament_surface_history(conn, tournament_id: int) -> tuple[set[str], set[str]]:
    editions = conn.execute(
        """
        SELECT DISTINCT surface::text
        FROM tournament_editions
        WHERE tournament_id = %s AND surface IS NOT NULL
        """,
        (tournament_id,),
    ).fetchall()
    matches = conn.execute(
        """
        SELECT DISTINCT mv.surface::text
        FROM match_versions mv
        JOIN matches m ON m.match_id = mv.match_id
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        WHERE te.tournament_id = %s AND mv.surface IS NOT NULL
        """,
        (tournament_id,),
    ).fetchall()
    return (
        {str(r[0]) for r in editions if r[0]},
        {str(r[0]) for r in matches if r[0]},
    )


def _shared_opponents(conn, p1: int, p2: int) -> int:
    row = conn.execute(
        """
        WITH edges AS (
            SELECT DISTINCT mro.match_id, mv.player_1_id AS a, mv.player_2_id AS b
            FROM match_result_observations mro
            JOIN LATERAL (
                SELECT player_1_id, player_2_id FROM match_versions
                WHERE match_id = mro.match_id
                ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
            WHERE mro.outcome_status = 'COMPLETED'
        ),
        p1_opp AS (
            SELECT CASE WHEN a = %s THEN b ELSE a END AS opp FROM edges WHERE a = %s OR b = %s
        ),
        p2_opp AS (
            SELECT CASE WHEN a = %s THEN b ELSE a END AS opp FROM edges WHERE a = %s OR b = %s
        )
        SELECT count(*) FROM (SELECT opp FROM p1_opp INTERSECT SELECT opp FROM p2_opp) shared
        """,
        (p1, p1, p1, p2, p2, p2),
    ).fetchone()
    return int(row[0])


def _direct(conn, p1: int, p2: int) -> int:
    row = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT DISTINCT mro.match_id
            FROM match_result_observations mro
            JOIN LATERAL (
                SELECT player_1_id, player_2_id FROM match_versions
                WHERE match_id = mro.match_id
                ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
                LIMIT 1
            ) mv ON TRUE
            WHERE mro.outcome_status = 'COMPLETED'
              AND ((mv.player_1_id = %s AND mv.player_2_id = %s)
                   OR (mv.player_1_id = %s AND mv.player_2_id = %s))
        ) t
        """,
        (p1, p2, p2, p1),
    ).fetchone()
    return int(row[0])


def _adjacency(conn, player_ids: list[int]) -> dict[int, int]:
    rows = conn.execute(
        """
        SELECT mv.player_1_id, mv.player_2_id
        FROM match_result_observations mro
        JOIN LATERAL (
            SELECT player_1_id, player_2_id FROM match_versions
            WHERE match_id = mro.match_id
            ORDER BY as_of DESC, source_observed_at DESC, match_version_id DESC
            LIMIT 1
        ) mv ON TRUE
        WHERE mro.outcome_status = 'COMPLETED'
          AND (mv.player_1_id = ANY(%s) OR mv.player_2_id = ANY(%s))
        """,
        (list(player_ids), list(player_ids)),
    ).fetchall()
    deg: dict[int, set[int]] = {}
    for a, b in rows:
        deg.setdefault(int(a), set()).add(int(b))
        deg.setdefault(int(b), set()).add(int(a))
    return {pid: len(v) for pid, v in deg.items()}


def main() -> int:
    argparse.ArgumentParser().parse_args()
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print("SURFACE_A3_AUDIT_BLOCKED missing_dsn")
        return 2

    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        targets = _targets(conn)
        surface_classes = Counter()
        surface_examples: dict[str, list] = {}
        a3 = Counter()
        a3_examples: dict[str, list] = {}
        active = sorted({t["p1"] for t in targets} | {t["p2"] for t in targets})

        cache: dict[int, tuple[set[str], set[str]]] = {}
        for t in targets:
            if t["tournament_id"] not in cache:
                cache[t["tournament_id"]] = _tournament_surface_history(conn, t["tournament_id"])
            editions, historical = cache[t["tournament_id"]]
            if t["mv_surface"]:
                cls = "EXACT_CURRENT_EVENT_SURFACE"
            elif t["edition_surface"]:
                cls = "EXACT_CURRENT_EDITION_SURFACE"
            else:
                combined = editions | historical
                if len(combined) == 1:
                    cls = "EXACT_TOURNAMENT_HISTORICAL_SURFACE"
                elif len(combined) > 1:
                    cls = "CONFLICTING_SURFACE"
                else:
                    cls = "UNRESOLVED"
            surface_classes[cls] += 1
            surface_examples.setdefault(cls, [])
            if len(surface_examples[cls]) < 6:
                surface_examples[cls].append(
                    {
                        "match_id": t["match_id"],
                        "tournament_id": t["tournament_id"],
                        "editions": sorted(editions),
                        "historical": sorted(historical),
                    }
                )

            direct = _direct(conn, t["p1"], t["p2"])
            shared = _shared_opponents(conn, t["p1"], t["p2"])
            if direct >= 2:
                cat = "DIRECT_SUPPORT_OK"
            elif direct == 1:
                cat = "DIRECT_WEAK"
            elif shared >= 1:
                cat = "NO_DIRECT_BUT_NETWORK_EXISTS"
            else:
                cat = "TRUE_MATCHUP_SUPPORT_GAP"
            a3[cat] += 1
            a3_examples.setdefault(cat, [])
            if len(a3_examples[cat]) < 6:
                a3_examples[cat].append(
                    {"match_id": t["match_id"], "direct": direct, "shared": shared}
                )

        adjacency = _adjacency(conn, active)
        conn.rollback()

    print(
        "SURFACE_A3_AUDIT "
        + json.dumps(
            {
                "transaction_read_only": read_only,
                "writes": 0,
                "targets": len(targets),
                "surface_classification": dict(surface_classes),
                "surface_examples": surface_examples,
                "a3_classification": dict(a3),
                "a3_examples": a3_examples,
                "active_players": len(active),
                "active_players_zero_adjacency": sum(1 for p in active if adjacency.get(p, 0) == 0),
            },
            sort_keys=True,
        )[:12000]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
