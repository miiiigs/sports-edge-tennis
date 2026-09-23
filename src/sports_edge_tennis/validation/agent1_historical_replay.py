from __future__ import annotations

"""Read-only Agent 1 v1 historical replay for the rehearsal evidence package.

Reuses the production Agent 1 surface-Elo walk-forward primitives
(``RunnerState`` / ``predict_from_state``) directly, so the model mathematics is
not re-implemented. A single read-only database transaction is rolled back and
disconnected before any model work. Two labelled modes:

* ``strict`` (MODE A): visibility exactly as stored.
* ``retrospective`` (MODE B): in-memory chronology reordering by match start.

Agent 1 is Elo/surface based and does not consume Sportradar serve statistics.
"""

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    DEFAULT_CONFIG as A1_CONFIG,
    HistoricalMatchObservation,
)
from sports_edge_tennis.data.agent1_history import load_agent1_history
from sports_edge_tennis.validation.agent1_walk_forward import (
    RunnerState,
    ValidationObservation,
    build_prediction_target,
    predict_from_state,
)
from sports_edge_tennis.validation.progress import heartbeat

MODE_STRICT = "strict"
MODE_RETROSPECTIVE = "retrospective"


def _load_observations_read_only(
    dsn: str,
    *,
    cutoff: datetime,
    year_start: int,
) -> tuple[list[HistoricalMatchObservation], str, float]:
    started = time.monotonic()
    with psycopg.connect(dsn, autocommit=False) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        read_only = str(conn.execute("SHOW transaction_read_only").fetchone()[0]).lower()
        if read_only != "on":
            raise RuntimeError("transaction_read_only is not on")
        observations = load_agent1_history(
            conn,
            cutoff=cutoff,
            tour_scopes=("ATP_MAIN", "CHALLENGER"),
            year_start=year_start,
            year_end=None,
        )
        conn.rollback()
    return observations, read_only, time.monotonic() - started


def _anchor(observation: HistoricalMatchObservation, mode: str) -> datetime:
    if mode == MODE_RETROSPECTIVE:
        return observation.match_start_at or max(
            observation.source_observed_at, observation.as_of
        )
    return max(observation.source_observed_at, observation.as_of)


def _to_validation(
    observation: HistoricalMatchObservation,
    *,
    anchor: datetime,
) -> ValidationObservation:
    season_year = (observation.match_start_at or observation.as_of).year
    return ValidationObservation(
        season_year=season_year,
        match_id=observation.match_id,
        player_1_id=observation.player_1_id,
        player_2_id=observation.player_2_id,
        surface=observation.surface,
        tour_scope=observation.tour_scope,
        competition_tier=observation.competition_tier,
        best_of_sets=observation.best_of_sets,
        qualifying_flag=observation.qualifying_flag,
        match_start_at=observation.match_start_at,
        outcome_status=observation.outcome_status,
        winner_side=observation.winner_side,
        source_observed_at=anchor,
        as_of=anchor,
    )


def _replay(
    observations: list[HistoricalMatchObservation],
    *,
    mode: str,
    prediction_filter=None,
) -> list[tuple[HistoricalMatchObservation, object]]:
    grouped: dict[datetime, list[HistoricalMatchObservation]] = defaultdict(list)
    for observation in observations:
        grouped[_anchor(observation, mode)].append(observation)

    state = RunnerState.empty()
    records: list[tuple[HistoricalMatchObservation, object]] = []
    for cohort_time in sorted(grouped):
        cohort = sorted(grouped[cohort_time], key=lambda item: item.match_id)
        to_predict = (
            cohort
            if prediction_filter is None
            else [item for item in cohort if prediction_filter(item)]
        )
        predictions: list[tuple[HistoricalMatchObservation, object]] = []
        for observation in to_predict:
            target = build_prediction_target(
                _to_validation(observation, anchor=cohort_time)
            )
            predictions.append((observation, predict_from_state(target, state, A1_CONFIG)))
        records.extend(predictions)
        for observation in cohort:
            state.apply_revealed_observation(
                _to_validation(observation, anchor=cohort_time), A1_CONFIG
            )
    return records


def _score(
    records,
    *,
    evaluation_start: date,
    evaluation_end: date,
) -> dict[str, object]:
    sample = predicted = abstained = correct = 0
    unknown_surface = 0
    cold_start = 0
    abstain_reasons: Counter[str] = Counter()
    for observation, result in records:
        match_date = (
            observation.match_start_at or observation.as_of
        ).date()
        if match_date < evaluation_start or match_date > evaluation_end:
            continue
        if observation.outcome_status != "COMPLETED" or observation.winner_side not in (1, 2):
            continue
        sample += 1
        if getattr(result.diagnostics, "unknown_surface", False):
            unknown_surface += 1
        if getattr(result.diagnostics, "player_1_cold_start", False) and getattr(
            result.diagnostics, "player_2_cold_start", False
        ):
            cold_start += 1
        if result.prediction_status != "PREDICTED":
            abstained += 1
            reasons = getattr(result.diagnostics, "abstain_reason", None)
            abstain_reasons[str(reasons)] += 1
            continue
        predicted += 1
        correct += 1 if result.selection_side == observation.winner_side else 0
    return {
        "sample": sample,
        "predicted": predicted,
        "abstained": abstained,
        "correct": correct,
        "incorrect": predicted - correct,
        "coverage": (predicted / sample) if sample else None,
        "accuracy": (correct / predicted) if predicted else None,
        "unknown_surface": unknown_surface,
        "both_players_cold_start": cold_start,
        "abstain_reasons": dict(sorted(abstain_reasons.items())),
    }


def run_agent1_replay(
    observations: list[HistoricalMatchObservation],
    *,
    mode: str,
    evaluation_start: date,
    evaluation_end: date,
) -> dict[str, object]:
    started = time.monotonic()
    with heartbeat(f"agent1_replay mode={mode} observations={len(observations)}"):
        records = _replay(
            observations,
            mode=mode,
            prediction_filter=lambda observation: (
                evaluation_start
                <= (observation.match_start_at or observation.as_of).date()
                <= evaluation_end
            ),
        )
    return {
        "observation_count": len(observations),
        "replayed_matches": len(records),
        "replay_seconds": time.monotonic() - started,
        "score": _score(
            records,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Agent 1 historical replay")
    parser.add_argument("--mode", choices=[MODE_STRICT, MODE_RETROSPECTIVE], default=MODE_RETROSPECTIVE)
    parser.add_argument("--evaluation-start", type=date.fromisoformat, default=date(2026, 9, 10))
    parser.add_argument("--evaluation-end", type=date.fromisoformat, default=date(2026, 9, 21))
    parser.add_argument("--year-start", type=int, default=2016)
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
    variant = run_agent1_replay(
        observations,
        mode=args.mode,
        evaluation_start=args.evaluation_start,
        evaluation_end=args.evaluation_end,
    )
    result = {
        "status": "OK",
        "agent": "AGENT_1_SURFACE_ELO_V1",
        "label": "RETROSPECTIVE_RESEARCH" if args.mode == MODE_RETROSPECTIVE else "STRICT_POINT_IN_TIME",
        "mode": args.mode,
        "cutoff": cutoff.isoformat(),
        "transaction_read_only": read_only,
        "database_writes": 0,
        "load_seconds": load_seconds,
        "evaluation_start": args.evaluation_start.isoformat(),
        "evaluation_end": args.evaluation_end.isoformat(),
        "result": variant,
    }
    print("AGENT1_HISTORICAL_REPLAY " + json.dumps(result, sort_keys=True, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
