"""Read-only matched Manager vs current original-aligned Model Picks.

Unlike comparing two standalone win rates, match IDs and first-issued
timestamps are joined. Manager/Original match may have a different issued
side, which MUST be shown, not silently treated as a rejected pick.

Run only with authorized read-only PostgreSQL; no write/snapshot mutation.
The output contains individual IDs and must remain private.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sports_edge_tennis.dashboard.aligned_model_picks import (  # noqa: E402
    aligned_production_model_picks_cte,
)
from sports_edge_tennis.performance.regimes import (  # noqa: E402
    first_production_model_picks_cte,
)

MANILA = ZoneInfo("Asia/Manila")


def matched_sql() -> str:
    return f"""
        WITH {aligned_production_model_picks_cte()},
             {first_production_model_picks_cte()},
        cohorts AS (
            SELECT COALESCE(a.match_id, m.match_id) AS match_id,
                   a.picked_at AS aligned_picked_at,
                   a.selected_side AS aligned_side,
                   a.aligned_probability,
                   a.aligned_pick_type,
                   m.picked_at AS manager_picked_at,
                   m.selected_side AS manager_side,
                   m.consensus_probability AS manager_probability
            FROM aligned_model_picks a
            FULL OUTER JOIN first_model_picks m USING (match_id)
        ),
        snapshots AS (
            SELECT c.*, mv.match_start_at,
                COALESCE(
                    (mv.match_start_at AT TIME ZONE 'Asia/Manila')::date,
                    (COALESCE(c.aligned_picked_at, c.manager_picked_at)
                        AT TIME ZONE 'Asia/Manila')::date
                ) AS day,
                result.outcome_status, result.winner_side,
                result.ingested_at AS result_ingested_at
            FROM cohorts c
            LEFT JOIN LATERAL (
                SELECT x.match_start_at
                FROM match_versions x
                WHERE x.match_id = c.match_id
                  AND x.as_of <= COALESCE(c.aligned_picked_at, c.manager_picked_at)
                  AND x.source_observed_at <= COALESCE(c.aligned_picked_at, c.manager_picked_at)
                  AND x.ingested_at <= COALESCE(c.aligned_picked_at, c.manager_picked_at)
                ORDER BY x.as_of DESC, x.source_observed_at DESC,
                         x.match_version_id DESC LIMIT 1
            ) mv ON TRUE
            LEFT JOIN LATERAL (
                SELECT x.outcome_status::text AS outcome_status,
                       x.winner_side, x.ingested_at
                FROM match_result_observations x
                WHERE x.match_id=c.match_id
                  AND x.outcome_status IN ('COMPLETED','RETIRED','WALKOVER','CANCELLED')
                  AND x.source_observed_at <= %(as_of)s
                  AND x.as_of <= %(as_of)s
                  AND x.ingested_at <= %(as_of)s
                ORDER BY x.as_of DESC,x.source_observed_at DESC,
                         x.match_result_observation_id DESC LIMIT 1
            ) result ON TRUE
        )
        SELECT match_id, day, match_start_at,
               aligned_picked_at, aligned_side, aligned_probability,
               aligned_pick_type, manager_picked_at, manager_side,
               manager_probability, outcome_status, winner_side,
               result_ingested_at
        FROM snapshots
        WHERE day BETWEEN %(start)s AND %(end)s
          AND (aligned_picked_at <= %(as_of)s OR manager_picked_at <= %(as_of)s)
        ORDER BY day, match_id
    """


def side_result(side, terminal, winner):
    if side not in (1, 2):
        return None
    if terminal == "COMPLETED" and winner in (1, 2):
        return "WON" if side == winner else "LOST"
    if terminal in ("WALKOVER", "RETIRED", "CANCELLED"):
        return "VOID"
    return "PENDING"


def compare(rows):
    cases = []
    seen = set()
    for row in rows:
        (mid, day, kickoff, issued_a, side_a, prob_a, kind,
         issued_m, side_m, prob_m, terminal, winner, ingested) = row
        if mid in seen:
            raise ValueError(f"duplicate manager comparison match {mid}")
        seen.add(mid)
        has_a = issued_a is not None and side_a in (1, 2)
        has_m = issued_m is not None and side_m in (1, 2)
        if has_a and has_m:
            bucket = "MATCHED_SAME_SIDE" if side_a == side_m else "MATCHED_DIFFERENT_SIDE"
        elif has_a:
            bucket = "ORIGINAL_ONLY_NOT_MANAGER"
        elif has_m:
            bucket = "MANAGER_ONLY_NOT_ORIGINAL"
        else:
            raise ValueError("match missing both cohorts")
        # A later Manager issue is not an independent replay of an earlier
        # original pick; keep both timestamps and flag post-start decisions.
        post_start = bool(kickoff and (
            (has_a and issued_a >= kickoff) or
            (has_m and issued_m >= kickoff)
        ))
        cases.append({
            "match_id": int(mid), "match_date": day.isoformat(),
            "bucket": bucket, "original_type": kind,
            "original_first_issued": issued_a.isoformat() if issued_a else None,
            "manager_first_issued": issued_m.isoformat() if issued_m else None,
            "original_side": side_a, "manager_side": side_m,
            "original_probability": str(prob_a) if prob_a is not None else None,
            "manager_probability": str(prob_m) if prob_m is not None else None,
            "original_result": side_result(side_a, terminal, winner),
            "manager_result": side_result(side_m, terminal, winner),
            "post_kickoff_issue_requires_exclusion": post_start,
            "result_observed_via_terminal": terminal,
            "result_ingested_at": ingested.isoformat() if ingested else None,
        })
    buckets = {}
    for bucket in sorted(set(x["bucket"] for x in cases)):
        group = [x for x in cases if x["bucket"] == bucket]
        item = {
            "picks": len(group),
            "original_won": sum(x["original_result"] == "WON" for x in group),
            "original_lost": sum(x["original_result"] == "LOST" for x in group),
            "manager_won": sum(x["manager_result"] == "WON" for x in group),
            "manager_lost": sum(x["manager_result"] == "LOST" for x in group),
            "unsafe_post_start": sum(
                x["post_kickoff_issue_requires_exclusion"] for x in group
            ),
        }
        for prefix in ("original", "manager"):
            n = item[f"{prefix}_won"]+item[f"{prefix}_lost"]
            item[f"{prefix}_graded_accuracy"] = item[f"{prefix}_won"]/n if n else None
        buckets[bucket] = item
    return {
        "scope": "EXPLORATORY_READ_ONLY_MATCH_ID_MERGE",
        "matched_buckets": buckets, "total_distinct_matches": len(cases),
        "cases": cases, "causal_claim": "NONE",
        "warning": (
            "Stand-alone Manager and aligned original populations are different. "
            "Observed matched same-side differences are timing/selection effects, "
            "not proof that Manager improved or damaged a pick."
        ),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if (args.start > args.end or args.as_of.tzinfo is None
            or args.as_of.utcoffset() is None or args.out.exists()):
        p.error("invalid dates or output already exists")
    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        p.error("approved read-only DATABASE_URL is required")
    import psycopg
    with psycopg.connect(dsn) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(
            matched_sql(), {
                "start": args.start, "end": args.end,
                "as_of": args.as_of,
            }
        ).fetchall()
    payload = compare(rows)
    payload.update({
        "as_of": args.as_of.isoformat(),
        "date_start": args.start.isoformat(),
        "date_end": args.end.isoformat(),
    })
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print("MATCHED_MANAGER_AUDIT="+str(args.out))


if __name__ == "__main__":
    main()
