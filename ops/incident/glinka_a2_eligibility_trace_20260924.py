from __future__ import annotations

"""Read-only trace of why targeted Sportradar history does not move D. Glinka's
Agent 2 readiness. Uses only rows already paid for. Writes nothing."""

import json
import os
from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.data.agent2_history import (
    _coherent_two_sided_rows,
    historical_match_is_eligible_for_update,
    load_agent2_history,
    resolve_scoring_rule,
)

PLAYER_ID = 4480


def emit(label: str, payload: dict) -> None:
    print(f"{label} {json.dumps(payload, default=str, sort_keys=True)}", flush=True)


def _eligible_reason(observation) -> str | None:
    if observation.outcome_status != "COMPLETED":
        return "no_final_result"
    if observation.surface is None:
        return "surface_missing"
    resolver = resolve_scoring_rule(
        canonical_tournament_name=observation.canonical_tournament_name,
        normalized_tournament_alias=observation.normalized_tournament_alias,
        season_year=observation.season_year,
        tour_scope=observation.tour_scope,
        competition_tier=observation.competition_tier,
        round_code=observation.round_code,
        best_of_sets=observation.best_of_sets,
        qualifying_flag=observation.qualifying_flag,
    )
    if resolver.scoring_rule_version is None:
        return "scoring_rule_unresolved"
    rows = _coherent_two_sided_rows(observation)
    if rows is None:
        return "no_coherent_two_player_stat_cohort"
    p1, p2 = rows
    if not p1.service_points_played or not p2.service_points_played:
        return "insufficient_point_evidence"
    return None


def main() -> int:
    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("GLINKA_TRACE_BLOCKED missing_dsn")
    cutoff = datetime.now(UTC)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")

        sources = conn.execute(
            "SELECT source_system, source_player_id FROM player_source_ids WHERE player_id = %s ORDER BY source_system",
            (PLAYER_ID,),
        ).fetchall()
        emit("GLINKA_SOURCE_IDS", {"player_id": PLAYER_ID, "sources": [
            {"source_system": r[0], "source_player_id": r[1]} for r in sources
        ]})

        # Candidate matches that StEP-2 date scoring believes are completed+statless.
        candidates = conn.execute(
            """
            WITH v AS (
                SELECT DISTINCT mv.match_id, mv.match_start_at, mv.surface::text AS surface
                FROM match_versions mv
                WHERE (mv.player_1_id = %s OR mv.player_2_id = %s)
                  AND mv.match_start_at IS NOT NULL
                  AND mv.match_start_at <= now()
                  AND mv.source_observed_at <= now()
                  AND mv.as_of <= now()
            )
            SELECT v.match_id, v.match_start_at, v.surface,
                   (SELECT count(*) FROM match_stat_observations s WHERE s.match_id = v.match_id)
            FROM v
            WHERE EXISTS (
                SELECT 1 FROM match_result_observations r
                WHERE r.match_id = v.match_id AND r.outcome_status = 'COMPLETED'
            )
              AND NOT EXISTS (
                SELECT 1 FROM match_stat_observations s WHERE s.match_id = v.match_id
              )
            ORDER BY v.match_start_at DESC
            LIMIT 30
            """,
            (PLAYER_ID, PLAYER_ID),
        ).fetchall()
        emit("GLINKA_STATLESS_CANDIDATES", {
            "count": len(candidates),
            "matches": [
                {"match_id": int(r[0]), "match_start_at": r[1], "surface": r[2], "stat_rows": int(r[3])}
                for r in candidates
            ],
        })

        # Newly ingested evidence (last 3 days).
        new_stats = conn.execute(
            """
            SELECT source_system, count(*), count(DISTINCT match_id),
                   count(*) FILTER (WHERE player_id = %s)
            FROM match_stat_observations
            WHERE ingested_at >= now() - interval '3 days'
            GROUP BY source_system ORDER BY source_system
            """,
            (PLAYER_ID,),
        ).fetchall()
        emit("NEW_STAT_SUMMARY", {"rows": [
            {"source_system": r[0], "rows": int(r[1]), "matches": int(r[2]), "glinks_rows": int(r[3])}
            for r in new_stats
        ]})

        glinka_new_stats = conn.execute(
            """
            SELECT match_id, source_system, source_observed_at, as_of
            FROM match_stat_observations
            WHERE player_id = %s AND ingested_at >= now() - interval '3 days'
            ORDER BY ingested_at DESC LIMIT 30
            """,
            (PLAYER_ID,),
        ).fetchall()
        emit("NEW_STATS_FOR_GLINKA", {"count": len(glinka_new_stats), "rows": [
            {"match_id": int(r[0]), "source_system": r[1], "source_observed_at": r[2], "as_of": r[3]}
            for r in glinka_new_stats
        ]})

        # Newly created Sportradar matches and whether Glinka is a participant.
        new_sr = conn.execute(
            """
            SELECT ms.match_id, ms.source_match_id, ms.created_at,
                   (SELECT count(*) FROM match_versions mv
                    WHERE mv.match_id = ms.match_id AND (mv.player_1_id = %s OR mv.player_2_id = %s)),
                   (SELECT string_agg(DISTINCT p.canonical_name, ' | ')
                    FROM match_versions mv
                    JOIN players p ON p.player_id = mv.player_1_id
                    WHERE mv.match_id = ms.match_id),
                   (SELECT string_agg(DISTINCT p.canonical_name, ' | ')
                    FROM match_versions mv
                    JOIN players p ON p.player_id = mv.player_2_id
                    WHERE mv.match_id = ms.match_id)
            FROM match_source_ids ms
            WHERE ms.source_system = 'SPORTRADAR' AND ms.created_at >= now() - interval '3 days'
            ORDER BY ms.created_at DESC LIMIT 40
            """,
            (PLAYER_ID, PLAYER_ID),
        ).fetchall()
        emit("NEW_SPORTRADAR_MATCHES", {"count": len(new_sr), "glinka_matches": sum(1 for r in new_sr if int(r[3]) > 0), "rows": [
            {"match_id": int(r[0]), "source_match_id": r[1], "created_at": r[2], "involves_glinks": int(r[3]) > 0, "p1": r[4], "p2": r[5]}
            for r in new_sr[:20]
        ]})

        # Full A2 view for Glinka from the canonicalized loader.
        observations = load_agent2_history(conn, cutoff=cutoff)
        glinka = [o for o in observations if PLAYER_ID in (o.player_1_id, o.player_2_id)]
        eligible = [o for o in glinka if historical_match_is_eligible_for_update(o)]
        reasons: dict[str, int] = {}
        rejected = []
        for o in glinka:
            if historical_match_is_eligible_for_update(o):
                continue
            reason = _eligible_reason(o) or "unknown"
            reasons[reason] = reasons.get(reason, 0) + 1
            if len(rejected) < 25:
                rejected.append({
                    "match_id": o.match_id,
                    "match_start_at": o.match_start_at,
                    "surface": o.surface,
                    "outcome_status": o.outcome_status,
                    "stat_rows": len(o.stat_rows),
                    "tournament": o.canonical_tournament_name,
                    "reason": reason,
                })
        emit("GLINKA_A2_VIEW", {
            "observations": len(glinka),
            "eligible": len(eligible),
            "rejected": len(glinka) - len(eligible),
            "rejection_reasons": reasons,
            "rejected_examples": rejected,
        })
    emit("GLINKA_TRACE_DONE", {"writes": 0})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
