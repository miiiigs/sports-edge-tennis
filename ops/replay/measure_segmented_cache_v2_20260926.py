"""Measure the final segmented replay cache layout against production evidence."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

from sports_edge_tennis.agents.agent3_intransitivity import DEFAULT_CONFIG as A3_CONFIG
from sports_edge_tennis.replay.history_cache import cache_correction_boundaries, cache_cutoff_segments
from sports_edge_tennis.replay.loader import ReplayDecisionTimePolicy, derive_run_specs_pit_safe

MATCH_IDS_PATH = Path("ops/replay/frozen_population_20260910_20260924_match_ids.txt")


def main() -> int:
    match_ids = tuple(
        int(line.strip())
        for line in MATCH_IDS_PATH.read_text().splitlines()
        if line.strip()
    )
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        read_only = conn.execute("SHOW default_transaction_read_only").fetchone()[0]
        if str(read_only).lower() not in {"on", "true"}:
            raise SystemExit(f"SEGMENT_PROBE_FAIL database_not_read_only value={read_only}")

        specs = derive_run_specs_pit_safe(
            conn,
            match_ids=match_ids,
            policy=ReplayDecisionTimePolicy(),
        )
        cutoffs = sorted({spec.prediction_as_of for spec in specs})
        boundaries = cache_correction_boundaries(conn, cutoffs=cutoffs)
        segments = cache_cutoff_segments(conn, cutoffs=cutoffs)

    print(
        "SEGMENT_PROBE "
        f"matches={len(match_ids)} specs={len(specs)} cutoffs={len(cutoffs)} "
        f"boundaries={len(boundaries)} segments={len(segments)} "
        f"a3_lookback_days={A3_CONFIG.lookback_days}",
        flush=True,
    )
    print(
        "SEGMENT_PROBE_SIZES " + ",".join(str(len(segment)) for segment in segments),
        flush=True,
    )
    for index, segment in enumerate(segments, start=1):
        print(
            "SEGMENT_PROBE_RANGE "
            f"segment={index}/{len(segments)} size={len(segment)} "
            f"first={segment[0].isoformat()} final={segment[-1].isoformat()}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
