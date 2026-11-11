"""Private, read-only Nov 9 selection ledger audit.

Run from an authorized environment with a readonly TENNIS_DATABASE_URL.
Compares SAME immutable first-issued aligned Model Picks at two as-of
timestamps. Reports the actual 28 >=70% selections including voids,
and every source-backed status transition. Never rewrites results.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sports_edge_tennis.dashboard.aligned_model_picks import (
    aligned_production_model_picks_cte,
)

MANILA = ZoneInfo("Asia/Manila")
REPORT_DAY = date(2026, 11, 9)
FLOOR = 0.70
STATUSES = ("WON", "LOST", "VOID", "PENDING")


def query() -> str:
    return f"""
        WITH {aligned_production_model_picks_cte()},
        candidate AS (
            SELECT p.*,
                mv.match_start_at AS original_kickoff,
                mv.player_1_id, mv.player_2_id,
                COALESCE(
                    (mv.match_start_at AT TIME ZONE 'Asia/Manila')::date,
                    (p.picked_at AT TIME ZONE 'Asia/Manila')::date
                ) AS report_date
            FROM aligned_model_picks p
            LEFT JOIN LATERAL (
                SELECT x.match_start_at, x.player_1_id, x.player_2_id
                FROM match_versions x
                WHERE x.match_id = p.match_id
                  AND x.as_of <= p.picked_at
                  AND x.source_observed_at <= p.picked_at
                  AND x.ingested_at <= p.picked_at
                ORDER BY x.as_of DESC, x.source_observed_at DESC,
                         x.match_version_id DESC LIMIT 1
            ) mv ON TRUE
        )
        SELECT p.match_id, p.picked_at, p.original_kickoff,
               p.selected_side, p.aligned_probability, p.aligned_pick_type,
               pl1.canonical_name AS player_1, pl2.canonical_name AS player_2,
               observed.outcome_status, observed.winner_side,
               observed.as_of AS result_as_of,
               observed.source_observed_at AS result_source_observed_at,
               observed.ingested_at AS result_ingested_at,
               current_fixture.match_status::text AS latest_fixture_status,
               current_fixture.match_start_at AS latest_start,
               current_fixture.source_observed_at AS fixture_source_observed_at,
               current_fixture.ingested_at AS fixture_ingested_at
        FROM candidate p
        LEFT JOIN players pl1 ON pl1.player_id=p.player_1_id
        LEFT JOIN players pl2 ON pl2.player_id=p.player_2_id
        LEFT JOIN LATERAL (
            SELECT mro.outcome_status::text AS outcome_status, mro.winner_side,
                   mro.as_of, mro.source_observed_at, mro.ingested_at
            FROM match_result_observations mro
            WHERE mro.match_id=p.match_id
              AND mro.outcome_status IN (
                  'COMPLETED','RETIRED','WALKOVER','CANCELLED'
              )
              AND mro.as_of <= %(as_of)s
              AND mro.source_observed_at <= %(as_of)s
              AND mro.ingested_at <= %(as_of)s
            ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
                     mro.match_result_observation_id DESC LIMIT 1
        ) observed ON TRUE
        LEFT JOIN LATERAL (
            SELECT mv.match_start_at, mv.match_status,
                   mv.source_observed_at, mv.ingested_at
            FROM match_versions mv
            WHERE mv.match_id=p.match_id
              AND mv.as_of <= %(as_of)s
              AND mv.source_observed_at <= %(as_of)s
              AND mv.ingested_at <= %(as_of)s
            ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                     mv.match_version_id DESC LIMIT 1
        ) current_fixture ON TRUE
        WHERE p.report_date = %(report_day)s
          AND p.picked_at <= %(as_of)s
        ORDER BY p.match_id
    """


def classify(terminal: str | None, winner: int | None, side: int) -> str:
    if terminal == "COMPLETED" and winner in (1, 2):
        return "WON" if winner == side else "LOST"
    if terminal in ("RETIRED", "WALKOVER", "CANCELLED"):
        return "VOID"
    return "PENDING"


def _iso(d):
    return d.isoformat() if d is not None else None


def parse_rows(rows, as_of: datetime) -> dict[int, dict]:
    out = {}
    for raw in rows:
        (mid, issued, kickoff, side, prob, kind, p1, p2,
         terminal, winner, result_asof, result_seen, result_ingested,
         fixture_status, latest_start, fixture_seen, fixture_ingested) = raw
        mid = int(mid)
        if mid in out:
            raise ValueError(f"duplicate original match_id {mid}")
        if side not in (1, 2) or issued > as_of:
            raise ValueError(f"invalid issue timing or selected side {mid}")
        if any(t is not None and t > as_of for t in (
            result_asof, result_seen, result_ingested, fixture_seen,
            fixture_ingested,
        )):
            raise ValueError(f"post-cutoff observation leaked into {mid}")
        record = {
            "match_id": mid,
            "players": [p1, p2],
            "selected_side": int(side),
            "original_probability": float(prob),
            "qualified_ge70": float(prob) >= FLOOR,
            "original_issue_at": _iso(issued),
            "original_kickoff": _iso(kickoff),
            "issued_before_kickoff": (
                issued < kickoff if kickoff is not None else None
            ),
            "aligned_type": kind,
            "status": classify(terminal, winner, side),
            "terminal_type": terminal,
            "winner_side": winner,
            "result_as_of": _iso(result_asof),
            "result_source_observed_at": _iso(result_seen),
            "result_ingested_at": _iso(result_ingested),
            "provider_fixture_status": fixture_status,
            "latest_start_at": _iso(latest_start),
            "fixture_source_observed_at": _iso(fixture_seen),
            "fixture_ingested_at": _iso(fixture_ingested),
        }
        out[mid] = record
    return out


def _counts(records) -> dict:
    values = list(records)
    status = Counter(r["status"] for r in values)
    graded = status["WON"] + status["LOST"]
    return {
        "selections": len(values),
        **{key.lower(): status[key] for key in STATUSES},
        "graded": graded,
        "accuracy": status["WON"] / graded if graded else None,
    }


def audit(before: dict[int, dict], after: dict[int, dict],
          *, before_time: datetime, after_time: datetime,
          expected_total: int = 28) -> dict:
    if not before_time < after_time:
        raise ValueError("as-of timestamps out of order")
    if set(before) != set(after):
        raise ValueError("first-issued match ID population changed; require investigation")
    if any(before[mid]["qualified_ge70"] != after[mid]["qualified_ge70"] or
           before[mid]["selected_side"] != after[mid]["selected_side"] or
           before[mid]["original_issue_at"] != after[mid]["original_issue_at"]
           for mid in before):
        raise ValueError("immutable original prediction changed")
    filtered_before = [x for x in before.values() if x["qualified_ge70"]]
    filtered_after = [x for x in after.values() if x["qualified_ge70"]]
    if len(filtered_before) != expected_total or len(filtered_after) != expected_total:
        raise ValueError("qualified count changed or differs from claimed 28")
    transitions = []
    for mid in sorted(before):
        a, b = before[mid], after[mid]
        if a["status"] != b["status"]:
            transitions.append({
                "match_id": mid, "players": b["players"],
                "before": a["status"], "after": b["status"],
                "source_observed_at": b["result_source_observed_at"],
                "result_ingested_at": b["result_ingested_at"],
                "terminal_type": b["terminal_type"],
                "qualified_ge70": b["qualified_ge70"],
            })
    return {
        "report": "NOVEMBER_9_FIXED_FIRST_ISSUED_AUDIT",
        "timezone": "Asia/Manila",
        "match_date": REPORT_DAY.isoformat(),
        "before_as_of": before_time.isoformat(),
        "after_as_of": after_time.isoformat(),
        "original_before": _counts(before.values()),
        "original_after": _counts(after.values()),
        "qualified_before": _counts(filtered_before),
        "qualified_after": _counts(filtered_after),
        "changed_results": transitions,
        "all_qualified_original_selections": [
            {"before_status": before[mid]["status"], **after[mid]}
            for mid in sorted(after) if after[mid]["qualified_ge70"]
        ],
        "unverified_issuance": [
            mid for mid in sorted(after)
            if after[mid]["issued_before_kickoff"] is not True
        ],
        "audit_status": "PASS" if all(
            after[mid]["issued_before_kickoff"] is True for mid in after
        ) else "PIT_ISSUE_TIME_REVIEW_REQUIRED",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=datetime.fromisoformat, required=True)
    parser.add_argument("--after", type=datetime.fromisoformat, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if (any(t.tzinfo is None or t.utcoffset() is None for t in
            (args.before, args.after)) or args.before >= args.after):
        parser.error("timestamps must be timezone aware, in order")
    if args.out.exists():
        parser.error("refusing to overwrite existing evidence")
    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        parser.error("read-only database URL not available in current environment")
    import psycopg
    with psycopg.connect(dsn) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows_a = conn.execute(
            query(), {"as_of": args.before, "report_day": REPORT_DAY},
        ).fetchall()
        rows_b = conn.execute(
            query(), {"as_of": args.after, "report_day": REPORT_DAY},
        ).fetchall()
    report = audit(
        parse_rows(rows_a, args.before), parse_rows(rows_b, args.after),
        before_time=args.before, after_time=args.after,
    )
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as writer:
        json.dump(report, writer, indent=2, sort_keys=True)
        writer.write("\n")
    print("NOV09_AUDIT="+str(args.out)+" STATUS="+report["audit_status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
