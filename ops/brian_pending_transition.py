"""Read-only, per-match pending transition evidence for Brian.

Reconstructs two *historical as-of* snapshots from immutable first-issued
production picks and append-only fixture/result observations. The export
is PRIVATE: never commit match-level player names or credentials.

Example (authorized read-only PostgreSQL connection):
    python ops/brian_pending_transition.py \\
      --before 2026-11-10T11:03:34+00:00 \\
      --after 2026-11-10T15:04:44+00:00 \\
      --expected-before 52 --expected-after 43 \\
      --out /secure/nov10_52_to_43_transitions.json

Historical as-of parity can fail if the diagnostic population itself used
different selection, windowing or update logic. NEVER force a report to 52/43.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sports_edge_tennis.live.pending_result_refresh import (  # noqa: E402
    pending_pick_matches_ctes,
)


def query_as_of() -> str:
    """Shared canonical pick population, no future-known result/fixture."""
    return f"""
        WITH {pending_pick_matches_ctes()}
        SELECT
            p.match_id, p.picked_at, p.pick_source, p.selected_side,
            pit.match_start_at AS original_match_start_at,
            latest.match_start_at AS latest_match_start_at,
            latest.match_status::text AS latest_fixture_status,
            latest.source_observed_at AS fixture_source_observed_at,
            latest.ingested_at AS fixture_ingested_at,
            observed.outcome_status, observed.winner_side,
            observed.source_observed_at AS result_source_observed_at,
            observed.ingested_at AS result_ingested_at,
            (a.match_id IS NOT NULL) AS aligned_original,
            a.aligned_pick_type AS aligned_pick_type
        FROM pick_matches p
        LEFT JOIN aligned_model_picks a
          ON a.match_id = p.match_id AND a.picked_at <= %(cutoff)s
        LEFT JOIN LATERAL (
          SELECT mv.match_start_at FROM match_versions mv
          WHERE mv.match_id = p.match_id
            AND mv.as_of <= p.picked_at
            AND mv.source_observed_at <= p.picked_at
            AND mv.ingested_at <= p.picked_at
          ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                   mv.match_version_id DESC LIMIT 1
        ) pit ON TRUE
        LEFT JOIN LATERAL (
          SELECT mv.match_start_at, mv.match_status,
                 mv.source_observed_at, mv.ingested_at
          FROM match_versions mv
          WHERE mv.match_id = p.match_id
            AND mv.as_of <= %(cutoff)s
            AND mv.source_observed_at <= %(cutoff)s
            AND mv.ingested_at <= %(cutoff)s
          ORDER BY mv.as_of DESC, mv.source_observed_at DESC,
                   mv.match_version_id DESC LIMIT 1
        ) latest ON TRUE
        LEFT JOIN LATERAL (
          SELECT mro.outcome_status::text AS outcome_status,
                 mro.winner_side, mro.source_observed_at, mro.ingested_at
          FROM match_result_observations mro
          WHERE mro.match_id = p.match_id
            AND mro.outcome_status IN (
                'COMPLETED','RETIRED','WALKOVER','CANCELLED'
            )
            AND mro.as_of <= %(cutoff)s
            AND mro.source_observed_at <= %(cutoff)s
            AND mro.ingested_at <= %(cutoff)s
          ORDER BY mro.as_of DESC, mro.source_observed_at DESC,
                   mro.match_result_observation_id DESC LIMIT 1
        ) observed ON TRUE
        WHERE p.picked_at <= %(cutoff)s
          AND p.picked_at >= %(since)s
        ORDER BY p.match_id
    """


def _iso(x):
    return x.isoformat() if x is not None else None


def _outcome(status, winner, selected_side):
    if status == "COMPLETED" and winner in (1, 2):
        return "WON" if winner == selected_side else "LOST"
    if status in ("RETIRED", "WALKOVER", "CANCELLED"):
        return "VOID"
    return "PENDING"


def _snapshot(rows, cutoff):
    """Rows come only from read-only SQL with explicit time fences."""
    result = {}
    for row in rows:
        (mid, picked_at, source, side, original_start,
         latest_start, latest_status, fixture_source_at, fixture_ingested,
         result_status, winner, result_source_at, result_ingested,
         aligned, aligned_kind) = row
        match_id = int(mid)
        if match_id in result:
            raise ValueError("duplicate canonical match in pending population")
        selected_side = int(side) if side is not None else None
        if selected_side not in (1, 2):
            raise ValueError("invalid selected side")
        outcome = _outcome(result_status, winner, selected_side)
        if picked_at > cutoff:
            raise ValueError("future-issued pick leaked into snapshot")
        if result_ingested and result_ingested > cutoff:
            raise ValueError("post-cutoff result leaked into snapshot")
        if fixture_ingested and fixture_ingested > cutoff:
            raise ValueError("post-cutoff fixture leaked into snapshot")
        decision_day = (
            (original_start or picked_at).astimezone(
                __import__("zoneinfo").ZoneInfo("Asia/Manila")
            ).date().isoformat()
        )
        result[match_id] = {
            "match_id": match_id, "picked_at": _iso(picked_at),
            "decision_day": decision_day, "selected_side": selected_side,
            "pick_source": source, "original_aligned": bool(aligned),
            "aligned_pick_type": aligned_kind,
            "status": outcome, "provider_terminal": result_status,
            "provider_winner_side": winner,
            "result_source_observed_at": _iso(result_source_at),
            "result_ingested_at": _iso(result_ingested),
            "latest_fixture_status": latest_status,
            "latest_fixture_start": _iso(latest_start),
            "fixture_source_observed_at": _iso(fixture_source_at),
            "fixture_ingested_at": _iso(fixture_ingested),
        }
    return result


def reconcile(before: dict, after: dict, *,
              expected_before: int | None = None,
              expected_after: int | None = None) -> dict:
    """Exact unique-match transition accounting including new pending picks."""
    a = {mid for mid, x in before.items() if x["status"] == "PENDING"}
    b = {mid for mid, x in after.items() if x["status"] == "PENDING"}
    settled = []
    for mid in sorted(a - b):
        current = after.get(mid)
        if current is None:
            raise ValueError(f"historically issued match disappeared: {mid}")
        if current["status"] == "PENDING":
            raise ValueError("pending match disappeared without terminal result")
        settled.append({
            **current,
            "previous_status": "PENDING",
            "transition": "LEFT_PENDING_"+current["status"],
        })
    new = [{**after[mid], "previous_status": before.get(mid, {}).get("status"),
            "transition": "NEW_PENDING"}
           for mid in sorted(b - a)]
    unchanged = len(a & b)
    if len(a) + len(new) - len(settled) != len(b):
        raise ValueError("pending opening + additions - exits mismatch")
    if expected_before is not None and len(a) != expected_before:
        raise ValueError(f"before count {len(a)} != expected {expected_before}")
    if expected_after is not None and len(b) != expected_after:
        raise ValueError(f"after count {len(b)} != expected {expected_after}")
    by_origin_period = Counter(
        "NOV05_NOV09" if "2026-11-05" <= x["decision_day"] <= "2026-11-09"
        else "OTHER_DAY" for x in settled
    )
    status_by_day = Counter(
        (x["decision_day"], x["status"]) for x in settled
    )
    return {
        "status": "READ_ONLY_HISTORICAL_TRANSITION_RECONCILED",
        "before_pending": len(a), "new_pending": len(new),
        "left_pending": len(settled), "unchanged_pending": unchanged,
        "after_pending": len(b), "left_pending_by_status":
            dict(sorted(Counter(x["status"] for x in settled).items())),
        "left_pending_by_reporting_period": dict(sorted(by_origin_period.items())),
        "left_pending_by_day_and_result": [
            {"date": day, "result": status, "count": count}
            for (day, status), count in sorted(status_by_day.items())
        ],
        "settled_or_void": settled,
        "newly_pending": new,
        "caveat": (
            "A complete match-level database export is required; changing "
            "diagnostic windows, newly issued matches, historical fixture "
            "versions, and delayed provider ingestion may explain why "
            "aggregate log counts are not identical to as-of reconstruction."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=datetime.fromisoformat, required=True)
    parser.add_argument("--after", type=datetime.fromisoformat, required=True)
    parser.add_argument("--since", type=datetime.fromisoformat,
                        default=datetime.fromisoformat("2026-09-01T00:00:00+00:00"))
    parser.add_argument("--expected-before", type=int)
    parser.add_argument("--expected-after", type=int)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if any(x.tzinfo is None or x.utcoffset() is None for x in (
        args.before, args.after, args.since
    )) or args.before >= args.after:
        parser.error("timezone-aware before < after required")
    if args.out.exists():
        parser.error("refusing to overwrite a previous audit")

    dsn = os.getenv("TENNIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        parser.error("authorized read-only DATABASE_URL environment required")
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            query = query_as_of()
            before_rows = conn.execute(
                query, {"cutoff": args.before, "since": args.since},
            ).fetchall()
            after_rows = conn.execute(
                query, {"cutoff": args.after, "since": args.since},
            ).fetchall()
        evidence = reconcile(
            _snapshot(before_rows, args.before),
            _snapshot(after_rows, args.after),
            expected_before=args.expected_before,
            expected_after=args.expected_after,
        )
        evidence["before_as_of"] = args.before.isoformat()
        evidence["after_as_of"] = args.after.isoformat()
    except (ValueError, OSError, ImportError) as exc:
        print(f"PENDING_TRANSITION_BLOCKED: {exc}", file=sys.stderr)
        return 2
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        f"PENDING_TRANSITION={args.out} "
        f"before={evidence['before_pending']} "
        f"new={evidence['new_pending']} "
        f"exits={evidence['left_pending']} "
        f"after={evidence['after_pending']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
