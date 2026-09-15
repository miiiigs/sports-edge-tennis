"""Shadow-only recency experiments for Agent 2.

Production Agent 2 remains unchanged.  This module replays the exact frozen Agent 2 model
with a rolling point-in-time history window so cumulative-history V1 can be compared against
365/730/1460-day variants using the same outcomes and scoring rules.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    DEFAULT_CONFIG,
    Agent2Config,
    predict,
)
from sports_edge_tennis.data.agent2_history import (
    Agent2HistoricalMatch,
    HistoricalPredictionRecord,
    HistoricalReplayResult,
    _MutablePlayerAccumulator,
    _MutablePopulationAccumulator,
    _coherent_two_sided_rows,
    apply_historical_match_in_place,
    build_prediction_request_from_snapshot,
    historical_match_is_eligible_for_update,
    snapshot_history_state,
)

_EPS = 1e-15


@dataclass(frozen=True)
class Agent2RecencyMetrics:
    window_days: int | None
    sample: int
    predicted: int
    abstained: int
    correct: int
    accuracy: float | None
    brier_score: float | None
    log_loss: float | None


def _decrement(value: int, amount: int, *, field: str) -> int:
    result = value - amount
    if result < 0:
        raise ValueError(f"Agent 2 recency state underflow for {field}")
    return result


def _remove_historical_match_in_place(
    players: dict[int, _MutablePlayerAccumulator],
    population: _MutablePopulationAccumulator,
    observation: Agent2HistoricalMatch,
) -> bool:
    """Exact inverse of Agent 2 historical accumulation for one eligible match."""
    if not historical_match_is_eligible_for_update(observation):
        return False
    rows = _coherent_two_sided_rows(observation)
    if rows is None:  # pragma: no cover - eligibility guard already proves this
        return False
    player_1_row, player_2_row = rows
    p1_serve_won = player_1_row.service_points_won
    p2_serve_won = player_2_row.service_points_won
    if p1_serve_won is None or p2_serve_won is None:  # pragma: no cover - coherence guard
        return False
    p1_return_won = player_2_row.service_points_played - p2_serve_won
    p2_return_won = player_1_row.service_points_played - p1_serve_won

    p1 = players[observation.player_1_id]
    p2 = players[observation.player_2_id]
    for player in (p1, p2):
        player.prior_usable_matches = _decrement(
            player.prior_usable_matches, 1, field="prior_usable_matches"
        )

    p1.overall_serve_points_won = _decrement(
        p1.overall_serve_points_won, p1_serve_won, field="p1 overall serve won"
    )
    p1.overall_serve_points_played = _decrement(
        p1.overall_serve_points_played,
        player_1_row.service_points_played,
        field="p1 overall serve played",
    )
    p1.overall_return_points_won = _decrement(
        p1.overall_return_points_won, p1_return_won, field="p1 overall return won"
    )
    p1.overall_return_points_played = _decrement(
        p1.overall_return_points_played,
        player_2_row.service_points_played,
        field="p1 overall return played",
    )

    p2.overall_serve_points_won = _decrement(
        p2.overall_serve_points_won, p2_serve_won, field="p2 overall serve won"
    )
    p2.overall_serve_points_played = _decrement(
        p2.overall_serve_points_played,
        player_2_row.service_points_played,
        field="p2 overall serve played",
    )
    p2.overall_return_points_won = _decrement(
        p2.overall_return_points_won, p2_return_won, field="p2 overall return won"
    )
    p2.overall_return_points_played = _decrement(
        p2.overall_return_points_played,
        player_1_row.service_points_played,
        field="p2 overall return played",
    )

    p1_surface = p1.surface_points[observation.surface]
    p1_surface.serve_points_won = _decrement(
        p1_surface.serve_points_won, p1_serve_won, field="p1 surface serve won"
    )
    p1_surface.serve_points_played = _decrement(
        p1_surface.serve_points_played,
        player_1_row.service_points_played,
        field="p1 surface serve played",
    )
    p1_surface.return_points_won = _decrement(
        p1_surface.return_points_won, p1_return_won, field="p1 surface return won"
    )
    p1_surface.return_points_played = _decrement(
        p1_surface.return_points_played,
        player_2_row.service_points_played,
        field="p1 surface return played",
    )

    p2_surface = p2.surface_points[observation.surface]
    p2_surface.serve_points_won = _decrement(
        p2_surface.serve_points_won, p2_serve_won, field="p2 surface serve won"
    )
    p2_surface.serve_points_played = _decrement(
        p2_surface.serve_points_played,
        player_2_row.service_points_played,
        field="p2 surface serve played",
    )
    p2_surface.return_points_won = _decrement(
        p2_surface.return_points_won, p2_return_won, field="p2 surface return won"
    )
    p2_surface.return_points_played = _decrement(
        p2_surface.return_points_played,
        player_1_row.service_points_played,
        field="p2 surface return played",
    )

    surface_tour = population.surface_tour_service_points[(observation.surface, observation.tour_scope)]
    surface_tour[0] = _decrement(
        surface_tour[0], p1_serve_won + p2_serve_won, field="population surface-tour won"
    )
    surface_tour[1] = _decrement(
        surface_tour[1],
        player_1_row.service_points_played + player_2_row.service_points_played,
        field="population surface-tour played",
    )
    surface_total = population.surface_service_points[observation.surface]
    surface_total[0] = _decrement(
        surface_total[0], p1_serve_won + p2_serve_won, field="population surface won"
    )
    surface_total[1] = _decrement(
        surface_total[1],
        player_1_row.service_points_played + player_2_row.service_points_played,
        field="population surface played",
    )
    population.global_service_points_won = _decrement(
        population.global_service_points_won,
        p1_serve_won + p2_serve_won,
        field="population global won",
    )
    population.global_service_points_played = _decrement(
        population.global_service_points_played,
        player_1_row.service_points_played + player_2_row.service_points_played,
        field="population global played",
    )
    return True


def replay_historical_predictions_windowed(
    observations: list[Agent2HistoricalMatch] | tuple[Agent2HistoricalMatch, ...],
    *,
    window_days: int,
    config: Agent2Config = DEFAULT_CONFIG,
) -> HistoricalReplayResult:
    """Leakage-safe Agent 2 replay using only evidence inside a rolling time window."""
    if window_days <= 0:
        raise ValueError("window_days must be positive")

    ordered = sorted(
        observations,
        key=lambda item: (
            item.visibility_cohort,
            item.result_source_observed_at,
            item.match_id,
            item.player_1_id,
            item.player_2_id,
        ),
    )
    grouped: dict[datetime, list[Agent2HistoricalMatch]] = {}
    for observation in ordered:
        grouped.setdefault(observation.visibility_cohort, []).append(observation)

    players: dict[int, _MutablePlayerAccumulator] = {}
    population = _MutablePopulationAccumulator()
    active_history: deque[Agent2HistoricalMatch] = deque()
    records: list[HistoricalPredictionRecord] = []
    window = timedelta(days=window_days)

    for visibility_cohort in sorted(grouped):
        boundary = visibility_cohort - window
        while active_history and active_history[0].visibility_cohort < boundary:
            expired = active_history.popleft()
            _remove_historical_match_in_place(players, population, expired)

        frozen_snapshot = snapshot_history_state(players, population)
        cohort = sorted(
            grouped[visibility_cohort],
            key=lambda item: (
                item.result_as_of,
                item.result_source_observed_at,
                item.match_id,
                item.player_1_id,
                item.player_2_id,
            ),
        )
        for observation in cohort:
            request = build_prediction_request_from_snapshot(
                observation,
                frozen_snapshot,
                prediction_as_of=visibility_cohort,
            )
            records.append(
                HistoricalPredictionRecord(
                    observation=observation,
                    request=request,
                    result=predict(request, config),
                )
            )
        for observation in cohort:
            if apply_historical_match_in_place(players, population, observation):
                active_history.append(observation)

    return HistoricalReplayResult(
        records=tuple(records),
        final_snapshot=snapshot_history_state(players, population),
    )


def score_agent2_replay(
    replay: HistoricalReplayResult,
    *,
    window_days: int | None,
    evaluation_year_start: int | None = None,
) -> Agent2RecencyMetrics:
    """Score a replay on completed matches without mixing abstentions into accuracy."""
    sample = predicted = abstained = correct = 0
    squared_errors: list[float] = []
    log_losses: list[float] = []

    for record in replay.records:
        observation = record.observation
        if evaluation_year_start is not None and observation.season_year < evaluation_year_start:
            continue
        if observation.outcome_status != "COMPLETED" or observation.winner_side not in (1, 2):
            continue
        sample += 1
        result = record.result
        if result.prediction_status != "PREDICTED":
            abstained += 1
            continue
        predicted += 1
        selected_side = result.selection_side
        probability = float(result.win_probability)
        outcome = 1 if selected_side == observation.winner_side else 0
        correct += outcome
        squared_errors.append((probability - outcome) ** 2)
        bounded = max(_EPS, min(1.0 - _EPS, probability))
        log_losses.append(-(outcome * math.log(bounded) + (1 - outcome) * math.log(1 - bounded)))

    return Agent2RecencyMetrics(
        window_days=window_days,
        sample=sample,
        predicted=predicted,
        abstained=abstained,
        correct=correct,
        accuracy=(correct / predicted) if predicted else None,
        brier_score=(sum(squared_errors) / predicted) if predicted else None,
        log_loss=(sum(log_losses) / predicted) if predicted else None,
    )


__all__ = [
    "Agent2RecencyMetrics",
    "replay_historical_predictions_windowed",
    "score_agent2_replay",
]
