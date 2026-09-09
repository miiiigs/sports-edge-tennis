from __future__ import annotations

from sports_edge_tennis.data.agent2_history import (
    Agent2HistoricalMatch,
    Agent2HistorySnapshot,
    _MutablePlayerAccumulator,
    _MutablePopulationAccumulator,
    _MutableSurfaceAccumulator,
    apply_historical_match_in_place,
    snapshot_history_state,
)


def advance_agent2_history_snapshot(
    snapshot: Agent2HistorySnapshot,
    observations: list[Agent2HistoricalMatch] | tuple[Agent2HistoricalMatch, ...],
    *,
    cutoff,
) -> Agent2HistorySnapshot:
    """Apply only newly visible Agent 2 history to a persisted aggregate snapshot."""
    players: dict[int, _MutablePlayerAccumulator] = {}
    for player_id, player in snapshot.players.items():
        mutable = _MutablePlayerAccumulator(
            player_id=player.player_id,
            prior_usable_matches=player.prior_usable_matches,
            overall_serve_points_won=player.overall_serve_points_won,
            overall_serve_points_played=player.overall_serve_points_played,
            overall_return_points_won=player.overall_return_points_won,
            overall_return_points_played=player.overall_return_points_played,
        )
        mutable.surface_points = {
            surface: _MutableSurfaceAccumulator(
                serve_points_won=state.serve_points_won,
                serve_points_played=state.serve_points_played,
                return_points_won=state.return_points_won,
                return_points_played=state.return_points_played,
            )
            for surface, state in player.surface_points.items()
        }
        players[player_id] = mutable

    population = _MutablePopulationAccumulator(
        surface_tour_service_points={
            key: [value[0], value[1]]
            for key, value in snapshot.population.surface_tour_service_points.items()
        },
        surface_service_points={
            key: [value[0], value[1]]
            for key, value in snapshot.population.surface_service_points.items()
        },
        global_service_points_won=snapshot.population.global_service_points_won,
        global_service_points_played=snapshot.population.global_service_points_played,
    )

    for observation in sorted(
        observations,
        key=lambda item: (
            item.result_as_of,
            item.result_source_observed_at,
            item.match_id,
            item.player_1_id,
            item.player_2_id,
        ),
    ):
        if observation.result_source_observed_at >= cutoff or observation.result_as_of >= cutoff:
            continue
        apply_historical_match_in_place(players, population, observation)

    return snapshot_history_state(players, population)
