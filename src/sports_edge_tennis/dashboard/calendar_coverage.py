"""Read-only ATP/Challenger per-week production inventory.

Known canonical API-Tennis fixtures are not an exhaustive future provider
calendar. Zero rows means unverified, not that no tournament exists.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
import psycopg

WEEKLY_SQL = """
WITH latest AS (
 SELECT DISTINCT ON (mv.match_id) mv.match_id, mv.match_start_at,
        mv.match_status::text AS match_status, mv.tour_scope::text AS tour_scope,
        mv.competition_tier::text AS tier, mv.qualifying_flag, mv.source_observed_at
 FROM match_versions mv
 WHERE mv.as_of <= %(as_of)s AND mv.source_observed_at <= %(as_of)s
 ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
          mv.match_version_id DESC
)
SELECT latest.match_id, latest.match_start_at, latest.match_status,
       latest.tour_scope, latest.tier, latest.qualifying_flag,
       t.canonical_name,
       EXISTS (
         SELECT 1 FROM prediction_run_targets prt JOIN prediction_runs pr
             ON pr.prediction_run_id = prt.prediction_run_id
         WHERE prt.match_id = latest.match_id
           AND pr.prediction_contract_version = 'predictive_agent_result_v1'
           AND pr.run_status = 'COMPLETED'
           AND pr.prediction_finalized_at IS NOT NULL
           AND pr.experiment_id IS NULL
       ) AS forward_evaluated,
       (SELECT mro.outcome_status::text FROM match_result_observations mro
        WHERE mro.match_id = latest.match_id
          AND mro.as_of <= %(as_of)s
        ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
                 mro.match_result_observation_id DESC LIMIT 1) AS result_status
FROM latest
JOIN matches m ON m.match_id = latest.match_id
JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
JOIN tournaments t ON t.tournament_id = te.tournament_id
WHERE latest.match_start_at >= %(start_at)s
  AND latest.match_start_at < %(end_at)s
  AND latest.tour_scope IN ('ATP_MAIN','CHALLENGER')
  AND EXISTS (
      SELECT 1 FROM match_source_ids msi
      WHERE msi.match_id = latest.match_id AND msi.source_system = 'API_TENNIS')
ORDER BY latest.match_start_at, latest.match_id
"""

HISTORY_SQL = """
WITH latest AS (
 SELECT DISTINCT ON (mv.match_id) mv.match_id, mv.match_start_at,
        mv.tour_scope::text AS tour_scope
 FROM match_versions mv
 WHERE mv.as_of <= %(as_of)s AND mv.source_observed_at <= %(as_of)s
 ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
          mv.match_version_id DESC
)
SELECT EXTRACT(YEAR FROM latest.match_start_at)::int AS year,
       latest.tour_scope,
       count(*)::int AS canonical_matches,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM match_source_ids msi
         WHERE msi.match_id = latest.match_id
           AND msi.source_system = 'API_TENNIS'))::int AS api_tennis_linked,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM match_result_observations mro
         WHERE mro.match_id = latest.match_id
           AND mro.outcome_status = 'COMPLETED'
           AND mro.winner_side IN (1,2)
           AND mro.as_of <= %(as_of)s))::int AS completed_result_matches
FROM latest
WHERE latest.match_start_at < %(as_of)s
  AND latest.tour_scope IN ('ATP_MAIN','CHALLENGER')
GROUP BY 1,2 ORDER BY 1,2
"""

SPECIAL_NAMES = ("davis cup","united cup","laver cup","atp cup",
                 "atp finals","next gen")


def load_seasonal_coverage(conn: psycopg.Connection, *,
                           start_date: date, end_date: date,
                           as_of: datetime | None = None) -> dict[str, Any]:
    if end_date < start_date or (end_date - start_date).days > 84:
        raise ValueError("invalid calendar window: maximum 85 days")
    now = as_of or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    rows = conn.execute(WEEKLY_SQL, {
        "as_of": now,
        "start_at": datetime.combine(start_date, time.min, UTC),
        "end_at": datetime.combine(end_date + timedelta(days=1), time.min, UTC)
    }).fetchall()
    historical = conn.execute(HISTORY_SQL, {"as_of": now}).fetchall()
    empty = lambda: {"fixtures":0, "forward_evaluated":0, "scheduled":0,
                     "completed":0, "other_terminal":0, "unresolved":0,
                     "special_tournament_review":0}
    buckets = defaultdict(empty)
    seen: set[int] = set()
    for match_id, start, status, tour, tier, qualifying, name, evaluated, result in rows:
        if match_id in seen:
            raise ValueError("weekly fixture inventory has duplicate match_id")
        seen.add(match_id)
        monday = start.date() - timedelta(days=start.date().weekday())
        group = "CHALLENGER" if tour == "CHALLENGER" else "ATP_TOUR"
        draw = "QUALIFYING" if qualifying else "MAIN_DRAW"
        bucket = buckets[(monday, group, draw)]
        bucket["fixtures"] += 1
        bucket["forward_evaluated"] += int(bool(evaluated))
        bucket["scheduled"] += int(status == "SCHEDULED")
        bucket["completed"] += int(result == "COMPLETED")
        bucket["other_terminal"] += int(result in ("RETIRED","WALKOVER","CANCELLED"))
        bucket["unresolved"] += int(result is None)
        bucket["special_tournament_review"] += int(
            any(term in str(name).casefold() for term in SPECIAL_NAMES)
        )

    weeks = []
    monday = start_date - timedelta(days=start_date.weekday())
    while monday <= end_date:
        week_end = monday + timedelta(days=6)
        groups = [
            {"tour": tour, "draw": draw, **buckets[(monday, tour, draw)]}
            for tour in ("ATP_TOUR","CHALLENGER")
            for draw in ("MAIN_DRAW","QUALIFYING")
        ]
        weeks.append({
            "week_start": monday.isoformat(), "week_end": week_end.isoformat(),
            "requested_start": max(monday,start_date).isoformat(),
            "requested_end": min(week_end,end_date).isoformat(),
            "groups": groups,
            "fixtures": sum(g["fixtures"] for g in groups),
            "forward_evaluated": sum(g["forward_evaluated"] for g in groups),
            "provider_full_week_verified": False,
            "coverage_state": "PERSISTED_ONLY_NOT_FULL_PROVIDER_SCAN",
        })
        monday += timedelta(days=7)

    return {
        "generated_at": now.isoformat(), "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(), "source": "API_TENNIS_CANONICAL_DB",
        "scope": "men's ATP/Challenger singles; no ITF, WTA or doubles",
        "distinct_rule": "latest canonical match_id; no hourly rerun duplication",
        "forward_rule": "finalized completed non-experiment prediction target",
        "limitation": (
            "Database-only inventory, not exhaustive API-Tennis future provider scan. "
            "Zero can mean not yet fetched; cannot claim zero tournaments or full "
            "subscription coverage. Special events and teams require scope review."
        ),
        "weeks": weeks,
        "history": [
            {"year": int(year),
             "tour": "CHALLENGER" if tour == "CHALLENGER" else "ATP_TOUR",
             "canonical_matches": int(all_matches),
             "api_tennis_linked": int(linked),
             "completed_result_matches": int(completed)}
            for year,tour,all_matches,linked,completed in historical
        ],
        "history_limitation": (
            "Stored match/result availability does not prove current historical "
            "API subscription entitlements or pre-match bookmaker odds readiness."
        )
    }
