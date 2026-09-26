"""Read-only diagnosis of replay history cache correction boundaries."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import os
from pathlib import Path

import psycopg

from sports_edge_tennis.replay.loader import (
    ReplayDecisionTimePolicy,
    derive_run_specs_pit_safe,
)

MATCH_IDS_PATH = Path("ops/replay/frozen_population_20260910_20260924_match_ids.txt")


def straddles(cutoffs, old, new):
    if old >= new:
        return False
    idx = bisect_left(cutoffs, old)
    return idx < len(cutoffs) and cutoffs[idx] < new


def main() -> int:
    ids = tuple(int(x) for x in MATCH_IDS_PATH.read_text().splitlines() if x.strip())
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        specs = derive_run_specs_pit_safe(
            conn,
            match_ids=ids,
            policy=ReplayDecisionTimePolicy(),
        )
        cutoffs = sorted({spec.prediction_as_of for spec in specs})
        final = cutoffs[-1]

        result_rows = conn.execute(
            """
            SELECT match_id, GREATEST(source_observed_at, as_of) AS vc
            FROM match_result_observations
            WHERE GREATEST(source_observed_at, as_of) <= %s
            ORDER BY match_id, vc, match_result_observation_id
            """,
            (final,),
        ).fetchall()
        by_match = {}
        for match_id, vc in result_rows:
            by_match.setdefault(int(match_id), []).append(vc)

        result_transitions = []
        for match_id, values in by_match.items():
            ordered = sorted(set(values))
            for old, new in zip(ordered, ordered[1:]):
                if straddles(cutoffs, old, new):
                    result_transitions.append((match_id, old, new))

        stat_rows = conn.execute(
            """
            SELECT match_id, GREATEST(source_observed_at, as_of) AS vc
            FROM match_stat_observations
            WHERE observation_kind = 'RAW_SOURCE'
              AND GREATEST(source_observed_at, as_of) <= %s
            GROUP BY match_id, source_system, source_observed_at, as_of
            HAVING count(*) = 2
               AND count(DISTINCT player_id) = 2
            ORDER BY match_id, vc
            """,
            (final,),
        ).fetchall()
        by_match_stats = {}
        for match_id, vc in stat_rows:
            by_match_stats.setdefault(int(match_id), []).append(vc)

        stat_transitions = []
        for match_id, values in by_match_stats.items():
            ordered = sorted(set(values))
            for old, new in zip(ordered, ordered[1:]):
                if straddles(cutoffs, old, new):
                    stat_transitions.append((match_id, old, new))

    boundaries = sorted(
        {new for _, _, new in result_transitions}
        | {new for _, _, new in stat_transitions}
    )
    segment_counts = {}
    for cutoff in cutoffs:
        segment = bisect_right(boundaries, cutoff)
        segment_counts[segment] = segment_counts.get(segment, 0) + 1

    print(
        "CACHE_DIAG "
        f"specs={len(specs)} cutoffs={len(cutoffs)} "
        f"result_transitions={len(result_transitions)} "
        f"stat_transitions={len(stat_transitions)} "
        f"boundaries={len(boundaries)} segments={len(segment_counts)}"
    )
    print("CACHE_DIAG_SEGMENTS " + ",".join(str(v) for _, v in sorted(segment_counts.items())))
    for kind, rows in (("RESULT", result_transitions), ("STAT", stat_transitions)):
        for match_id, old, new in rows[:50]:
            first_idx = bisect_left(cutoffs, old)
            first_cutoff = cutoffs[first_idx] if first_idx < len(cutoffs) and cutoffs[first_idx] < new else None
            print(
                f"CACHE_DIAG_{kind} match={match_id} old={old.isoformat()} "
                f"new={new.isoformat()} first_straddled_cutoff="
                f"{first_cutoff.isoformat() if first_cutoff else '-'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
