from __future__ import annotations

"""Read-only Agent 2 historical replay for the Sportradar stats rehearsal.

Two explicitly separated evaluation modes are supported and labelled in output:

* ``strict`` (MODE A / production truth): observation timestamps are used exactly
  as stored, so the linked Sportradar results/stats are only visible from the
  September-23 fetch instant onward. This answers "what could production have
  known at the recorded point in time?"
* ``retrospective`` (MODE B / research): history is re-ordered by real match
  chronology in memory only (never persisted, never mutating stored timestamps),
  so each match's final stats become visible to later matches. This is a research
  simulation, not production point-in-time truth.

Both modes are identical Agent 2 mathematics; only the visibility ordering of the
in-memory observations differs. The database stage is a single read-only
transaction that is rolled back and disconnected before any model work.
"""

import argparse
import json
import os
import time
from collections import Counter
from dataclasses import asdict, replace
from datetime import date, datetime, timezone

import psycopg

from sports_edge_tennis.data.agent2_history import (
    Agent2HistoricalMatch,
    load_agent2_history,
    replay_historical_predictions,
)
from sports_edge_tennis.validation.progress import heartbeat

SPORTRADAR_SOURCE = "SPORTRADAR"
MODE_STRICT = "strict"
MODE_RETROSPECTIVE = "retrospective"


def _load_observations_read_only(
    dsn: str,
    *,
    cutoff: datetime,
    year_start: int,
) -> tuple[list[Agent2HistoricalMatch], str, float]:
    started = time.monotonic()
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        if read_only != "on":
            raise RuntimeError("transaction_read_only is not on")
        observations = load_agent2_history(
            conn,
            cutoff=cutoff,
            tour_scopes=("ATP_MAIN", "CHALLENGER"),
            year_start=year_start,
            year_end=None,
        )
        conn.rollback()
    return observations, read_only, time.monotonic() - started


def _as_retrospective(
    observations: list[Agent2HistoricalMatch],
) -> list[Agent2HistoricalMatch]:
    """Re-order visibility by real match chronology, in memory only.

    A small number of canonical rows have no usable match start; those fall back
    to their stored result visibility so they never produce a null ordering key.
    """
    ordered: list[Agent2HistoricalMatch] = []
    for observation in observations:
        anchor = observation.match_start_at or observation.result_source_observed_at
        ordered.append(
            replace(
                observation,
                result_source_observed_at=anchor,
                result_as_of=anchor,
            )
        )
    return ordered


def _without_sportradar_stats(
    observations: list[Agent2HistoricalMatch],
) -> list[Agent2HistoricalMatch]:
    return [
        replace(
            observation,
            stat_rows=tuple(
                row
                for row in observation.stat_rows
                if row.source_system != SPORTRADAR_SOURCE
            ),
        )
        for observation in observations
    ]


def _score(
    records,
    *,
    evaluation_start: date,
    evaluation_end: date,
) -> dict[str, object]:
    sample = predicted = abstained = correct = 0
    sportradar_stat_matches = 0
    usable_stat_matches = 0
    abstain_reasons: Counter[str] = Counter()
    for record in records:
        observation = record.observation
        match_date = (
            observation.match_start_at or observation.result_source_observed_at
        ).date()
        if match_date < evaluation_start or match_date > evaluation_end:
            continue
        if observation.outcome_status != "COMPLETED" or observation.winner_side not in (1, 2):
            continue
        sample += 1
        if observation.stat_rows:
            usable_stat_matches += 1
        if any(row.source_system == SPORTRADAR_SOURCE for row in observation.stat_rows):
            sportradar_stat_matches += 1
        result = record.result
        if result.prediction_status != "PREDICTED":
            abstained += 1
            abstain_reasons[str(result.diagnostics.abstain_reason)] += 1
            continue
        predicted += 1
        correct += 1 if result.selection_side == observation.winner_side else 0
    return {
        "sample": sample,
        "usable_stat_matches": usable_stat_matches,
        "sportradar_stat_matches": sportradar_stat_matches,
        "predicted": predicted,
        "abstained": abstained,
        "correct": correct,
        "incorrect": predicted - correct,
        "coverage": (predicted / sample) if sample else None,
        "accuracy": (correct / predicted) if predicted else None,
        "abstain_reasons": dict(sorted(abstain_reasons.items())),
    }


def _run_variant(
    observations: list[Agent2HistoricalMatch],
    *,
    mode: str,
    evaluation_start: date,
    evaluation_end: date,
) -> dict[str, object]:
    ordered = _as_retrospective(observations) if mode == MODE_RETROSPECTIVE else observations
    started = time.monotonic()
    with heartbeat(f"agent2_replay mode={mode} observations={len(ordered)}"):
        replay = replay_historical_predictions(ordered)
    replay_seconds = time.monotonic() - started
    return {
        "observation_count": len(ordered),
        "replayed_matches": len(replay.records),
        "replay_seconds": replay_seconds,
        "score": _score(
            replay.records,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Agent 2 historical replay")
    parser.add_argument("--mode", choices=[MODE_STRICT, MODE_RETROSPECTIVE], default=MODE_RETROSPECTIVE)
    parser.add_argument("--evaluation-start", type=date.fromisoformat, default=date(2026, 9, 10))
    parser.add_argument("--evaluation-end", type=date.fromisoformat, default=date(2026, 9, 21))
    parser.add_argument("--year-start", type=int, default=2016)
    parser.add_argument("--variant", choices=["with", "without", "both"], default="with")
    parser.add_argument("--compare", action="store_true", help="alias for --variant both")
    args = parser.parse_args(argv)

    dsn = os.environ.get("TENNIS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("database URL unavailable")

    cutoff = datetime.now(timezone.utc)
    observations, read_only, load_seconds = _load_observations_read_only(
        dsn,
        cutoff=cutoff,
        year_start=args.year_start,
    )

    variant = "both" if args.compare else args.variant
    with_sportradar = (
        _run_variant(
            observations,
            mode=args.mode,
            evaluation_start=args.evaluation_start,
            evaluation_end=args.evaluation_end,
        )
        if variant in {"with", "both"}
        else None
    )
    without_sportradar = None
    if variant in {"without", "both"}:
        without_sportradar = _run_variant(
            _without_sportradar_stats(observations),
            mode=args.mode,
            evaluation_start=args.evaluation_start,
            evaluation_end=args.evaluation_end,
        )

    result = {
        "status": "OK",
        "label": "RETROSPECTIVE_RESEARCH" if args.mode == MODE_RETROSPECTIVE else "STRICT_POINT_IN_TIME",
        "mode": args.mode,
        "cutoff": cutoff.isoformat(),
        "transaction_read_only": read_only,
        "database_writes": 0,
        "load_seconds": load_seconds,
        "evaluation_start": args.evaluation_start.isoformat(),
        "evaluation_end": args.evaluation_end.isoformat(),
        "with_sportradar_stats": with_sportradar,
        "without_sportradar_stats": without_sportradar,
    }
    print("AGENT2_HISTORICAL_REPLAY " + json.dumps(result, sort_keys=True, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
